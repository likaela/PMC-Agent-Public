"""真实库存表的只读 Excel 适配器。"""

from collections.abc import Iterable
from pathlib import Path

from openpyxl import load_workbook

from repositories.normalization import normalize_header, normalize_material_code, normalize_nonnegative_number
from repositories.data_source import RAW_INVENTORY_FILE, load_demo_data, resolve_data_source_mode


INVENTORY_FILE = RAW_INVENTORY_FILE
INVENTORY_SHEET_NAME = "库存表"
SOURCE_ASSUMPTIONS = {
    "reserved": 0,
    "incoming": 0,
    "safety_stock": "not_provided_in_source",
}

# 业务已确认：这些料号在本次库存核对中属于错误记录，不参与当前 MRP。
# 此清单只控制库存/MRP 输入范围，不修改真实 BOM 关系。
INVENTORY_DATA_ERROR_MATERIAL_CODES = frozenset(
    {
        "19T0Z11200101", "11296220A0101", "105A502764101", "105A525564101",
        "105A549964201", "11296010B1101", "11215227B1101", "11215016B1101",
        "1124468BB1101", "11245108B0101", "105A703304201", "105A551164101",
        "105A700004201", "105A524004201", "105A547504201", "105A510004201",
        "105A747004101", "105A519124201", "105A530124201", "105A532414201",
        "105A513324201", "105A560424201",
    }
)


def get_inventory_data_error_material_codes() -> frozenset[str]:
    """返回当前数据源明确声明的库存排除料号。"""
    if resolve_data_source_mode() == "demo":
        return frozenset(str(code) for code in load_demo_data()["inventory_exclusions"])
    return INVENTORY_DATA_ERROR_MATERIAL_CODES


def load_real_inventory_details(file_path: Path | None = None) -> list[dict[str, object]]:
    """只读取库存表工作表，保留站点、库位和批次库存明细。"""
    if file_path is None and resolve_data_source_mode() == "demo":
        return [
            {
                "material_code": str(item["material_code"]),
                "stock": normalize_nonnegative_number(item["stock"], "库存"),
                "site": "演示工厂",
                "location": "DEMO",
                "batch": "DEMO",
                "source_sheet": INVENTORY_SHEET_NAME,
            }
            for item in load_demo_data()["inventory_details"]
        ]

    workbook = load_workbook(file_path or INVENTORY_FILE, read_only=True, data_only=True)
    try:
        sheet = workbook[INVENTORY_SHEET_NAME]
        header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))
        headers = {normalize_header(value): index for index, value in enumerate(header_row)}
        required_columns = {"归属地", "库位", "物料代码", "批次号", "库存"}
        if not required_columns.issubset(headers):
            raise ValueError("库存表缺少一期必需列")

        details: list[dict[str, object]] = []
        for row in sheet.iter_rows(min_row=2, values_only=True):
            material_code = normalize_material_code(row[headers["物料代码"]])
            if not material_code or material_code in INVENTORY_DATA_ERROR_MATERIAL_CODES:
                continue
            details.append(
                {
                    "material_code": material_code,
                    "stock": normalize_nonnegative_number(row[headers["库存"]], "库存"),
                    "site": str(row[headers["归属地"]] or "").strip(),
                    "location": str(row[headers["库位"]] or "").strip(),
                    "batch": str(row[headers["批次号"]] or "").strip(),
                    "source_sheet": INVENTORY_SHEET_NAME,
                }
            )
        return details
    finally:
        workbook.close()


def aggregate_real_inventory(details: Iterable[dict[str, object]]) -> dict[str, dict[str, object]]:
    """仅按库存表明细聚合库存；不合并其他库存工作表。"""
    result: dict[str, dict[str, object]] = {}
    for detail in details:
        material_code = str(detail["material_code"])
        stock = normalize_nonnegative_number(detail["stock"], "库存")
        if material_code not in result:
            result[material_code] = {
                "material_code": material_code,
                "stock": 0,
                "reserved": SOURCE_ASSUMPTIONS["reserved"],
                "incoming": SOURCE_ASSUMPTIONS["incoming"],
                "source_assumptions": SOURCE_ASSUMPTIONS.copy(),
                "details": [],
            }
        result[material_code]["stock"] = result[material_code]["stock"] + stock
        result[material_code]["details"].append(detail.copy())
    return result


def load_real_inventory_data(file_path: Path | None = None) -> dict[str, dict[str, object]]:
    """读取并按物料代码聚合真实库存。"""
    return aggregate_real_inventory(load_real_inventory_details(file_path))


def build_real_inventory_scope(
    material_codes: Iterable[str],
    file_path: Path | None = None,
) -> dict[str, dict[str, object]]:
    """为给定物料生成一期 MRP 所需的库存状态，绝不把未知来源转成零库存。"""
    main_inventory = load_real_inventory_data(file_path)
    scope: dict[str, dict[str, object]] = {}
    for material_code in material_codes:
        if material_code in main_inventory:
            item = main_inventory[material_code].copy()
            item["inventory_status"] = "known"
            scope[material_code] = item
        else:
            scope[material_code] = {
                "material_code": material_code,
                "inventory_status": "missing",
                "stock": None,
                "available": None,
                "shortage": None,
                "source_assumptions": {
                    "reason": "主库存及待确认库存来源均未发现料号",
                },
            }
    return scope


def exclude_inventory_data_errors(
    requirements: dict[str, int],
) -> tuple[dict[str, int], list[str]]:
    """从 MRP 输入中剔除已确认的库存错误记录，保留 BOM 原始关系。"""
    excluded = sorted(set(requirements).intersection(get_inventory_data_error_material_codes()))
    return (
        {material: quantity for material, quantity in requirements.items() if material not in excluded},
        excluded,
    )
