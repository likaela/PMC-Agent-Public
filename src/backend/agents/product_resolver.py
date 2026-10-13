"""Exact, permission-scoped product references. No fuzzy matching or model calls."""
import re


def resolve_product(message, catalog, *, current_product=None, catalog_order=None):
    from agents.reviewer_catalog_queries import catalog_kind
    rows = list(catalog)
    mode = "full" if re.search(r"完整|全部.*物料|使用完整企业", message) else "slim" if re.search(r"标准分析|精简", message) else None
    exact = {row["product_code"] for row in rows if re.search(
        r"(?<![A-Za-z0-9_-])" + re.escape(row["product_code"]) + r"(?![A-Za-z0-9_-])", message, re.I)}
    if catalog_kind(message) and not exact:
        return dict(status="ok", product_code=None, catalog_query=True, message=message, explicit=False)
    aliases = {}
    for row in rows:
        code = row["product_code"]
        names = [code, *(row.get("aliases") or [])]
        if code.startswith("KQ-"):
            names.append(code[3:])
        if row.get("product_name"):
            names.append(row["product_name"])
        if row.get("synthetic"):
            names.extend(["演示产品", "演示用产品", "Demo产品", "Demo"])
        for name in names:
            aliases.setdefault(name, set()).add(code)
    matches, spans = set(), []
    for alias, codes in aliases.items():
        if exact and alias.lower() not in {code.lower() for code in exact}:
            continue
        for hit in re.finditer(r"(?<![A-Za-z0-9_-])" + re.escape(alias) + r"(?![A-Za-z0-9_-])", message, re.I):
            matches.update(codes)
            spans.append((hit.start(), hit.end()))
    ordinal = re.search(r"第([一二三四五六七八九]|\d+)个产品", message)
    if ordinal and not matches:
        number = int(ordinal[1]) if ordinal[1].isdigit() else "一二三四五六七八九".index(ordinal[1]) + 1
        order = catalog_order or []  # Only an explicitly shown catalog defines ordinal references.
        allowed = {r["product_code"] for r in rows}
        if number <= len(order) and order[number-1] in allowed:
            matches.add(order[number-1])
            spans.append(ordinal.span())
        else:
            return dict(status="needs_input", product_code=None, catalog_query=False,
                answer="请先查看产品目录，或提供产品编码。", candidates=[])
    if len(matches) > 1:
        return dict(status="needs_input", product_code=None, catalog_query=False,
            answer="当前问题对应多个产品，请确认：" + "、".join(sorted(matches)) + "。", candidates=sorted(matches))
    if not matches:
        # Explicit inaccessible/nonexistent codes and Demo references never fall back to history.
        if re.search(r"(?<![A-Za-z0-9_-])(?:KQ-|DEMO-)[A-Za-z0-9_-]+|演示(?:用)?产品|(?<![A-Za-z0-9_-])Demo(?![A-Za-z0-9_-])", message, re.I):
            return dict(status="item_not_found", product_code=None, catalog_query=False,
                answer="未找到可访问的产品，请从当前产品目录选择。", candidates=[])
        normalized = message
        if current_product and re.search(r"BOM|库存|物料组成", message, re.I):
            from agents.business_language import identifier_message
            from agents.fact_query_agent import _explicit_material_code
            if _explicit_material_code(identifier_message(message)) is None:
                normalized = message + "，产品" + current_product
        active = next((r for r in rows if r["product_code"] == current_product), {})
        return dict(status="ok", product_code=current_product, catalog_query=False, message=normalized, explicit=False,
            analysis_mode=None if active.get("default_mode") == "current" else mode)
    product = next(iter(matches))
    normalized = message
    # Replace overlapping aliases only once; longest match wins.
    end = -1
    selected = []
    for start, stop in sorted(set(spans), key=lambda span: (span[0], -span[1])):
        if start >= end:
            selected.append((start, stop)); end = stop
    for start, stop in reversed(selected):
        normalized = normalized[:start] + product + normalized[stop:]
    mode = "full" if re.search(r"完整|全部.*物料|使用完整企业", message) else "slim" if re.search(r"标准分析|精简", message) else None
    row = next(r for r in rows if r["product_code"] == product)
    if row.get("default_mode") == "current":
        mode = None  # All current materials, never a switch into an archived scope.
    return dict(status="ok", product_code=product, catalog_query=False, message=normalized,
        explicit=True, analysis_mode=mode)


def product_overview_requested(message, product):
    return bool(re.fullmatch(r"\s*(?:(?:查询|查看|查一下|看看|看一下|切换到|换成|回到(?:之前的)?)\s*)?"
        + r"(?:那个|这个)?" + re.escape(product) + r"[。！？?\s]*", message, re.I))
