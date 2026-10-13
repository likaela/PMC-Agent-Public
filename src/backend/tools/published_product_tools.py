"""Bound, read-only product discovery. Not part of the legacy Demo registry."""

from langchain.tools import tool
from repositories.published_product_repository import PublishedProductRepository


def build_published_product_search(store):
    repository = PublishedProductRepository(store)

    @tool
    def published_product_search(search_term: str | None = None, limit: int = 50) -> dict:
        """List published BOM parents, optionally filter literal code/name text; no capacity claims."""
        query = dict(search_term=search_term, limit=limit)
        envelope = dict(status="invalid_query", query=query, data=None, completeness={}, provenance=None, warnings=[])
        if (type(limit) is not int or not 1 <= limit <= 100 or
                (search_term is not None and (not search_term.strip() or len(search_term) > 256))):
            envelope["warnings"] = ["请提供有效产品名称关键词，查询条数需在允许范围内。"]
            return envelope
        try:
            rows, truncated = repository.search(search_term, limit)
            products = {}
            for row in rows:
                code = row["product_code"]
                product = products.setdefault(code, dict(product_code=code, product_name=row["product_name"],
                    published_bom_status="published", revisions=[]))
                product["revisions"].append(dict(revision=row["revision"],
                    effective_from=str(row["effective_from"]) if row["effective_from"] is not None else None,
                    effective_to=str(row["effective_to"]) if row["effective_to"] is not None else None))
            sources = [{key: str(row[key]) for key in
                ("product_code", "revision", "import_batch_id", "sheet_name", "source_file", "content_sha256")}
                for row in rows]
            for source, row in zip(sources, rows):
                if row["product_name"] is not None:
                    source["product_name_source"] = {key: row[key] for key in
                        ("name_import_batch_id", "name_source_file", "name_sha256", "name_sheet", "name_row")}
            return dict(status="ok" if rows else "data_unavailable", query=query,
                data=dict(products=list(products.values()), truncated=truncated) if rows else None,
                completeness=dict(published_bom=True, product_names=all(row["product_name"] is not None for row in rows) if rows else False,
                    complete_listing=not truncated, production_feasibility=False),
                provenance=dict(bom_sources=sources),
                warnings=["仅发现已发布BOM父件，可能包含中间组件；不代表库存齐套或允许开工。"] +
                         (["结果已截断，请使用产品名称或编码关键词缩小范围。"] if truncated else []))
        except Exception:
            import logging
            logging.getLogger(__name__).exception("产品发现查询失败")
            return {**envelope, "status": "query_failed", "warnings": ["产品目录暂不可用，请稍后重试。"]}

    return published_product_search


@tool
def published_product_search(search_term: str | None = None, limit: int = 50) -> dict:
    """Read published BOM parent codes/names only; not a feasible-production list."""
    from repositories.fact_store import create_fact_store
    return build_published_product_search(create_fact_store()).invoke(dict(search_term=search_term, limit=limit))
