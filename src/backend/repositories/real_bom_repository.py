"""真实 KQ-Z112 BOM 的只读 Excel 适配器。"""

import re
from pathlib import Path

import xlrd

from repositories.normalization import normalize_header, normalize_material_code, normalize_nonnegative_int
from repositories.data_source import RAW_BOM_FILE, load_demo_data, resolve_data_source_mode


BOM_FILE = RAW_BOM_FILE
BOM_SHEET_NAME = "BOM"
METADATA_ROW_INDEX = 1
HEADER_ROW_INDEX = 2
DATA_ROW_START_INDEX = 3


def _get_bom_sheet(file_path: Path) -> xlrd.sheet.Sheet:
    workbook = xlrd.open_workbook(file_path)
    return workbook.sheet_by_name(BOM_SHEET_NAME)


def load_real_bom_metadata(file_path: Path | None = None) -> dict[str, str]:
    """从 BOM 工作表元数据行读取父项料号和版本，不从文件名推断。"""
    if file_path is None and resolve_data_source_mode() == "demo":
        metadata = load_demo_data()["metadata"]
        return {
            "parent_material": str(metadata["parent_material"]),
            "version": str(metadata["version"]),
        }
    sheet = _get_bom_sheet(file_path or BOM_FILE)
    metadata_text = " ".join(str(value) for value in sheet.row_values(METADATA_ROW_INDEX))
    match = re.search(r"项目代号\s*:\s*([^\s]+).*版本号\s*:\s*([^\s]+)", metadata_text)
    if match is None:
        raise ValueError("BOM 元数据行缺少项目代号或版本号")
    return {"parent_material": match.group(1).strip(), "version": match.group(2).strip()}


def load_real_bom_relationships(
    file_path: Path | None = None,
    expected_parent_material: str | None = None,
    expected_version: str | None = None,
) -> dict[str, object]:
    """读取 BOM 关系，输出标准字段 parent_material、child_material、qty_per、version。"""
    metadata = load_real_bom_metadata(file_path)
    if expected_parent_material is not None and metadata["parent_material"] != expected_parent_material:
        raise ValueError("BOM 父项料号与请求不一致")
    if expected_version is not None and metadata["version"] != expected_version:
        raise ValueError("BOM 版本与请求不一致")

    if file_path is None and resolve_data_source_mode() == "demo":
        relationships = [
            {
                "parent_material": metadata["parent_material"],
                "child_material": str(item["child_material"]),
                "qty_per": normalize_nonnegative_int(item["qty_per"], "BOM 数量"),
                "version": metadata["version"],
            }
            for item in load_demo_data()["bom_relationships"]
        ]
        return {**metadata, "relationships": relationships}

    sheet = _get_bom_sheet(file_path or BOM_FILE)
    headers = {normalize_header(value): index for index, value in enumerate(sheet.row_values(HEADER_ROW_INDEX))}
    if "料号" not in headers or "数量" not in headers:
        raise ValueError("BOM 工作表缺少料号或数量列")

    relationships: list[dict[str, object]] = []
    for row_index in range(DATA_ROW_START_INDEX, sheet.nrows):
        row = sheet.row_values(row_index)
        child_material = normalize_material_code(row[headers["料号"]])
        if not child_material:
            continue
        qty_per = normalize_nonnegative_int(row[headers["数量"]], "BOM 数量")
        if qty_per == 0:
            raise ValueError(f"BOM 第 {row_index + 1} 行数量不能为零")
        relationships.append(
            {
                "parent_material": metadata["parent_material"],
                "child_material": child_material,
                "qty_per": qty_per,
                "version": metadata["version"],
            }
        )

    return {**metadata, "relationships": relationships}


def load_real_bom_data(file_path: Path | None = None) -> dict[str, dict[str, int]]:
    """转换为 BOM 服务可直接消费的 ``{parent: {child: qty_per}}`` 结构。"""
    payload = load_real_bom_relationships(file_path)
    parent_material = str(payload["parent_material"])
    result: dict[str, dict[str, int]] = {parent_material: {}}
    for relationship in payload["relationships"]:
        child_material = str(relationship["child_material"])
        if child_material in result[parent_material]:
            raise ValueError(f"真实 BOM 存在重复子料号：{child_material}")
        result[parent_material][child_material] = int(relationship["qty_per"])
    return result
