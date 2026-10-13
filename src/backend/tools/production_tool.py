"""生产决策与报告的 Agent 工具适配层。"""

from collections.abc import Mapping

from services.production_service import build_production_decision
from services.report_service import build_pmc_report


def make_production_decision(
    product: str,
    quantity: int,
    mrp_result: Mapping[str, object],
    purchase_analysis: Mapping[str, object],
) -> dict[str, object]:
    """调用确定性生产决策服务；不由 Agent 推导生产结论。"""
    return build_production_decision(product, quantity, mrp_result, purchase_analysis)


def build_real_pmc_report(
    product: str,
    bom_version: str,
    quantity: int,
    bom_requirements: Mapping[str, int],
    mrp_result: Mapping[str, object],
    purchase_analysis: Mapping[str, list[dict[str, object]]],
    data_quality_status: Mapping[str, object],
    production_decision: Mapping[str, object],
) -> dict[str, object]:
    """调用报告服务汇总工具结果；报告不重新计算业务数值。"""
    return build_pmc_report(
        product=product,
        bom_version=bom_version,
        production_quantity=quantity,
        bom_requirements=bom_requirements,
        mrp_result=mrp_result,
        purchase_analysis=purchase_analysis,
        data_quality_status=data_quality_status,
        production_decision=production_decision,
    )


__all__ = ["make_production_decision", "build_real_pmc_report"]
