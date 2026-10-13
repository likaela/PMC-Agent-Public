"""PMC 数据源选择与公开演示数据读取。

``raw`` 只读取本机忽略的企业原始文件；``demo`` 读取仓库内的纯合成数据；
``auto`` 在完整 raw 文件存在时优先使用 raw，否则回退到 demo。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
DEMO_DATA_FILE = DATA_DIR / "demo" / "kq_z112_demo.json"

RAW_BOM_FILE = RAW_DATA_DIR / "10 KQ-Z112 BOM 969T0Z1120101  23.xls"
RAW_INVENTORY_FILE = RAW_DATA_DIR / "2026年库存表+料号查询表及成品库存表(1).xlsx"
RAW_SUPPLIER_FILE = RAW_DATA_DIR / "供方导出.xlsx"
REQUIRED_RAW_FILES = (RAW_BOM_FILE, RAW_INVENTORY_FILE, RAW_SUPPLIER_FILE)

DataSourceMode = Literal["raw", "demo"]


def resolve_data_source_mode() -> DataSourceMode:
    """返回当前数据源；非法配置立即给出可诊断错误。"""
    configured = os.getenv("PMC_DATA_SOURCE", "auto").strip().lower()
    if configured not in {"auto", "raw", "demo"}:
        raise ValueError("PMC_DATA_SOURCE 仅支持 auto、raw 或 demo")
    if configured == "raw":
        return "raw"
    if configured == "demo":
        return "demo"
    return "raw" if all(path.is_file() for path in REQUIRED_RAW_FILES) else "demo"


def load_demo_data() -> dict[str, Any]:
    """读取不含真实企业信息的合成演示数据。"""
    if not DEMO_DATA_FILE.is_file():
        raise FileNotFoundError("脱敏演示数据文件不存在")
    payload = json.loads(DEMO_DATA_FILE.read_text(encoding="utf-8"))
    if payload.get("synthetic_data") is not True:
        raise ValueError("演示数据必须明确标记为 synthetic_data=true")
    return payload


def get_data_source_status() -> dict[str, object]:
    """返回不含本机绝对路径和企业文件名的数据就绪状态。"""
    try:
        mode = resolve_data_source_mode()
    except ValueError as error:
        return {"status": "error", "mode": "invalid", "message": str(error)}

    if mode == "raw":
        ready = all(path.is_file() for path in REQUIRED_RAW_FILES)
        return {
            "status": "ok" if ready else "error",
            "mode": mode,
            "message": "本机企业数据已就绪" if ready else "本机企业数据文件不完整",
        }

    try:
        load_demo_data()
    except (OSError, ValueError, json.JSONDecodeError):
        return {"status": "error", "mode": mode, "message": "脱敏演示数据不可用"}
    return {"status": "ok", "mode": mode, "message": "脱敏演示数据已就绪"}


__all__ = [
    "DATA_DIR",
    "DEMO_DATA_FILE",
    "RAW_BOM_FILE",
    "RAW_DATA_DIR",
    "RAW_INVENTORY_FILE",
    "RAW_SUPPLIER_FILE",
    "get_data_source_status",
    "load_demo_data",
    "resolve_data_source_mode",
]
