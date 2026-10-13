"""真实数据适配的质量报告生成。"""

import json
from collections import Counter
from pathlib import Path

from repositories.material_master_repository import index_material_master, load_material_master_records
from repositories.data_source import load_demo_data, resolve_data_source_mode
from repositories.real_bom_repository import load_real_bom_relationships
from repositories.real_inventory_repository import INVENTORY_SHEET_NAME, load_real_inventory_data


NORMALIZED_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "normalized"
QUALITY_REPORT_FILE = NORMALIZED_DATA_DIR / "data_quality_report.json"
REQUIRED_MISSING_INVENTORY_CODES = {
    "105A502764101",
    "105A525564101",
    "105A549964201",
    "11296220A0101",
    "19T0Z11200101",
}


def get_required_missing_inventory_codes() -> set[str]:
    """返回当前数据源声明的必须显式报告料号。"""
    if resolve_data_source_mode() == "demo":
        return {str(code) for code in load_demo_data()["required_missing_inventory_codes"]}
    return REQUIRED_MISSING_INVENTORY_CODES.copy()


def build_real_data_quality_report() -> dict[str, object]:
    """生成真实 BOM、库存与料号主数据的可审计质量结果。"""
    bom_payload = load_real_bom_relationships()
    relationships = bom_payload["relationships"]
    bom_codes = [str(item["child_material"]) for item in relationships]
    inventory_data = load_real_inventory_data()
    master_records = load_material_master_records()
    material_master = index_material_master(master_records)

    missing_inventory_codes = sorted(set(bom_codes) - set(inventory_data))
    duplicate_bom_codes = sorted(code for code, count in Counter(bom_codes).items() if count > 1)
    duplicate_inventory_codes = sorted(
        code
        for code, item in inventory_data.items()
        if len(item["details"]) > 1
    )
    duplicate_master_codes = sorted(
        code for code, count in Counter(record["material_code"] for record in master_records).items() if count > 1
    )

    required_missing_codes = get_required_missing_inventory_codes()
    return {
        "bom_parent_material": bom_payload["parent_material"],
        "bom_version": bom_payload["version"],
        "bom_relationship_count": len(relationships),
        "bom_material_count": len(set(bom_codes)),
        "material_master_match_count": sum(code in material_master for code in set(bom_codes)),
        "inventory_match_count": len(set(bom_codes) & set(inventory_data)),
        "unmatched_inventory_material_codes": missing_inventory_codes,
        "required_missing_inventory_codes": sorted(required_missing_codes),
        "required_missing_inventory_codes_confirmed": required_missing_codes.issubset(missing_inventory_codes),
        "duplicate_material_codes": {
            "bom": duplicate_bom_codes,
            "inventory": duplicate_inventory_codes,
            "material_master": duplicate_master_codes,
        },
        "excluded_data_sources": [
            "BOM: 版本说明",
            "BOM: 加工原材料",
            "BOM: 生产说明",
            "库存: 电阻电容现库位",
            "库存: 成品库存表",
            "库存: 2023年至2025年形成的存货-可以处理",
            "库存: 阻容库存表-2019",
        ],
        "current_default_assumptions": {
            "inventory_source_sheet": INVENTORY_SHEET_NAME,
            "reserved": 0,
            "incoming": 0,
            "safety_stock": "not_provided_in_source_not_defaulted",
            "supplier_master": "no_material_supplier_map_or_purchase_parameters_generated",
        },
    }


def write_real_data_quality_report(output_path: Path = QUALITY_REPORT_FILE) -> dict[str, object]:
    """写出 UTF-8 JSON 质量报告。"""
    report = build_real_data_quality_report()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
