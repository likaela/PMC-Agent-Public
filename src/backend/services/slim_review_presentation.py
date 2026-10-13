"""Reviewer-facing allowlist. Original evidence remains in private storage."""
import copy


class SlimReviewPresentation:
    def __init__(self, dataset):
        self.dataset = dataset
        self.aliases = dataset.get("material_aliases") or {
            code: f"MAT-{i:03d}" for i, code in enumerate(sorted({
                line["child_item"]["material_code"] for line in dataset["bom"]["data"]["lines"]}), 1)}

    def text(self, value):
        for code, alias in sorted(self.aliases.items(), key=lambda pair: len(pair[0]), reverse=True):
            value = value.replace(code, alias)
        return value

    def result(self, result):
        data = result.get("data")
        public = {key: copy.deepcopy(result.get(key)) for key in ("status", "completeness")}
        public["query"] = {key: copy.deepcopy(value) for key, value in result.get("query", {}).items()
            if key in ("product", "revision", "requested_quantity", "quantity", "requested_unit")}
        public["warnings"] = ["结果仅适用于当前分析范围，不代表完整产品生产放行。"]
        if self.dataset["synthetic"]:
            public["warnings"].append("本次包含演示库存，仅用于数量模拟。")
        public["provenance"] = dict(filtered_dataset=dict(dataset_version=self.dataset["dataset_version"],
            retained_material_count=self.dataset["retained_material_count"],
            excluded_material_count=len(self.dataset["excluded"]), synthetic=self.dataset["synthetic"]),
            bom=dict(source_file="授权BOM结构记录", revision=self.dataset["revision"]),
            inventory=dict(source_file="授权主库存记录", source_kind="excel_observation", unit="片"))
        if self.dataset["synthetic"]:
            public["provenance"]["supplement"] = dict(source_file="演示库存补充", synthetic=True)
        if not data:
            public["data"] = None
            return public
        supplements = {r["material_code"]: r for r in self.dataset.get("supplements", [])}
        def material(row):
            code = row["material_code"]
            alias = self.aliases.get(code)
            if alias is None:
                if code not in self.aliases.values():
                    raise ValueError("Material outside approved aliases")
                alias = code
            values = {key: copy.deepcopy(row.get(key)) for key in (
                "qty_per", "required_qty", "recorded_stock_qty", "actual_stock_qty", "available_qty", "reserved_qty",
                "shortage_qty", "book_shortage_qty", "book_difference", "unit", "inventory_unit", "quantity_status",
                "comparison_status", "record_found", "duplicate_free", "max_supported_quantity")}
            private_code = next(code for code, mapped in self.aliases.items() if mapped == alias)
            supplement = supplements.get(private_code)
            stock_sources = [dict(source_file="授权主库存记录", source_kind="excel_observation", material_code=alias)]
            if supplement:
                values.update(original_stock_qty=supplement["original_stock_qty"], synthetic_added_qty=supplement["synthetic_added_qty"])
                stock_sources.append(dict(source_file="演示库存补充", synthetic=True, material_code=alias,
                    original_stock_qty=supplement["original_stock_qty"], synthetic_added_qty=supplement["synthetic_added_qty"]))
            return {**values, "material_code": alias, "name": f"物料{alias[4:]}",
                "bom_line_sources": [dict(source_file="授权BOM结构记录", material_code=alias)],
                "stock_sources": stock_sources, "details": []}
        values = {key: copy.deepcopy(data.get(key)) for key in (
            "calculation_scope", "product", "revision", "product_unit", "requested_quantity", "total_material_count",
            "matched_material_count", "comparable_material_count", "inventory_sufficient_for_requested",
            "theoretical_max_quantity", "max_producible_quantity", "formal_production_approved", "quantity_review",
            "quantity_check_status", "stock_read_failed", "slim_review", "scenario_name", "scenario_id",
            "scenario_product_code", "synthetic", "original_material_count", "excluded_material_count", "dataset_version")}
        values.update(materials=[material(row) for row in data["materials"]],
            book_shortages=[material(row) for row in data["book_shortages"]],
            limiting_materials=[material(row) for row in data["limiting_materials"]],
            no_record_materials=[], recommendations=[],
            source_summary=dict(source_file="授权主库存记录", sheet_name="已审核库存范围",
                unit_confirmation=dict(unit="片"), review_scenario=dict(filtered=True, synthetic=self.dataset["synthetic"])),
            deidentification="filtered_and_identifier_redacted_not_anonymous")
        public["data"] = values
        public["provenance"]["materials"] = [dict(material_code=r["material_code"], rows=r["stock_sources"])
            for r in values["materials"]]
        return public

    def response(self, response):
        value = copy.deepcopy(response)
        structured = value.get("structured_data") or {}
        if (structured.get("data") or {}).get("slim_review"):
            public = self.result(dict(status=value["status"], query=structured.get("query") or {},
                data=structured["data"], completeness=structured.get("completeness") or {}))
            structured.update(query=public["query"], data=public["data"], completeness=public["completeness"])
            value.update(provenance=public["provenance"], warnings=public["warnings"])
        value["answer"] = self.text(value["answer"])
        return value

    def session(self, session):
        # Session responses/history are public too, including fact_references.
        # Do not modify the privately stored evidence used for explanations.
        value = copy.deepcopy(session)
        context = value.get("context") or {}
        value["context"] = {key: copy.deepcopy(context[key]) for key in
            ("scenario_id", "active_domain", "production", "slim_analysis") if key in context}
        value["context"]["dataset_version"] = self.dataset["dataset_version"]
        for turn in value.get("turns", []):
            turn["user_query"] = self.text(turn["user_query"])
            turn["response"] = self.response(turn["response"])
            for ref in turn.get("fact_references", []):
                if not (ref["result"].get("data") or {}).get("slim_review"):
                    continue
                ref["result"] = self.result(ref["result"])
                ref["query"] = ref["result"]["query"]
                ref["provenance"] = ref["result"]["provenance"]
        return value
