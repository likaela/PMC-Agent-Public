"""Demo 体验记录与统计（仅当前进程内）。

这里不读取或保存 Excel、BOM、库存等企业源数据；仅保留用户授权提交的
自然语言输入、关联 workflow 标识和简短反馈，以便小范围体验测试复盘。
"""

from __future__ import annotations

from datetime import UTC, datetime
from threading import RLock
from typing import TypedDict


class DemoExperienceRecord(TypedDict):
    session_time: str
    workflow_id: str
    user_input: str
    analysis_completed: bool
    user_feedback: str | None


_records: dict[str, DemoExperienceRecord] = {}
_scenario_count = 0
_lock = RLock()


def record_demo_analysis(workflow_id: str, user_input: str, *, analysis_completed: bool) -> None:
    """记录一次首轮体验；同一 workflow 不重复计入体验次数。"""
    with _lock:
        _records.setdefault(
            workflow_id,
            {
                "session_time": datetime.now(UTC).isoformat(),
                "workflow_id": workflow_id,
                "user_input": user_input,
                "analysis_completed": analysis_completed,
                "user_feedback": None,
            },
        )


def record_demo_scenario() -> None:
    """记录一次成功发起的减量方案推演。"""
    global _scenario_count
    with _lock:
        _scenario_count += 1


def add_demo_feedback(workflow_id: str, feedback: str, *, user_input: str) -> DemoExperienceRecord:
    """补充用户反馈；对没有首轮记录的旧 workflow 安全创建最小记录。"""
    with _lock:
        record = _records.setdefault(
            workflow_id,
            {
                "session_time": datetime.now(UTC).isoformat(),
                "workflow_id": workflow_id,
                "user_input": user_input,
                "analysis_completed": True,
                "user_feedback": None,
            },
        )
        record["user_feedback"] = feedback
        return record.copy()


def get_demo_statistics() -> dict[str, int]:
    """返回匿名聚合指标，不暴露体验记录正文。"""
    with _lock:
        return {
            "total_experience_count": len(_records),
            "successful_analysis_count": sum(1 for record in _records.values() if record["analysis_completed"]),
            "scenario_count": _scenario_count,
            "feedback_count": sum(1 for record in _records.values() if record["user_feedback"]),
        }
