import { QueryError, type AgentResult, type Source } from "../data/model";
import type { BackendView, InventoryOperationView } from "./backendTypes";
type Obj = Record<string, unknown>;
const obj = (v: unknown): Obj =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : {};
const list = (v: unknown): Obj[] => (Array.isArray(v) ? v.map(obj) : []);
const str = (v: unknown): string =>
  v == null
    ? "未提供"
    : typeof v === "boolean"
      ? v
        ? "是"
        : "否"
      : typeof v === "object"
        ? JSON.stringify(v)
        : String(v);
const text = (v: unknown) => (typeof v === "string" ? v : undefined);
const num = (v: unknown) =>
  typeof v === "number" && Number.isFinite(v) ? v : null;
const names: Record<string, string> = {
  catalog: "产品目录",
  product_overview: "产品概览",
  bom: "BOM 组成与单耗",
  inventory: "库存事实",
  production: "指定数量物料核对",
  maximum: "最大满足数量与核算范围",
  shortage: "缺料与限制说明",
  purchase: "采购建议",
  source: "来源与历史证据",
  inventory_preview: "库存领料预览",
  inventory_execution: "库存执行回执",
};
export function adaptBackendResponse(payload: unknown): AgentResult {
  const r = obj(payload);
  if (typeof r.answer !== "string" || typeof r.status !== "string")
    throw new QueryError(
      "mapping",
      "响应缺少 answer 或 status，原始结构无法映射。",
    );
  const s = obj(r.structured_data),
    reuse = obj(s.reused_result),
    d = obj(s.data ?? reuse.data),
    p = obj(s.presentation),
    query = obj(s.query),
    binding = obj(s.product_context);
  const fallback: Record<string, string> = {
    product_search: "catalog",
    production_capacity: "production",
    purchase_planning: "purchase",
  };
  const q = str(s.query_type ?? r.intent);
  const operation = text(s.operation)?.replace(/^public_/, "");
  const kind =
    operation === "inventory_preview" || operation === "inventory_execution"
      ? operation
      : text(p.result_type) ??
    (p.show_maximum === true ? "maximum" : (fallback[q] ?? q));
  const reused = Boolean(s.reused_from_turn_id || s.reused_result);
  // Only the backend's own tool trace is displayed; nothing is reconstructed here.
  const steps = list(reuse.completed_steps ?? s.completed_steps)
    .map((x) => ({
      operation: text(x.operation) ?? "",
      status: text(x.status) ?? "",
    }))
    .filter((x) => x.operation);
  const metrics: BackendView["metrics"] = [];
  const metric = (label: string, v: unknown, unit: unknown = "") => {
    if (v != null)
      metrics.push({ label, value: str(v) + (unit ? " " + str(unit) : "") });
  };
  const unit = d.product_unit ?? d.unit ?? obj(d.parent_item).unit;
  metric(
    "产品",
    d.product ?? obj(d.parent_item).material_code ?? binding.product_code,
  );
  metric("BOM 版本", d.revision);
  let rows: Obj[] = [],
    columns: BackendView["columns"] = [];
  let inventoryOperation: InventoryOperationView | undefined;
  const col = (key: string, label: string, numeric = false) => ({
    key,
    label,
    numeric,
  });
  const identity = [col("material_code", "物料编码"), col("name", "物料名称")];
  if (kind === "inventory_preview" || kind === "inventory_execution") {
    const strings = (value: unknown): string[] => Array.isArray(value)
      ? value.filter((entry): entry is string => typeof entry === "string") : [];
    const decimalText = (value: unknown): string | null =>
      typeof value === "string" && /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(value)
        ? value : typeof value === "number" && Number.isFinite(value) ? String(value) : null;
    const materials = list(d.materials);
    const allocations: Obj[] = kind === "inventory_execution" && Array.isArray(d.movements)
      ? list(d.movements) : Array.isArray(d.allocations) ? list(d.allocations)
      : materials.flatMap((material) => list(material.allocations).map((allocation): Obj => ({
          ...allocation, material_code: allocation.material_code ?? material.material_code,
        })));
    const isPreview = kind === "inventory_preview";
    inventoryOperation = {
      kind: isPreview ? "preview" : "execution",
      previewId: text(d.preview_id) ?? null,
      expiresAt: text(d.expires_at) ?? null,
      boundSessionId: text(d.session_id) ?? text(r.session_id) ?? null,
      canExecute: isPreview && ["ok", "confirmation_required"].includes(String(r.status)) && d.can_execute === true,
      executionAvailable: isPreview && d.execution_available === true,
      executed: !isPreview && r.status === "ok" && (d.executed === true || d.status === "executed"),
      blockers: [...new Set([...strings(d.blockers), ...strings(d.warnings)])],
      allocations: allocations.map((allocation) => ({
        materialCode: text(allocation.material_code) ?? null,
        batch: text(allocation.batch_no) ?? text(allocation.lot) ?? null,
        receivedAt: text(allocation.received_at) ?? text(allocation.batch_date) ?? null,
        quantity: decimalText(allocation.allocated_qty ?? allocation.quantity ?? allocation.deduct_qty),
        before: decimalText(allocation.available_before ?? allocation.before_qty),
        after: decimalText(allocation.available_after ?? allocation.after_qty),
      })),
    };
    rows = materials;
    columns = [...identity, col("required_qty", "需求", true),
      col("available_qty", "可用库存", true), col("shortage_qty", "缺口", true), col("unit", "单位")];
    metric("目标数量", d.requested_quantity ?? d.quantity, unit);
    metric("执行状态", isPreview ? "仅预览，尚未扣减"
      : inventoryOperation.executed ? "已执行" : "执行未确认");
  } else if (kind === "catalog") {
    rows = list(d.products).map((x) => ({
      ...x,
      data_kind: x.synthetic === true ? "后端演示产品" : "公开业务数据",
      revision: list(x.revisions)
        .map((z) => str(z.revision))
        .join(" / "),
    }));
    columns = [
      col("product_code", "产品编码"),
      col("product_name", "名称"),
      col("revision", "版本"),
      col("data_kind", "数据性质"),
    ];
  } else if (kind === "product_overview") {
    metric("物料项数", d.line_count);
    metric("分析范围", d.analysis_scope);
  } else if (kind === "bom") {
    rows = list(d.lines).map((x) => ({
      ...obj(x.child_item),
      ...x,
      name: obj(x.child_item).name,
      material_code: obj(x.child_item).material_code,
    }));
    columns = [...identity, col("qty_per", "单耗", true), col("unit", "单位")];
    metric("BOM 项数", d.line_count ?? d.total_material_count);
  } else if (
    ["inventory", "production", "shortage", "maximum"].includes(kind)
  ) {
    rows = list(
      kind === "maximum" && Array.isArray(d.limiting_materials)
        ? d.limiting_materials
        : d.materials,
    );
    if (!rows.length && d.canonical_item)
      rows = [{ ...obj(d.canonical_item), ...d }];
    rows = rows.map((x) => ({
      ...x,
      status:
        x.comparison_status === "no_stock" || x.record_found === false
          ? "库存未查询到"
          : x.quantity_status === "shortage" || x.is_ready === false
            ? "不满足需求"
            : x.quantity_status === "sufficient" || x.is_ready === true
              ? "满足本次需求"
              : "已有库存记录",
    }));
    columns = [...identity];
    if (kind === "production" || kind === "shortage") {
      columns.push(col("required_qty", "需求", true));
      metric("目标数量", d.requested_quantity, unit);
      // 结论只有“满足 / 存在缺口”两种；未给出生产数量时明确提示，不用“无法判断”兜底。
      metric(
        "需求满足",
        d.requested_quantity == null
          ? "未指定生产数量"
          : d.inventory_sufficient_for_requested === true
            ? "所列物料满足"
            : "存在缺口",
      );
    }
    columns.push(
      col("recorded_stock_qty", "账面记录数量", true),
      col("actual_stock_qty", "核验库存数量", true),
      // Already calculated by R19; never convert stock units in the frontend.
      col("stock_qty_in_bom_unit", "核对库存（物料单位）", true),
      col("available_qty", "可用库存", true),
      col("on_hand_qty", "在库数量", true),
      col("reserved_qty", "预留数量", true),
    );
    if (kind === "production" || kind === "shortage")
      columns.push(
        col("shortage_qty", "缺口", true),
        // 库存未查询到的物料按需求全额采购，缺口物料按缺口数采购。
        col("suggested_purchase_qty", "需要采购量", true),
      );
    if (kind === "maximum" && p.show_maximum !== false) {
      const subset = d.capacity_scope === "verified_subset";
      metric(
        subset ? "有库存记录物料产量上限" : "所列物料最大满足数量",
        subset ? d.verified_material_capacity : d.theoretical_max_quantity,
        unit,
      );
      // 库存口径二分后不再展示“已核算物料数”，改为点出查不到库存、需要采购的种数。
      metric("库存未查询到", d.missing_stock_material_count, "种");
      columns.push(
        col("capacity", "限制数量", true),
        col("material_capacity", "限制数量", true),
        col("max_supported_quantity", "限制数量", true),
        col("stock_qty", "核验库存", true),
      );
    }
    columns.push(col("unit", "物料单位"), col("status", "核对状态"));
    if (rows.some((row) => row.stock_unit != null))
      columns.push(col("stock_unit", "原库存单位"));
  } else if (kind === "purchase") {
    rows = list(d.recommendations).map((x) => ({
      ...x,
      material_code: x.material ?? x.material_code,
      minimum_order_quantity:
        x.minimum_order_quantity ??
        obj(x.purchase_parameters).minimum_order_quantity,
    }));
    columns = [
      col("material_code", "物料"),
      col("required_qty", "需求", true),
      col("available_qty", "可用库存", true),
      col("shortage_qty", "缺口", true),
      col("safety_stock_qty", "安全库存", true),
      col("suggested_purchase_qty", "建议采购", true),
      col("unit", "单位"),
      col("minimum_order_quantity", "MOQ", true),
      col("effective_order_multiple", "订单倍数", true),
      col("purchase_cost", "采购成本", true),
      col("currency", "币种"),
      col("lead_time_days", "交期（天）", true),
      col("supplier", "供应商"),
    ];
    metric("目标数量", d.quantity, unit ?? "（单位未提供）");
    metric("采购总成本", d.total_purchase_cost, d.total_cost_currency);
  }
  // Suppress entirely unknown numeric columns, while preserving nulls in mixed columns.
  columns = columns.filter(
    (c) => !c.numeric || rows.some((x) => x[c.key] != null),
  );
  const displayRows = rows.map((row) =>
    Object.fromEntries(
      columns.map((c) => [
        c.key,
        row[c.key] == null
          ? (row.record_found === false ||
              row.comparison_status === "no_stock") &&
            [
              "actual_stock_qty",
              "recorded_stock_qty",
              "available_qty",
              "shortage_qty",
            ].includes(c.key)
            ? "未查询到"
            : "未提供"
          : str(row[c.key]),
      ]),
    ),
  );
  const sources: Source[] = [];
  const provenance = r.provenance ?? reuse.provenance;
  const seen = new Set<string>();
  function collect(value: unknown, path: string) {
    if (Array.isArray(value)) {
      value.forEach((v, i) => collect(v, `${path}[${i}]`));
      return;
    }
    const v = obj(value);
    if (v.source_file || v.source_uri) {
      const signature = JSON.stringify([
        v.source_file,
        v.source_uri,
        v.sheet_name,
        v.row_number,
        v.dataset_version,
        v.bom_version,
        v.snapshot_at,
        v.observed_at,
        v.source_kind,
      ]);
      if (!seen.has(signature)) {
        seen.add(signature);
        sources.push({
          id: `api-source-${sources.length}`,
          title: text(v.source_file) ?? text(v.source_uri) ?? "接口来源",
          kind: "接口数据",
          scope: (reused ? "沿用历史证据 · " : "") + path,
          reference: [
            v.source_file ?? v.source_uri,
            v.sheet_name,
            v.row_number == null ? null : `第 ${str(v.row_number)} 行`,
          ]
            .filter((x) => x != null)
            .map(str)
            .join(" / "),
          recordId: text(v.source_file_id) ?? text(v.import_batch_id) ?? null,
          version: text(v.dataset_version) ?? text(v.bom_version) ?? null,
          publishedAt: text(v.snapshot_at) ?? text(v.observed_at) ?? null,
          conditions:
            text(v.reason) ??
            (reused
              ? "沿用历史证据，不是最新库存重查。"
              : "按该来源对应范围使用；不代表正式生产或采购指令。"),
        });
      }
    }
    for (const [k, nested] of Object.entries(v))
      if (nested && typeof nested === "object") collect(nested, `${path}.${k}`);
  }
  collect(provenance, "provenance");
  collect(
    d,
    s.data == null
      ? "structured_data.reused_result.data"
      : "structured_data.data",
  );
  const warnings = Array.isArray(p.user_warnings)
    ? p.user_warnings.filter((x): x is string => typeof x === "string")
    : Array.isArray(r.warnings)
      ? r.warnings.filter((x): x is string => typeof x === "string")
      : [];
  if (d.stock_read_failed === true)
    warnings.push("库存读取失败；已返回的 BOM 与物料需求仍保留。");
  if (reused) warnings.unshift("沿用本会话历史证据，不代表最新库存重查。");
  const status =
    r.status === "ok"
      ? "text"
      : r.status === "needs_input" || r.status === "invalid_query"
        ? "uncalculable"
        : r.status === "conflict"
          ? "conflict"
          : "no-facts";
  return {
    id: text(r.turn_id) ?? text(r.session_id) ?? "response",
    status,
    product:
      text(d.product) ??
      text(binding.product_code) ??
      text(query.material_code) ??
      null,
    quantity: ["production", "shortage"].includes(kind)
      ? num(d.requested_quantity)
      : null,
    quantityUnit: text(unit) ?? "",
    title: names[kind] ?? "后端回答",
    answer: r.answer,
    conclusion: r.answer,
    nextAction: "",
    materials: [],
    sources,
    warnings,
    backend: {
      resultType: kind,
      status: r.status,
      reused,
      sessionId: text(r.session_id),
      conversationId: text(r.conversation_id),
      turnId: text(r.turn_id),
      metrics,
      columns,
      rows: displayRows,
      steps,
      ...(inventoryOperation ? { inventoryOperation } : {}),
      raw: payload,
    },
  };
}
