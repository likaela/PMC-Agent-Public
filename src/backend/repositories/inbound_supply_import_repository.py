"""在途事实来源合同。

当前私有库存工作簿没有可靠采购订单/在途字段，因此本模块只定义受控适配器
必须提供的标准行，不从库存分配列或 supplier master 推断在途量。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from fact_layer.models import InboundSupplyInputRow


class InboundSupplySource(Protocol):
    """未来 ERP/采购订单适配器必须实现的最小合同。"""

    source_name: str

    def iter_rows(self) -> Iterable[InboundSupplyInputRow]: ...


def current_real_inbound_availability() -> dict[str, object]:
    """明确声明当前企业源不能产出可靠在途事实。"""
    return {
        "real_inbound_facts_available": False,
        "published_fact_count": 0,
        "reason": "当前来源未提供经确认的采购订单、已收数量和承诺到货日期事实",
        "prohibited_inferences": [
            "不从库存表扩展列推断在途量",
            "不从 supplier master 推断物料供应关系",
            "不把缺失 received_qty 默认成 0",
        ],
    }


__all__ = ["InboundSupplySource", "current_real_inbound_availability"]
