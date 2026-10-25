"""PMC 扩展问答能力：缺料识别、独立物料查询、投产核算/采购BOM、FIFO扣减与库存统计。

设计原则（与既有架构一致）：
- 所有数量都来自确定性 Python 计算（复用 BookInventoryService.analyze / _stock），
  不由大模型生成，不补零、不猜测缺记录物料；
- 已发布事实文件只读，普通自然语言请求仅生成领料预览；
- 企业库存执行关闭；可选Demo执行须经过独立确认、可信权限和事务校验；
- 严格FIFO只使用可信入库日期，不从批次名称猜测执行顺序。
"""

from __future__ import annotations

import copy
import os
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from repositories.fact_store import PROJECT_ROOT
from repositories import inventory_adjustments
from services.book_inventory_service import BookInventoryService
from services import unit_system
from services.product_display_names import display_names


# ---------------------------------------------------------------- 意图识别

_DEDUCT = re.compile(
    r"扣减|扣除|扣掉|减去库存|减掉|出库|领料|核销|消耗库存|更新库存|库存更新|更新库存文件")
_PLAN = re.compile(
    r"投产|投料|备料|采购BOM|采购清单|采购表单|采购需求|采购建议|采购计划表|采购数量|采购多少|采购量|"
    r"需要采购的|生成采购|补货清单|采购单|买料")
_SHORTAGE = re.compile(
    r"急缺|缺料|缺哪些|哪些[^。？!?]{0,12}(?:缺|不足|不够|紧张)|缺口|不足[^。？!?]{0,6}(?:物料|材料|哪些)|"
    r"(?:比较|最|很|太|非常|有点)\s*(?:缺|紧张)|缺什么|少什么|还缺|差多少|缺多少|不够(?:的)?(?:物料|材料)")
_MATERIAL_LEVEL = re.compile(r"哪些|哪个|哪一|什么(?:物料|材料)|哪些物料")
# 全局库存口径统计：明确带库存/在库/全库语境的“多少种/多少类”提问。
_INV_KIND_SCOPED = re.compile(
    r"(?:库存|现存|现有|账面|在库|全库|总库存)[^。？!?]{0,16}(?:多少|几)\s*(?:种|类)|"
    r"(?:库存|现存|现有|在库|全库|总库存)[^。？!?]{0,8}(?:统计|概况|总览|种类|物料数)|"
    r"(?:多少|几)\s*种\s*(?:物料|元器件|料号|种类)[^。？!?]{0,6}(?:库存|在库)")
# 不带库存语境的裸问法（如“一共有多少种物料”），排除 BOM 类提问后才按库存口径处理。
_INV_KIND_BARE = re.compile(r"(?:多少|几)\s*种\s*(?:物料|元器件|料号|种类)")
# 兼容旧引用；实际判定以 _INV_KIND_SCOPED / _INV_KIND_BARE 为准。
_INV_KIND = _INV_KIND_SCOPED
# 会话绑定产品时 product_resolver 会把原句改写成「…，产品XXX」；判定全局库存口径前剥离该后缀。
_INJECTED_PRODUCT = re.compile(r"[，,]\s*产品\s*(?P<code>[A-Za-z0-9][A-Za-z0-9_-]*)\s*$")
_BOM_COUNTS = re.compile(
    r"BOM[^。？!?]{0,18}(?:多少|几)\s*(?:种|类)|(?:支持的|几个|哪些|所有|各)[^。？!?]{0,14}BOM|"
    r"(?:元器件|物料)种类", re.IGNORECASE)
_INV_ASK = re.compile(
    r"库存|在库|现存|还有多少|还剩|余量|存量|批次|哪个批次|哪批|库位|FIFO|先进先出|明细|有多少|多少|"
    r"个数|数量|几片|多少片|几件|多少件|几只|多少只|结存|库存量|账面量|账面数量")
_BOM_ASK = re.compile(r"BOM|物料清单|物料组成", re.IGNORECASE)
# 库存记录条数：问“记录多少条”，不是问“多少种物料”。两者口径不同，必须分别路由。
_INV_RECORD_COUNT = re.compile(
    r"(?:库存|库存表|主库存|账面|全库)[^。？!?]{0,10}(?:多少|几)\s*(?:条|行)\s*(?:记录|库存记录)?|"
    r"(?:库存|库存表)?\s*记录\s*(?:条数|总数|数量|共有多少)|多少条库存记录")
# 零库存：数量恰为 0，区别于“没有库存记录”和“库存未知”。
_ZERO_STOCK = re.compile(
    r"零库存|库存(?:数量)?\s*(?:为|是|=)\s*(?:0|零)|(?:0|零)\s*库存|"
    r"(?:没有|无)\s*库存(?:的)?(?:物料|料号|项目)")
# 重复观测：统计存在重复记录的料号数与带标记的记录行数，不合并为可信库存。
_DUPLICATE_OBS = re.compile(r"重复(?:的)?(?:库存)?(?:观测|记录)|库存记录(?:重复|有重复)|重复料号|哪些物料[^。？!?]{0,8}重复")
# BOM 行数：只回答条数，不展开明细；“多少种物料”仍走既有元器件种类统计。
_BOM_ROWS = re.compile(
    r"(?:多少|几)\s*(?:行|条|项)\s*(?:物料|BOM|记录)|BOM\s*(?:一共|共)?\s*(?:有)?\s*(?:多少|几)\s*(?:行|条|项)|"
    r"物料记录\s*(?:共|有)?\s*(?:多少|几)", re.IGNORECASE)
_FOLLOWUP_MATERIAL = re.compile(
    r"(?:它|该物料|刚才(?:那个)?|这个物料|那个物料|此物料|接着|继续)|"
    r"^\s*(?:(?:它|该物料|刚才(?:那个)?|这个物料)?(?:的)?)?(?:批次|明细|库存|还有多少|还剩多少|FIFO|先进先出|库位)[\s。？！?！]*$")
_PRODUCTION_QUANTITY = re.compile(r"(?:投产|投料|备料|生产|安排生产|计划生产|做|数量(?:为|是|：|:)?)\s*"
                                  r"(\d+|[零〇一二三四五六七八九十百千万两]+)")
_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9][A-Za-z0-9_-]{2,}(?![A-Za-z0-9])")
_RESERVED_TOKENS = {"BOM", "FIFO", "MRP", "PMC", "STOCK", "BATCH", "INBOUND", "PCS"}
_REVISION = re.compile(r"(?<![A-Za-z0-9])V\d+(?![A-Za-z0-9])", re.IGNORECASE)
_DATE_TOKEN = re.compile(r"\d{4}-\d{2}-\d{2}")


# ---------------------------------------------------------------- 批次先进先出

def batch_fifo_key(batch_no) -> tuple:
    """把批次号解析为近似日期；解析不出的排在最后（稳定按批次号排序）。

    兼容常见批次号形态：YYYYMMDD、YYMMDD、YYYYMM、YYWW(周)、'25+'、'2047+'。
    """
    text = str(batch_no or "").strip()
    if re.fullmatch(r"[12]\d{3}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])", text):
        try:
            return (datetime(int(text[:4]), int(text[4:6]), int(text[6:8])), 0, text)
        except ValueError:
            pass
    if re.fullmatch(r"\d{6}", text):
        yy, mm, dd = int(text[:2]), int(text[2:4]), int(text[4:6])
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            try:
                return (datetime(2000 + yy, mm, dd), 0, text)
            except ValueError:
                pass
        if text.startswith(("19", "20", "21")) and 1 <= mm <= 12:
            return (datetime(int(text[:4]), mm, 1), 1, text)
    if re.fullmatch(r"\d{4}", text):
        yy, ww = int(text[:2]), int(text[2:])
        if 1 <= ww <= 53:
            return (datetime(2000 + yy, 1, 1) + timedelta(weeks=ww - 1), 1, text)
    matched = re.fullmatch(r"(\d{2})\d{0,2}\+?", text)
    if matched:
        return (datetime(2000 + int(matched.group(1)), 1, 1), 1, text)
    return (datetime.max, 2, text)


def sort_fifo(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda row: batch_fifo_key(row.get("batch_no")))


# ---------------------------------------------------------------- 数据读取（含扣减流水）

def _public_root() -> Path:
    root = os.getenv("PMC_PUBLIC_DATA_ROOT")
    return Path(root).expanduser().resolve() if root else PROJECT_ROOT / "data/public"


_MASTER_RECORDS_CACHE: dict = {}


def load_master_records() -> dict:
    """主库存表账面记录（叠加运行时扣减流水后的数量）。

    全量库存表有数千条记录，按 (路径, mtime, 大小) 缓存解析结果，避免每次提问
    重复解析整份 JSON；扣减流水仍在返回前实时叠加。
    """
    import json
    path = _public_root() / "inventory/observations.json"
    if not path.is_file():
        return {}
    try:
        stat = path.stat()
        cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
        records = _MASTER_RECORDS_CACHE.get("records") if _MASTER_RECORDS_CACHE.get("key") == cache_key else None
        if records is None:
            data = json.loads(path.read_text(encoding="utf-8"))
            records = (data.get("observations") or {}).get("records") or {}
            _MASTER_RECORDS_CACHE.update(key=cache_key, records=records)
    except (OSError, ValueError):
        return {}
    from services.safe_inventory_runtime import overlay_observations
    return overlay_observations({"records": records})["records"]


def load_product_inventory(product_code: str | None) -> dict:
    """产品配套库存文件记录（同样叠加扣减流水）。"""
    import json
    if not product_code:
        return {}
    path = _public_root() / "products" / str(product_code).lower() / "inventory.json"
    if not path.is_file():
        return {}
    try:
        records = json.loads(path.read_text(encoding="utf-8")).get("records") or {}
    except (OSError, ValueError):
        return {}
    from services.safe_inventory_runtime import overlay_observations
    return overlay_observations({"records": records})["records"]


def load_alias_table() -> dict[str, str]:
    """全部物料别名（含 MAT-XXX 展示别名）→ 标准料号。"""
    import json
    table: dict[str, str] = {}
    root = _public_root() / "products"
    for path in sorted(root.glob("*/aliases.json")):
        try:
            mapping = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for real, alias in mapping.items():
            table.setdefault(str(real), str(real))
            table.setdefault(str(alias), str(real))
    return table


# ---------------------------------------------------------------- 工具函数

def _decimal(value) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _num(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _parse_quantity(message: str) -> int | None:
    from services.production_request_context import parse_quantity
    parsed = parse_quantity(message)
    return parsed.quantity if parsed.status == "valid" else None


def _context_quantity(context: dict, product=None) -> int | None:
    from services.production_request_context import inherit_quantity, selected_product
    return inherit_quantity(context, product or selected_product(context))


def _scope_product(context: dict) -> str | None:
    binding = context.get("product_context") or {}
    if isinstance(binding.get("product_code"), str) and binding["product_code"]:
        return binding["product_code"]
    for domain in ("production", "fact"):
        task = context.get(domain) or {}
        code = task.get("material_code")
        if isinstance(code, str) and code.upper().startswith(("KQ-", "DEMO-")):
            return code
    return None


def _catalog_rows() -> list[dict]:
    try:
        from services.local_reviewer_catalog import local_review_catalog
        from repositories.fact_store import create_fact_store
        return local_review_catalog(create_fact_store())
    except Exception:
        return []


def _match_product(message: str, rows: list[dict]) -> str | None:
    """产品编码/名称/简称联想（Z112→KQ-Z112，3588→KQ-BHX3588M）。"""
    aliases: dict[str, str] = {}
    for row in rows:
        code = row.get("product_code")
        if not code:
            continue
        names = [code, *(row.get("aliases") or [])]
        if code.startswith("KQ-"):
            names.append(code[3:])
        if row.get("product_name"):
            names.append(row["product_name"])
        if row.get("synthetic"):
            names.extend(["演示产品", "演示用例", "演示用产品", "Demo产品", "Demo"])
        for name in names:
            if isinstance(name, str) and name.strip():
                aliases.setdefault(name.strip(), code)
    matches = set()
    for name in sorted(aliases, key=len, reverse=True):
        if re.search(r"(?<![A-Za-z0-9_-])" + re.escape(name) + r"(?![A-Za-z0-9_-])", message, re.I):
            matches.add(aliases[name])
    return next(iter(matches)) if len(matches) == 1 else None


def scrub_injected_product(message: str) -> str:
    """剥离 product_resolver 追加的「，产品XXX」后缀，恢复用户原始提问。

    会话已绑定产品时该层会把泛指提问改写成产品限定提问，导致“库存一共多少种物料”
    被误判为该产品的 BOM 概览。统计类提问需要基于用户原文判定口径。
    """
    return _INJECTED_PRODUCT.sub("", message or "").strip()


def _material_tokens(message: str) -> list[str]:
    tokens = []
    for token in _TOKEN.findall(message):
        upper = token.upper()
        if upper in _RESERVED_TOKENS or _REVISION.fullmatch(token) or _DATE_TOKEN.fullmatch(token):
            continue
        if token.isdigit() and len(token) < 8:
            continue
        tokens.append(token)
    return tokens


def _resolve_material(token: str) -> dict | None:
    """独立物料查询的编码解析：标准料号或任一产品展示别名。"""
    alias_table = load_alias_table()
    canonical = alias_table.get(token, token)
    records = load_master_records().get(canonical)
    name = next((r.get("name") for r in records if r.get("name")), None) if records else None
    if not records:
        for code in ("kq-z112",):
            product_records = load_product_inventory(code).get(canonical)
            if product_records:
                records = product_records
                name = next((r.get("name") for r in product_records if r.get("name")), name)
                break
    aliases = sorted({alias for alias, real in alias_table.items() if real == canonical and alias != canonical})
    if not records and not aliases:
        return None
    return {"material_code": canonical, "name": name, "aliases": aliases, "records": records or []}


def _materials_by_name(message: str) -> dict | None:
    """按物料名称（中文器件名）反查料号；同一名称可对应多个料号（不猜测、不合并）。"""
    text = message or ""
    hits: dict[str, dict[str, list]] = {}
    for code, rows in load_master_records().items():
        for row in rows:
            name = str(row.get("name") or "").strip()
            chinese = bool(re.search(r"[\u4e00-\u9fff]", name))
            if len(name) < 2 or not (chinese or len(name) >= 4):
                continue
            if name in text:
                hits.setdefault(name, {}).setdefault(code, rows)
    if not hits:
        return None
    name = max(hits, key=len)  # 长名优先，避免短名（如“MOS”）抢先命中
    return dict(name=name, materials=[dict(material_code=code, name=name, records=rows)
                                      for code, rows in sorted(hits[name].items())])


def _product_file_note(entry: dict) -> str:
    """产品配套库存文件相对主库存表的口径说明。"""
    base = f"{entry['source']}记录 {entry['material_count']} 种（{entry['record_count']} 条记录）"
    extra = entry.get("exclusive_material_count") or 0
    if extra:
        return f"{base}，其中 {extra} 种不在主库存表内，已一并计入去重总数"
    return f"{base}，这些料号均已包含在上列全库存口径之内"


def _display_factory(service):
    presentation = getattr(service, "presentation", None)
    aliases = dict(getattr(presentation, "aliases", None) or {})

    def display(code):
        return aliases.get(code, code) if code else code

    return display


def _product_label(product: str | None, rows: list[dict]) -> str:
    entry = next((r for r in rows if r.get("product_code") == product), None) or {}
    name = entry.get("product_name") or display_names(product).get("product_name")
    return f"{product}（{name}）" if name else (product or "当前产品")


def _max_supported(row: dict) -> int | None:
    per = _decimal(row.get("qty_per"))
    stock = _decimal(row.get("recorded_stock_qty"))
    if per is None or stock is None or per <= 0:
        return None
    bom_unit = row.get("unit")
    stock_unit = row.get("stock_unit") or row.get("inventory_unit")
    if bom_unit and not stock_unit:
        return None  # 库存单位不可核验时不给产量上限
    if stock_unit and bom_unit and stock_unit != bom_unit:
        converted = unit_system.convert(stock, stock_unit, bom_unit)
        if converted is None:
            return None  # 跨维度或计数不等价单位不做上限推算
        stock = converted
    return int(stock // per)


def _references(tool_name: str, arguments: dict, result: dict) -> list[dict]:
    try:
        from agents.evidence import EvidenceCollector
        return [EvidenceCollector().collect(tool_name, arguments, result).reference()]
    except Exception:
        return []


class PmcCapabilityQueries:
    """挂在 PersistentAgentService.query 的最前置受控问答层。"""

    def __init__(self, service):
        self.service = service

    # ------------------------------------------------------------- 入口
    def run(self, message: str, context: dict, history_loader=None, *, session=None):
        text = (message or "").strip()
        if not text:
            return None
        if re.search(r"邮件|写信", text):
            return None  # Existing evidence-grounded draft path, not a new purchase demand.
        rows = _catalog_rows()
        scope_product = _scope_product(context)
        # “库存一共多少种物料”是全局库存口径提问；product_resolver 会在会话绑定产品时
        # 改写原文（追加「，产品XXX」），此处先剥离再判定，避免被当成该产品的 BOM 概览。
        clean = scrub_injected_product(message)
        explicit_product = _match_product(clean, rows)
        product = explicit_product or scope_product
        from services.production_request_context import parse_quantity
        request = parse_quantity(clean)
        if request.status in {"invalid", "ambiguous"}:
            return self._turn("production_capacity", "invalid_query", "请提供一个明确的大于0的整数生产数量。",
                query_type="production_capacity", query={}, data=None, completeness={}, context=context)
        explicit_quantity = request.quantity
        quantity = explicit_quantity if request.status == "valid" else _context_quantity(context, product)
        has_book = getattr(self.service, "book_service", None) is not None

        inventory_kind = _INV_KIND_SCOPED.search(clean) or (
            _INV_KIND_BARE.search(clean) and not _BOM_COUNTS.search(clean))
        if inventory_kind:
            return self._inventory_scope_count(clean, context, rows, explicit_product)
        # 统计口径分流：记录条数 / 零库存 / 重复观测 必须各自回答，不能都退回 BOM 概况。
        if _INV_RECORD_COUNT.search(clean):
            return self._inventory_record_count(clean, context)
        if _ZERO_STOCK.search(clean):
            return self._zero_stock(clean, context, rows, product)
        if _DUPLICATE_OBS.search(clean):
            return self._duplicate_observations(clean, context)
        if _BOM_ROWS.search(clean) and re.search(r"多少|几", clean):
            counted_product = explicit_product or _match_product(clean, rows)
            if counted_product:
                return self._bom_row_count(clean, context, counted_product)
        if _BOM_COUNTS.search(clean) and re.search(r"多少|几", clean):
            return self._bom_counts(clean, context, rows)
        if _DEDUCT.search(clean):
            bound_scenario = getattr(getattr(self.service, "repository", None), "manifest", None)
            return self._deduct(clean, context, rows, product, quantity, session={**session, "context": context} if session else None) if has_book or bound_scenario is not None else None
        if _PLAN.search(clean) and quantity:
            return self._production_plan(clean, context, rows, product, quantity) if has_book else None
        if _SHORTAGE.search(clean):
            material = None
            if not _MATERIAL_LEVEL.search(clean):
                for token in _material_tokens(clean):
                    resolved = _resolve_material(token)
                    if resolved:
                        material = resolved
                        break
            if material is not None:
                return self._material_coverage(clean, context, rows, material, _parse_quantity(clean))
            if has_book:
                return self._shortage(clean, context, rows, product, explicit_quantity)
            if not product:
                listing = "；".join(f"{r.get('product_code')}"
                                    f"{'（' + r['product_name'] + '）' if r.get('product_name') else ''}"
                                    for r in rows)
                return self._turn("clarification", "needs_input",
                                  f"请指明要分析哪个产品的缺料情况，当前可查询产品：{listing}。",
                                  query_type="clarification", query={"question": clean}, data=None,
                                  completeness={}, context=context)
            return None  # 演示场景保留原有齐套分析流程
        return self._standalone_material(clean, context, rows)

    def _turn(self, intent, status, answer, *, query_type, query, data, completeness,
              provenance=None, warnings=None, context=None, references=None, extra=None):
        from agents.orchestrator import OrchestratedTurn
        structured = dict(query_type=query_type, query=query, data=data, completeness=completeness,
                          response_source="deterministic", completed_steps=[])
        if extra:
            structured.update(extra)
        response = dict(intent=intent, status=status, answer=answer, structured_data=structured,
                        provenance=provenance, warnings=list(warnings or []))
        return OrchestratedTurn(response, copy.deepcopy(context) if context is not None else {}, references or [])

    def _analysis(self, product: str | None, quantity: int | None, requested_unit: str | None = None):
        book = getattr(self.service, "book_service", None)
        if book is None or not product:
            return None
        try:
            return book.analyze(product, quantity=quantity, requested_unit=requested_unit)
        except Exception:
            return None

    @staticmethod
    def _unverified_reason(row):
        """不可核算物料的中文原因；用于回答、汇总与采购清单保持一致。"""
        status = row.get("comparison_status")
        detail = row.get("uncomputable_reason")
        if status == "no_stock":
            return "库存无记录；保留BOM需求，未生成确定采购数量（无记录不等于零库存）"
        if status == "duplicate_unverified":
            return f"存在未核验的重复库存记录（{detail or '未取最大值、未求和'}）；未生成确定缺口与采购数量"
        if status == "unit_conflict":
            return f"计量单位冲突：{detail or '需求单位与库存单位不可换算'}；未生成确定缺口与采购数量"
        if status == "unit_unknown":
            return f"库存计量单位无法核验：{detail or '缺少可核验单位'}；未生成确定缺口与采购数量"
        if status == "inventory_read_failed":
            return "库存文件本次读取失败；未生成确定缺口与采购数量"
        return f"库存数量无法核验：{detail or '缺少可核验数量'}；未生成确定缺口与采购数量"

    @staticmethod
    def _remember(context, **values):
        saved = copy.deepcopy(context)
        capability = dict(saved.get("pmc_capability") or {})
        capability.update({key: value for key, value in values.items() if value is not None})
        saved["pmc_capability"] = capability
        return saved

    # ------------------------------------------------------------- 4-1 库存种类统计
    def _inventory_scope_count(self, text, context, rows, product):
        """库存物料种类统计：未指定产品时统计整个库存表，指定产品时按该产品库存口径统计。"""
        if product:
            counted = self._product_inventory_count(text, context, rows, product)
            if counted is not None:
                return counted
        return self._global_inventory_count(text, context, rows)

    def _global_inventory_count(self, text, context, rows):
        """整个库存表（主库存表）按料号去重的物料种类数。"""
        master = load_master_records()
        material_count = len(master)
        record_count = sum(len(v) for v in master.values())
        batches = {str(r.get("batch_no")) for v in master.values() for r in v if r.get("batch_no")}
        locations = {str(r.get("location")) for v in master.values() for r in v if r.get("location")}
        product_files = []
        for row in rows:
            records = load_product_inventory(row.get("product_code"))
            if not records:
                continue
            extra = set(records) - set(master)
            product_files.append(dict(product=row.get("product_code"), source=f"{row.get('product_code')} 配套库存文件",
                                      material_count=len(records),
                                      record_count=sum(len(v) for v in records.values()),
                                      exclusive_material_count=len(extra)))
        deductions = len(inventory_adjustments.load_deductions())
        answer = (f"整个库存表（主库存表已核验账面记录）按料号去重共 {material_count} 种物料"
                  f"（{record_count} 条库存记录、{len(batches)} 个批次、{len(locations)} 个库位）。"
                  f" 这是全库存口径，不限于任何单一产品的 BOM。")
        if product_files:
            answer += " 其中" + "；".join(_product_file_note(p) for p in product_files) + "。"
        if deductions:
            answer += f" 已叠加 {deductions} 次投产扣减流水后的账面数量。"
        answer += " 口径说明：只统计已核验账面记录，缺记录的物料不补零，也不按主库存表以外的来源补数。"
        data = dict(inventory_kind_count=material_count, record_count=record_count, batch_count=len(batches),
                    location_count=len(locations), sources=[dict(source="主库存表（已核验账面记录）",
                    material_count=material_count, record_count=record_count)],
                    product_inventories=product_files, deduction_count=deductions,
                    inventory_scope="entire_inventory_table", calculation_scope="inventory_kind_statistics")
        return self._turn("fact_query", "ok", answer, query_type="fact_query",
                          query={"question": text}, data=data,
                          completeness={"book_observation": True}, context=context)

    def _product_inventory_count(self, text, context, rows, product):
        """某一产品的库存物料种类数（配套库存文件优先，其次 BOM 在主库存表的匹配项）。"""
        records = load_product_inventory(product)
        master = load_master_records()
        if records:
            count, record_count = len(records), sum(len(v) for v in records.values())
            source = f"{product} 配套库存文件"
        else:
            analysis = self._analysis(product, None)
            data = (analysis or {}).get("data") or {}
            count, record_count = data.get("matched_material_count"), None
            source = f"{product} 的 BOM 物料在主库存表中的匹配记录"
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return None
        label = _product_label(product, rows)
        record_hint = f"，{record_count} 条库存记录" if record_count is not None else ""
        answer = (f"{label}的库存口径共 {count} 种物料（来源：{source}{record_hint}）。"
                  f" 整个库存表按料号去重共 {len(master)} 种物料；"
                  f"如需全库存口径，请直接问“库存一共多少种物料”。"
                  f" 口径说明：只统计已核验账面记录，缺记录的物料不补零。")
        data = dict(inventory_kind_count=count, global_inventory_kind_count=len(master),
                    record_count=record_count, source=source, product=product,
                    inventory_scope="product_inventory", calculation_scope="inventory_kind_statistics")
        return self._turn("fact_query", "ok", answer, query_type="fact_query",
                          query={"question": text}, data=data,
                          completeness={"book_observation": True}, context=context)

    # ------------------------------------------------------------- 4-2 各BOM元器件种类
    def _bom_counts(self, text, context, rows):
        products, parts = [], []
        for row in rows:
            count = row.get("line_count") or next(
                (m.get("line_count") for m in row.get("modes") or [] if m.get("line_count") is not None), None)
            products.append(dict(product_code=row.get("product_code"), product_name=row.get("product_name"),
                                 published_bom_status="synthetic" if row.get("synthetic") else "published",
                                 synthetic=bool(row.get("synthetic")),
                                 revisions=[dict(revision=row.get("revision"))], bom_line_count=count))
            name = row.get("product_name") or ""
            parts.append(f"{row.get('product_code')}{'（' + name + '）' if name else ''}："
                         f"BOM 版本 {row.get('revision')}，共 {count if count is not None else '未知'} 种元器件")
        answer = "当前支持的 BOM 及元器件种类：" + "；".join(parts) + "。"
        if re.search(r"库存", text):
            answer += f" 另：当前库存账面记录共 {len(load_master_records())} 种物料。"
        data = dict(products=products, calculation_scope="bom_component_statistics")
        return self._turn("fact_query", "ok", answer, query_type="product_search",
                          query={"question": text}, data=data,
                          completeness={"published_bom": True}, context=context)

    # ------------------------------------------------------------- 4-3 分项统计：BOM 行数 / 库存记录 / 零库存 / 重复观测
    def _bom_row_count(self, text, context, product):
        """只回答某产品已发布 BOM 的条数，不展开 91 条明细。"""
        dataset = getattr(self.service, "dataset", None) or getattr(
            getattr(self.service, "book_service", None), "dataset", None) or {}
        bom_data = ((dataset.get("bom") or {}).get("data") or {})
        revision = dataset.get("revision")
        lines = bom_data.get("lines") or []
        count = len(lines) if lines else None
        if count is None:
            facts = (getattr(self.service, "facts", None)
                     or getattr(getattr(self.service, "book_service", None), "facts", None))
            try:
                versions = facts.repository.list_published_bom_versions(product)
                revision = revision or (versions[0].get("version") if versions else None)
                if revision:
                    data = (facts.query_bom(product, revision) or {}).get("data") or {}
                    count = data.get("line_count")
            except Exception:
                count = None
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            return self._turn("bom", "data_unavailable", f"暂未取得 {product} 的已发布 BOM 行数，请稍后重试。",
                query_type="bom", query=dict(product=product, revision=revision), data=None,
                completeness={}, context=context)
        answer = (f"{product} 当前发布的 BOM 共 {count} 条物料记录"
                  f"（版本 {revision}）。本次只回答问题中的行数；如需逐条明细，可再说“查看 BOM 清单”。")
        payload = dict(product=product, revision=revision, total_material_count=count,
                       line_count=count, bom_line_count=count, calculation_scope="bom_row_count")
        return self._turn("bom", "ok", answer, query_type="bom",
            query=dict(product=product, revision=revision), data=payload,
            completeness={"published_bom": True}, context=context)

    def _inventory_record_count(self, text, context):
        """全库存表的记录条数；明确区分“记录数”和“不同料号数”。"""
        master = load_master_records()
        if not master:
            return self._turn("fact_query", "data_unavailable",
                "库存记录暂不可读取，请稍后重试。", query_type="fact_query",
                query={"question": text}, data=None, completeness={}, context=context)
        record_count = sum(len(v) for v in master.values())
        material_count = len(master)
        answer = (f"当前库存表共有 {record_count} 条库存记录。记录条数不等于不同料号数量："
                  f"本表按料号去重为 {material_count} 种物料。"
                  f" 统计范围是全库存表，不是某个产品的 BOM 记录数。")
        data = dict(record_count=record_count, inventory_kind_count=material_count,
                    inventory_scope="entire_inventory_table",
                    calculation_scope="inventory_record_statistics")
        return self._turn("fact_query", "ok", answer, query_type="fact_query",
                          query={"question": text}, data=data,
                          completeness={"book_observation": True}, context=context)

    def _zero_stock(self, text, context, rows, product):
        """真实零库存查询：只统计“有记录且数量恰为 0”，缺记录和未知不算零。"""
        target = product or _scope_product(context)
        if target:
            analysis = self._analysis(target, None) or {}
            materials = (analysis.get("data") or {}).get("materials") or []
            if not materials:
                return self._turn("inventory", "data_unavailable",
                    f"{target} 的 BOM 物料库存暂不可读取，未生成零库存结论。",
                    query_type="inventory", query=dict(product=target), data=None,
                    completeness={}, context=context)
            known, zero, missing = [], [], []
            for row in materials:
                value = row.get("recorded_stock_qty")
                if not row.get("record_found") or value is None:
                    missing.append(row)
                    continue
                known.append(row)
                try:
                    if Decimal(str(value)) == 0:
                        zero.append(row)
                except (InvalidOperation, ValueError):
                    missing.append(row)
                    continue
            scope_text = f"{target} 的 BOM 物料"
            data = dict(product=target, zero_stock_count=len(zero),
                        zero_stock_materials=[r.get("material_code") for r in zero],
                        checked_material_count=len(known), missing_record_count=len(missing),
                        inventory_scope="product_bom", calculation_scope="zero_stock_check")
        else:
            master = load_master_records()
            if not master:
                return self._turn("inventory", "data_unavailable", "库存记录暂不可读取，未生成零库存结论。",
                    query_type="inventory", query={"question": text}, data=None, completeness={}, context=context)
            known, zero, missing = [], [], []
            for code, records in master.items():
                values = [r.get("recorded_stock_qty") for r in records]
                if not records or any(v is None for v in values):
                    missing.append(code)
                    continue
                known.append(code)
                if all(Decimal(str(v)) == 0 for v in values):
                    zero.append(code)
            scope_text = "全库存表"
            data = dict(product=None, zero_stock_count=len(zero), zero_stock_materials=zero,
                        checked_material_count=len(known), missing_record_count=len(missing),
                        inventory_scope="entire_inventory_table", calculation_scope="zero_stock_check")
        if data["zero_stock_count"]:
            answer = (f"当前可核验库存中，{scope_text}有 {data['zero_stock_count']} 项库存为零："
                      f"{'、'.join(data['zero_stock_materials'][:8])}。这些物料无法支持任何正整数投产。")
        else:
            answer = (f"当前已发布库存数据中，{scope_text}未发现库存为零的项目：零库存 0 项，"
                      f"已核验的 {len(known)} 项物料均有正数库存记录")
            if data["missing_record_count"]:
                answer += f"；另有 {data['missing_record_count']} 项缺少库存记录，按“未知”处理，未计为零库存"
            else:
                answer += "；也无缺失库存记录的物料"
            answer += "。"
        return self._turn("inventory", "ok", answer, query_type="inventory",
                          query={"question": text}, data=data,
                          completeness={"book_observation": True}, context=context)

    def _duplicate_observations(self, text, context):
        """重复库存观测统计：分别给出重复料号数与带标记的记录行数。"""
        master = load_master_records()
        if not master:
            return self._turn("inventory", "data_unavailable", "库存记录暂不可读取，未生成重复观测统计。",
                query_type="inventory", query={"question": text}, data=None, completeness={}, context=context)
        duplicate_codes = [code for code, records in master.items()
                           if any(r.get("duplicate_observation") for r in records)]
        duplicate_rows = sum(1 for records in master.values() for r in records if r.get("duplicate_observation"))
        answer = (f"当前全库存表中有 {len(duplicate_codes)} 个料号存在重复库存观测，"
                  f"这些料号中带重复标记的记录共 {duplicate_rows} 条。"
                  f" 两个数字含义不同：前者是料号数，后者是记录行数。"
                  f" 重复记录不能直接求和或取最大值当作可信库存，需要按库存质量规则核验后再参与计算。")
        data = dict(duplicate_material_count=len(duplicate_codes),
                    duplicate_record_count=duplicate_rows,
                    duplicate_materials=duplicate_codes[:20],
                    inventory_scope="entire_inventory_table",
                    calculation_scope="duplicate_observation_statistics")
        return self._turn("inventory", "ok", answer, query_type="inventory",
                          query={"question": text}, data=data,
                          completeness={"book_observation": True}, context=context)

    # ------------------------------------------------------------- 4-4 投产扣减 + 更新库存文件
    def _deduct(self, text, context, rows, product, quantity, session=None):
        """Natural-language issue requests create a preview, never stock movements."""
        if not product or quantity is None:
            question = ("请先选择或指明产品。" if not product else
                        "请提供大于0的整数生产数量，我会先生成领料预览，不会直接扣减。")
            return self._turn("clarification", "needs_input", question,
                              query_type="clarification", query={"question": text},
                              data=None, completeness={}, context=context)
        if not isinstance(session, dict) or not (session.get("session_id") or session.get("id")):
            return self._turn("production_capacity", "query_failed",
                              "本次领料预览缺少有效会话，未执行库存操作。",
                              query_type="inventory_preview",
                              query={"product": product, "quantity": quantity},
                              data=None, completeness={}, context=context,
                              extra={"operation": "inventory_preview"})
        try:
            from services.safe_inventory_runtime import preview_issue
            demand = (context.get("current_demand") or {})
            requested_unit = (demand.get("unit") if demand.get("status") == "valid"
                              and demand.get("product") == product else None)
            result = preview_issue(self.service, {**session, "context": context}, product, quantity,
                                   requested_unit=requested_unit)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Inventory preview failed without executing stock movements")
            return self._turn("production_capacity", "query_failed",
                              "领料预览暂不可用，未执行库存扣减。",
                              query_type="inventory_preview",
                              query={"product": product, "quantity": quantity},
                              data=None, completeness={}, context=context,
                              extra={"operation": "inventory_preview"})
        data = result.get("data")
        saved = copy.deepcopy(context)
        if isinstance(data, dict) and data.get("preview_id"):
            saved["inventory_preview"] = {
                "preview_id": data["preview_id"], "product": product,
                "quantity": quantity, "executed": False,
            }
        answer = result.get("answer") or (
            "已生成领料预览，库存尚未扣减。请核对明细；执行须另行明确确认并通过权限校验。"
            if result["status"] == "ok" and data else
            "；".join(result.get("warnings") or ["本次未能生成领料预览，未执行库存扣减。"]))
        return self._turn("production_capacity", result["status"], answer,
                          query_type="inventory_preview",
                          query=result.get("query") or {"product": product, "quantity": quantity},
                          data=data, completeness=result.get("completeness") or {},
                          provenance=result.get("provenance"),
                          warnings=result.get("warnings") or [], context=saved,
                          references=_references("inventory_issue_preview",
                                                 {"product": product, "quantity": quantity}, result),
                          extra={"operation": "inventory_preview"})

    # ------------------------------------------------------------- 4-3 投产核算 + 采购BOM表单
    def _production_plan(self, text, context, rows, product, quantity):
        if not product:
            return self._turn("clarification", "needs_input",
                              "请先选择或指明产品（例如 KQ-Z112 / 飞腾八核核心板），并给出投产数量，"
                              "我会自动核对库存并在不足时生成需要采购的BOM表单。",
                              query_type="clarification", query={"question": text}, data=None,
                              completeness={}, context=context)
        analysis = self._analysis(product, quantity)
        if analysis is None or analysis.get("status") != "ok" or not analysis.get("data"):
            return self._turn("production_capacity", "query_failed",
                              "投产核算未能读取完整的 BOM/库存事实，未生成采购数量，也未推断库存。",
                              query_type="production_capacity", query={"product": product, "quantity": quantity},
                              data=None, completeness={}, context=context)
        data = analysis["data"]
        unit = data.get("product_unit") or "片"
        shortages = list(data.get("book_shortages") or [])
        no_record = [r for r in data["materials"] if r.get("comparison_status") == "no_stock"]
        uncomputable = list(data.get("uncomputable_materials") or [])
        full = bool(data["materials"]) and all(r.get("comparison_status") == "comparable" for r in data["materials"])
        sufficient = False if shortages else (True if full else None)
        purchase_rows = []
        for row in shortages:
            purchase_rows.append(dict(material=row["material_code"], name=row.get("name"), unit=row.get("unit"),
                                      shortage_qty=row.get("book_shortage_qty"),
                                      suggested_purchase_qty=row.get("suggested_purchase_qty")
                                      or row.get("book_shortage_qty"),
                                      purchase_cost=None, currency=None, supplier=None, lead_time_days=None,
                                      risk_level=None,
                                      reason=["按账面缺口计算的补货数量（未取得权威MOQ/包装倍数参数，未作估算）"],
                                      missing_fields=["MOQ", "order_multiple", "lead_time_days", "price"]))
        # 不可核算物料（无记录/重复待核验/单位冲突/单位未知）一律不生成确定采购数量。
        unknown = [dict(material=row["material_code"], name=row.get("name"), unit=row.get("unit"),
            stock_unit=row.get("stock_unit"),
            required_qty=row.get("required_qty"), shortage_qty=None, suggested_purchase_qty=None,
            reason=self._unverified_reason(row)) for row in data["materials"]
            if row.get("comparison_status") != "comparable"]
        data["unverified_purchase_materials"] = unknown
        data["unverified_purchase_count"] = len(unknown)
        display = _display_factory(self.service)
        for row in purchase_rows:
            row["material"] = display(row["material"])
        gaps = [f"{display(r['material_code'])}（{r.get('name') or '物料'}）需求 {r.get('required_qty')}"
                f"{r.get('unit') or unit}、"
                f"账面 {r.get('recorded_stock_qty') if r.get('recorded_stock_qty') is not None else '无记录'}"
                f"{r.get('stock_unit') or r.get('unit') or ''}、"
                f"缺口 {r.get('book_shortage_qty') or r.get('required_qty')}{r.get('unit') or unit}"
                for r in shortages[:10]]
        answer = (f"{_product_label(product, rows)}投产 {quantity}{unit} 的库存核对结果："
                  + ("账面库存不足。" if sufficient is False else
                     "账面库存满足本次需求。" if sufficient is True else
                     "部分物料缺少可核验库存记录或单位无法核对，完整齐套结论待确认。") + " ")
        if gaps:
            answer += "缺料清单：" + "；".join(gaps) + "。 "
        if purchase_rows:
            answer += (f"已自动生成需要采购的BOM表单：共 {len(purchase_rows)} 种物料需采购，"
                       f"表单已标明建议采购数量（按缺口/需求量列示；未取得权威MOQ、包装倍数与价格参数，未作估算）。")
        else:
            answer += "当前没有可核实的采购缺口。" if no_record else "无需生成采购清单。"
        if unknown:
            answer += f" 另有{len(unknown)}种物料未生成确定缺口与采购数量（见待核验清单）。"
        answer += " 本核算为账面数量核对，不代表正式工单放行或采购已下单。"

        if not purchase_rows:
            return self._turn("production_capacity", "ok", answer, query_type="production_capacity",
                              query=dict(product=product, quantity=quantity), data=data,
                              completeness=analysis.get("completeness") or {},
                              provenance=analysis.get("provenance"),
                              warnings=list(analysis.get("warnings") or []),
                              context=self._remember(context, product=product, quantity=quantity),
                              references=_references("enterprise_book_demand_analysis",
                                                     dict(product=product, quantity=quantity), analysis))
        purchase_data = dict(product=data.get("product"), revision=data.get("revision"),
                             product_unit=unit, requested_quantity=quantity,
                             inventory_sufficient_for_requested=sufficient,
                             quantity_check_status=data.get("quantity_check_status"),
                             total_purchase_cost=None, total_cost_currency=None,
                             recommendations=purchase_rows,
                              unverified_purchase_materials=unknown,
                             unverified_purchase_count=len(unknown),
                             purchase_material_count=len(purchase_rows),
                             materials=data.get("materials"), book_shortages=shortages,
                             total_material_count=data.get("total_material_count"),
                             matched_material_count=data.get("matched_material_count"),
                             comparable_material_count=data.get("comparable_material_count"),
                             missing_stock_material_count=data.get("missing_stock_material_count"),
                             missing_stock_materials=data.get("missing_stock_materials"),
                             unit_conflict_materials=data.get("unit_conflict_materials"),
                             unit_unknown_materials=data.get("unit_unknown_materials"),
                             duplicate_unverified_materials=data.get("duplicate_unverified_materials"),
                             uncomputable_material_count=data.get("uncomputable_material_count"),
                             quantity_review=data.get("quantity_review"))
        return self._turn("purchase_planning", "ok", answer, query_type="purchase_planning",
                          query=dict(product=product, quantity=quantity), data=purchase_data,
                          completeness=analysis.get("completeness") or {},
                          provenance=analysis.get("provenance"),
                          warnings=list(analysis.get("warnings") or []),
                          context=self._remember(context, product=product, quantity=quantity),
                          references=_references("pmc_purchase_bom_planning",
                                                 dict(product=product, quantity=quantity), analysis))

    # ------------------------------------------------------------- 3 缺料/急刻意图
    def _shortage(self, text, context, rows, product, quantity):
        if not product:
            listing = "；".join(f"{r.get('product_code')}"
                                f"{'（' + r['product_name'] + '）' if r.get('product_name') else ''}"
                                for r in rows)
            return self._turn("clarification", "needs_input",
                              f"请指明要分析哪个产品的缺料情况，当前可查询产品：{listing}。",
                              query_type="clarification", query={"question": text}, data=None,
                              completeness={}, context=context)
        analysis = self._analysis(product, quantity)
        if analysis is None or analysis.get("status") != "ok" or not analysis.get("data"):
            return self._turn("production_capacity", "query_failed",
                              "缺料分析未能读取完整的 BOM/库存事实，未生成缺料清单。",
                              query_type="production_capacity", query={"product": product}, data=None,
                              completeness={}, context=context)
        data = analysis["data"]
        unit = data.get("product_unit") or "片"
        display = _display_factory(self.service)
        label = _product_label(product, rows)
        if quantity is not None:
            gaps = [r for r in data["materials"] if (_decimal(r.get("book_shortage_qty")) or Decimal("0")) > 0]
            no_record = [r for r in data["materials"] if r.get("comparison_status") == "no_stock"]
            unverified = [r for r in data["materials"] if r.get("comparison_status") not in
                          {"comparable", "no_stock"}]
            if gaps:
                listing = "；".join(
                    f"{display(r['material_code'])}（{r.get('name') or '物料'}）需求 {r.get('required_qty')}"
                    f"{r.get('unit') or unit}、账面库存 "
                    f"{r.get('recorded_stock_qty') if r.get('recorded_stock_qty') is not None else '无记录'}"
                    f"{r.get('stock_unit') or r.get('unit') or ''}、"
                    f"缺口 {r.get('book_shortage_qty')}{r.get('unit') or unit}" for r in gaps[:12])
                if len(gaps) > 12:
                    listing += f"；其余 {len(gaps) - 12} 项见物料明细"
                total_gap = sum((_decimal(r.get("book_shortage_qty")) or Decimal("0")) for r in gaps)
                answer = (f"{label}生产 {quantity}{unit} 的缺料分析：共 {len(data['materials'])} 种物料，"
                          f"其中 {len(gaps)} 种存在缺口。缺料/急缺物料清单：{listing}。"
                          f" 结论：当前账面库存不能满足投产 {quantity}{unit}，"
                          f"缺口合计约 {format(total_gap, 'f')}（各物料数量之和，仅作规模参考）；"
                          f"需按缺口补齐物料后方可齐套。")
            else:
                answer = (f"{label}生产 {quantity}{unit} 的缺料分析："
                          f"已核验的 {len(data['materials'])} 种物料账面库存均满足本次需求，当前没有急缺物料。")
            if no_record:
                # 无记录不是“按需求全量采购”的依据：只说明需求已保留、未生成确定采购数量。
                answer += (f" 另有 {len(no_record)} 种物料没有库存记录，需求已保留，"
                           f"未生成确定采购数量（无记录不等于零库存）："
                           f"{'、'.join(display(r['material_code']) for r in no_record[:8])}。")
            if unverified:
                answer += (f" 另有 {len(unverified)} 种物料因单位冲突、单位未知或重复记录未核验而不可核算，"
                           f"未生成确定缺口与采购数量："
                           f"{'、'.join(display(r['material_code']) for r in unverified[:8])}。")
            answer += " 本分析为账面数量核对口径，不代表正式工单放行。"
            result = dict(status="ok", query=dict(product=product, quantity=quantity), data=data,
                          completeness=analysis.get("completeness") or {},
                          provenance=analysis.get("provenance"),
                          warnings=list(analysis.get("warnings") or []))
            return self._turn("production_capacity", "ok", answer, query_type="production_capacity",
                              query=result["query"], data=result["data"], completeness=result["completeness"],
                              provenance=result["provenance"], warnings=result["warnings"],
                              context=self._remember(context, product=product, quantity=quantity),
                              references=_references("pmc_shortage_analysis", result["query"], result))
        # 未给出投产数量：按 BOM 单耗与当前账面库存识别急缺/最紧张物料。
        ranked, unknown = [], []
        for row in data["materials"]:
            limit = row.get("max_supported_quantity")
            if limit is None:
                limit = _max_supported(row)
            if limit is None:
                unknown.append(row)
            else:
                ranked.append((limit, row))
        ranked.sort(key=lambda pair: (pair[0], pair[1].get("material_code") or ""))
        critical = [row for limit, row in ranked if limit == 0]
        maximum = min((limit for limit, _ in ranked), default=None)
        limiting = [row for limit, row in ranked if limit == maximum][:6] if maximum is not None else []
        critical_text = "；".join(
            f"{display(r['material_code'])}（{r.get('name') or '物料'}）单耗 {r.get('qty_per')}"
            f"{r.get('unit') or unit}、账面库存 {r.get('recorded_stock_qty')}"
            f"{r.get('inventory_unit') or unit}" for r in critical[:12]) or "无"
        tense_text = "；".join(
            f"{display(r['material_code'])}（{r.get('name') or '物料'}）最多可支持 {limit}{unit}"
            for limit, r in ranked if limit > 0) or "无"
        if len([1 for limit, _ in ranked if limit > 0]) > 8:
            tense_text = "；".join(
                f"{display(r['material_code'])}（{r.get('name') or '物料'}）最多可支持 {limit}{unit}"
                for limit, r in [(l, r) for l, r in ranked if l > 0][:8]) + "；其余见物料明细"
        answer = (f"{label}急缺物料分析（按 BOM 单耗与当前账面库存核算）："
                  f" 急缺（库存不足 1{unit}，无法投产）：{critical_text}。"
                  f" 其余最紧张物料（按最大可支持产量升序）：{tense_text}。")
        if maximum is not None:
            answer += (f" 结论：当前账面库存整体最多可满足 {maximum}{unit}"
                       f"（限制物料：{'、'.join(display(r['material_code']) for r in limiting)}）；"
                       f"{'上述急缺物料' if critical else '上述最紧张物料'}建议优先补料/采购。")
        if unknown:
            answer += (f" 另有 {len(unknown)} 种物料缺少可核验的可比库存"
                       f"（无记录、单位冲突或重复记录未核验），未纳入核算"
                       f"（{'、'.join(display(r['material_code']) for r in unknown[:8])}）。")
        answer += " 本分析为账面数量核对口径，不替代正式缺料工单。"
        result = dict(status="ok", query=dict(product=product, quantity=None), data=data,
                      completeness=analysis.get("completeness") or {},
                      provenance=analysis.get("provenance"),
                      warnings=list(analysis.get("warnings") or []))
        return self._turn("production_capacity", "ok", answer, query_type="production_capacity",
                          query=result["query"], data=result["data"], completeness=result["completeness"],
                          provenance=result["provenance"], warnings=result["warnings"],
                          context=self._remember(context, product=product),
                          references=_references("pmc_shortage_analysis", result["query"], result))

    # ------------------------------------------------------------- 2 独立物料查询（脱离产品）
    def _standalone_material(self, text, context, rows):
        if _BOM_ASK.search(text):
            return None  # BOM/物料组成问题保留既有受控流程
        tokens = _material_tokens(text)
        resolved = None
        for token in tokens:
            candidate = _resolve_material(token)
            if candidate:
                resolved = candidate
                break
        if resolved is None:
            last = (context.get("pmc_capability") or {}).get("last_material")
            if last and _FOLLOWUP_MATERIAL.search(text):
                resolved = _resolve_material(last)
        if not _INV_ASK.search(text) and not _SHORTAGE.search(text) and tokens:
            return None  # 只接管明确的库存/批次/数量/缺料类提问
        if resolved is None:
            # 未给出料号时，按物料名称（如“贴片电阻”）查询；同名的多个料号并列给出，不合并。
            named = _materials_by_name(text) if not tokens else None
            if named and not _SHORTAGE.search(text):
                return self._named_material_stock(named, text, context)
        if resolved is None:
            return None
        if _SHORTAGE.search(text):
            return self._material_coverage(text, context, rows, resolved, _parse_quantity(text))

        display = _display_factory(self.service)
        code = resolved["material_code"]
        material_code = display(code)
        name = resolved.get("name") or "物料"
        row = BookInventoryService._stock(
            code, {"records": {code: resolved["records"]}} if resolved["records"] else {"records": {}})
        name = name if name != "物料" else (row.get("name") or "物料")
        if not row["record_found"]:
            answer = (f"{material_code}（{name}）：主库存表中没有该料号的账面记录"
                      f"（无记录不等于零库存），请核对料号或补充入库记录后再核算。")
            if resolved["aliases"]:
                answer += f" 已识别的展示别名：{'、'.join(resolved['aliases'])}。"
            data = dict(calculation_scope="excel_book_observation", materials=[dict(row, material_code=material_code,
                                                                                    name=name)],
                        product=material_code, record_found=False,
                        source_summary=dict(source_file="主库存表"))
            return self._turn("inventory", "item_not_found", answer, query_type="inventory",
                              query=dict(material_code=material_code), data=data,
                              completeness={"recorded_stock_qty": False},
                              context=self._remember(context, last_material=code))
        details = sort_fifo(row.get("details") or [])
        for detail in details:
            detail["material_code"] = material_code
        row["details"] = details
        row["material_code"] = material_code
        row["name"] = name
        total = row.get("recorded_stock_qty")
        stock_unit, stock_unit_basis = BookInventoryService._stock_unit(row, {"records": {}})
        row["stock_unit"] = stock_unit; row["stock_unit_basis"] = stock_unit_basis
        duplicate_unverified = not row["duplicate_free"]
        batch_text = "；".join(
            f"批次 {d.get('batch_no') or '-'}（库位 {d.get('location') or '-'}）"
            f"结余 {d.get('recorded_stock_qty')}{d.get('inventory_unit') or ''}" for d in details[:8])
        if len(details) > 8:
            batch_text += f"；其余 {len(details) - 8} 条见批次明细"
        if total is None and duplicate_unverified:
            answer = (f"{material_code}（{name}）：该料号有 {row.get('record_count')} 条重复观测记录，"
                      f"尚未取得可核验的合并口径，未给出确定账面合计、未取最大值也未求和。"
                      f" 原始批次明细：{batch_text}。")
        else:
            answer = (f"{material_code}（{name}）：账面库存合计 {total}{stock_unit or ''}"
                      f"（{row.get('record_count')} 条记录、{row.get('batch_count')} 个批次、"
                      f"{row.get('location_count')} 个库位）。"
                      f" 批次明细（先进先出，批次日期靠前优先）：{batch_text}。")
        if stock_unit is None:
            answer += " 该料号缺少可核验的计量单位，未参与确定性缺口/采购计算。"
        if resolved["aliases"]:
            answer += f" 展示别名：{'、'.join(resolved['aliases'])}。"
        answer += " 数据来源：已核验主库存账面记录；不提供权威可用量/预留量，无记录不补零。"
        data = dict(calculation_scope="excel_book_observation", materials=[row], product=material_code,
                    record_found=True,
                    source_summary=dict(source_file="主库存表", record_count=row.get("record_count"),
                                        unique_material_count=len(load_master_records())))
        return self._turn("inventory", "ok", answer, query_type="inventory",
                          query=dict(material_code=material_code), data=data,
                          completeness={"recorded_stock_qty": row.get("recorded_stock_qty") is not None},
                          context=self._remember(context, last_material=code),
                          references=_references("pmc_material_inventory_query",
                                                 dict(material_code=code),
                                                 dict(status="ok", query=dict(material_code=code), data=data,
                                                      completeness={"recorded_stock_qty": True},
                                                      provenance={"source_file": "主库存表"}, warnings=[])))

    def _named_material_stock(self, named, text, context):
        """按物料名称查询库存个数；同名料号并列列出各自账面数量，不合并、不猜测。"""
        display = _display_factory(self.service)
        materials = []
        for item in named["materials"]:
            code = item["material_code"]
            row = BookInventoryService._stock(
                code, {"records": {code: item["records"]}} if item["records"] else {"records": {}})
            if not row["record_found"]:
                continue
            shown = display(code)
            details = sort_fifo(row.get("details") or [])
            for detail in details:
                detail["material_code"] = shown
            row["details"] = details
            row["material_code"] = shown
            row["name"] = row.get("name") or named["name"]
            materials.append(row)
        if not materials:
            return None
        materials.sort(key=lambda r: _decimal(r.get("recorded_stock_qty")) or Decimal("0"), reverse=True)
        lines = [f"{r['material_code']}（{r['name']}）账面库存 "
                 f"{r.get('recorded_stock_qty')}{r.get('inventory_unit') or ''}"
                 f"（{r.get('batch_count') or 0} 个批次、{r.get('record_count') or 0} 条记录）"
                 for r in materials[:8]]
        if len(materials) > 8:
            lines.append(f"其余 {len(materials) - 8} 个料号见物料明细")
        answer = (f"物料名称「{named['name']}」在主库存表中对应 {len(materials)} 个料号："
                  + "；".join(lines) + "。"
                  f" 同名不同料号按料号分别列示，库存不合并、不互相替代，请按料号核对。"
                  f" 数据来源：已核验主库存账面记录，无记录不补零。")
        data = dict(calculation_scope="excel_book_observation", materials=materials,
                    product=named["name"], record_found=True, matched_material_count=len(materials),
                    source_summary=dict(source_file="主库存表",
                                        unique_material_count=len(load_master_records())))
        return self._turn("inventory", "ok", answer, query_type="inventory",
                          query=dict(material_name=named["name"]), data=data,
                          completeness={"recorded_stock_qty": True}, context=context)

    def _material_coverage(self, text, context, rows, material, quantity):
        """独立物料的缺料/覆盖分析：账面库存 × 各产品BOM单耗。"""
        display = _display_factory(self.service)
        code = material["material_code"]
        material_code = display(code)
        name = material.get("name") or "物料"
        row = BookInventoryService._stock(
            code, {"records": {code: material["records"]}} if material["records"] else {"records": {}})
        stock_unit, stock_unit_basis = BookInventoryService._stock_unit(row, {"records": {}})
        from services.production_request_context import parse_quantity, compatible_unit
        request = parse_quantity(text)
        if request.status == "valid" and stock_unit and not compatible_unit(request.unit, stock_unit):
            return self._turn("inventory", "invalid_query",
                f"需求单位{request.unit}与库存单位{stock_unit}无法直接比较；未计算缺口。",
                query_type="inventory", query=dict(material_code=code, requested_unit=request.unit),
                data=None, completeness={}, context=context)
        name = name if name != "物料" else (row.get("name") or "物料")
        stock = _decimal(row.get("recorded_stock_qty"))
        duplicate_unverified = bool(row["record_found"] and not row["duplicate_free"])
        details = sort_fifo(row.get("details") or [])
        for detail in details:
            detail["material_code"] = material_code
        row["details"] = details
        row["material_code"] = material_code
        row["name"] = name
        row["stock_unit"] = stock_unit
        row["stock_unit_basis"] = stock_unit_basis
        usages = []
        try:
            from repositories.fact_store import create_fact_store
            from services.fact_query_service import FactQueryService
            from repositories.fact_query_repository import FactQueryRepository
            from repositories.fact_repository import FactRepository
            facts = FactQueryService(FactQueryRepository(FactRepository(create_fact_store())))
            for entry in _catalog_rows():
                if entry.get("synthetic"):
                    continue
                result = facts.query_bom(entry["product_code"], entry.get("revision") or "")
                for line in ((result.get("data") or {}).get("lines") or []):
                    child = (line.get("child_item") or {}).get("material_code")
                    if child == code:
                        usages.append(dict(product=entry["product_code"], product_name=entry.get("product_name"),
                                           qty_per=line.get("qty_per"), unit=line.get("unit")))
        except Exception:
            usages = []
        lines, gap_lines, has_gap, blocked = [], [], False, []

        def in_usage_unit(value, usage_unit):
            """把库存数量规范化到 BOM 单耗单位；无法核验或跨维度返回 None。"""
            if value is None:
                return None
            if not usage_unit and not stock_unit:
                return value  # 双方都没有单位时保持既有账面数量对比，不做臆断换算
            if not usage_unit or not stock_unit:
                return None
            if usage_unit == stock_unit:
                return value
            return unit_system.convert(value, stock_unit, usage_unit)

        for usage in usages:
            per = _decimal(usage.get("qty_per"))
            stock_in_unit = in_usage_unit(stock, usage.get("unit"))
            limit = int(stock_in_unit // per) if (stock_in_unit is not None and per and per > 0) else None
            usage["max_supported_quantity"] = limit
            usage["unit_comparable"] = stock_in_unit is not None
            lines.append(f"{_product_label(usage['product'], rows)}单耗 {usage.get('qty_per')}"
                         f"{usage.get('unit') or ''}，当前最多可支持 {limit if limit is not None else '未知'}")
            if quantity is not None and per is not None and stock_in_unit is not None:
                required = per * quantity
                gap = max(required - stock_in_unit, Decimal("0"))
                has_gap = has_gap or gap > 0
                gap_lines.append(f"{_product_label(usage['product'], rows)}需求 {_num(required)}"
                                 f"{usage.get('unit') or ''}、缺口 {_num(gap)}")
            elif quantity is not None and per is not None and stock is not None and stock_in_unit is None:
                blocked.append(usage)
        if quantity is not None:
            available_text = (_num(stock) if stock is not None else
                              f"重复记录待核验（{row.get('record_count') or 0} 条）" if duplicate_unverified else
                              "无记录" if not row["record_found"] else "单位不可核验")
            answer = (f"{material_code}（{name}）：账面库存 {available_text}"
                      f"{stock_unit or ''}，按投产 {quantity} 的需求核对："
                      + ("；".join(gap_lines) if gap_lines else
                         "该物料不在任何已发布产品BOM中，无可核对的BOM需求" if not usages else
                         "单位或库存口径不可比，未生成确定缺口")
                      + (f"；其中 {len(blocked)} 项BOM因需求单位与库存单位不可换算，未计算缺口"
                         if blocked else "")
                      + ("。结论：存在缺口，建议按缺口采购补齐。" if has_gap else
                         "。结论：账面库存满足需求。" if gap_lines else "。"))
        else:
            if stock == 0:
                verdict = "缺料：账面库存为 0，任何投产数量都无法满足。"
            elif stock is not None:
                verdict = "当前账面库存可支持有限数量投产，属于紧张物料。"
            elif duplicate_unverified:
                verdict = ("该料号存在未核验的重复库存记录，未取最大值、未求和，"
                           "库存与缺口保持未知，不生成确定采购数量。")
            elif not row["record_found"]:
                verdict = ("库存无记录（无记录不等于零库存）：需求已保留，未生成确定采购数量；"
                           "请核对料号或补充入库记录。")
            else:
                verdict = "库存计量单位无法核验，未生成确定缺口与采购数量。"
            answer = (f"{material_code}（{name}）：账面库存 "
                      f"{_num(stock) if stock is not None else '不可用于核算'}"
                      f"{stock_unit or ''}"
                      f"（{row.get('record_count') or 0} 条记录、{row.get('batch_count') or 0} 个批次）。")
            if lines:
                answer += " 各产品BOM覆盖：" + "；".join(lines) + "。"
            answer += f" 结论：{verdict}"
        data = dict(calculation_scope="excel_book_observation", materials=[row], product=material_code,
                    requested_quantity=quantity, total_material_count=1,
                    comparison_status=("comparable" if stock is not None and stock_unit else
                                       "duplicate_unverified" if duplicate_unverified else
                                       "no_stock" if not row["record_found"] else "unit_unknown"),
                    material_stock_coverage=dict(material=dict(
                        material_code=material_code, name=name,
                        recorded_stock_qty=_num(stock), inventory_unit=stock_unit,
                        unit_basis=stock_unit_basis, record_found=row.get("record_found"),
                        duplicate_free=row["duplicate_free"]), usages=usages, requested_quantity=quantity))
        return self._turn("production_capacity", "ok", answer, query_type="production_capacity",
                          query=dict(material_code=material_code, quantity=quantity), data=data,
                          completeness={"recorded_stock_qty": row.get("recorded_stock_qty") is not None},
                          context=self._remember(context, last_material=code),
                          references=_references("pmc_material_shortage_query",
                                                 dict(material_code=code, quantity=quantity),
                                                 dict(status="ok", query=dict(material_code=code),
                                                      data=data, completeness={}, provenance=None, warnings=[])))


__all__ = ["PmcCapabilityQueries", "batch_fifo_key", "sort_fifo"]
