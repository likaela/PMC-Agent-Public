"""比赛演示启动与健康检查，不执行 PMC 业务计算。"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from config import get_llm_configuration_status
from repositories.data_source import DATA_DIR, get_data_source_status


DEFAULT_LOCAL_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


def get_allowed_origins() -> list[str]:
    """合并安全的本地默认 Origin 与显式配置，拒绝通配符。"""
    configured = os.getenv("PMC_ALLOWED_ORIGINS", "")
    origins = [*DEFAULT_LOCAL_ORIGINS, *(item.strip() for item in configured.split(",") if item.strip())]
    if "*" in origins:
        raise ValueError("PMC_ALLOWED_ORIGINS 不允许使用 '*'，请配置明确 origin")
    return list(dict.fromkeys(origins))


def build_runtime_checks() -> dict[str, dict[str, object]]:
    """检查数据、工作流、LLM 和本地跨域配置，不泄露密钥或绝对路径。"""
    data_status = get_data_source_status()
    data_status["directory_exists"] = DATA_DIR.is_dir()

    try:
        from workflows.pmc_graph import PMC_GRAPH

        workflow_ready = PMC_GRAPH is not None and callable(getattr(PMC_GRAPH, "invoke", None))
    except Exception:
        workflow_ready = False

    try:
        origins = get_allowed_origins()
        cors_check: dict[str, object] = {
            "status": "ok",
            "message": "本地前端跨域配置已就绪",
            "allowed_origin_count": len(origins),
        }
    except ValueError:
        cors_check = {
            "status": "error",
            "message": "跨域配置无效，请检查 PMC_ALLOWED_ORIGINS",
            "allowed_origin_count": 0,
        }

    return {
        "data": data_status,
        "workflow": {
            "status": "ok" if workflow_ready else "error",
            "message": "PMC 工作流已初始化" if workflow_ready else "PMC 工作流初始化失败",
        },
        "llm": get_llm_configuration_status(),
        "cors": cors_check,
    }


def overall_health_status(checks: Mapping[str, Mapping[str, object]]) -> str:
    """LLM warning 不影响服务可用性，关键检查 error 才视为 degraded。"""
    critical = (checks.get("data", {}), checks.get("workflow", {}), checks.get("cors", {}))
    return "degraded" if any(item.get("status") == "error" for item in critical) else "ok"


def log_startup_checks(logger: logging.Logger) -> None:
    """只记录公开状态摘要，不记录环境变量值、路径或密钥。"""
    for name, check in build_runtime_checks().items():
        logger.info("启动检查 [%s] %s：%s", name, check.get("status"), check.get("message"))


__all__ = [
    "DEFAULT_LOCAL_ORIGINS",
    "build_runtime_checks",
    "get_allowed_origins",
    "log_startup_checks",
    "overall_health_status",
]
