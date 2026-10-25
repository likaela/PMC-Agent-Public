/**
 * Presentation helpers for the analysis trace ("thinking line").
 *
 * Two honest sources are combined, never invented:
 * - pending stages are the request phases the browser actually executes
 *   (/resolve, session setup, /query);
 * - settled steps are the backend's own structured_data.completed_steps.
 * There is no token stream or private reasoning: the UI never fabricates steps.
 */
import type { AnalysisStep } from "./backendTypes";

export type TraceStageKey = "resolve" | "session" | "query";
export type ThoughtStepState = "done" | "active" | "upcoming" | "failed";

export interface ThoughtStep {
  key: string;
  label: string;
  state: ThoughtStepState;
  detail?: string;
  /** Backend operation name, kept for audit (tooltip/title), never reworded. */
  operation?: string;
}

export const traceStageOrder: readonly TraceStageKey[] = [
  "resolve",
  "session",
  "query",
];

export const traceStageLabels: Record<TraceStageKey, string> = {
  resolve: "理解问题并识别产品",
  session: "确认分析范围与会话",
  query: "核对已发布业务事实",
};

/** Backend tool/operation names, translated without inventing semantics. */
const operationLabels: Record<string, string> = {
  published_product_search: "检索已发布产品目录",
  bom_query: "查询 BOM 组成与单耗",
  inventory_query: "查询库存事实",
  batch_inventory_query: "查询批次库存",
  inventory_risk: "评估库存风险",
  inventory_risk_analysis: "分析库存风险",
  inventory_fifo: "规划 FIFO 领用",
  production_analysis: "核对生产需求与可用物料",
  purchase_plan: "生成采购建议",
  quantity_validation: "校验生产数量",
  enterprise_book_demand_analysis: "核对账面记录",
  enterprise_book_inventory_query: "核对账面库存",
  enterprise_book_capacity_query: "核对账面产能",
};

const statusLabels: Record<string, string> = {
  data_unavailable: "无已发布数据",
  query_failed: "查询失败",
  invalid_query: "输入无效",
  needs_input: "待补充条件",
  item_not_found: "未找到记录",
  item_alias_ambiguous: "物料名称不唯一",
};

/** Unknown operations stay verbatim so the trace remains auditable. */
export function stepLabel(operation: string): string {
  return operationLabels[operation] ?? operation;
}

export function stepStatusDetail(status: string): string | undefined {
  return status === "ok" ? undefined : (statusLabels[status] ?? status);
}

export function pendingStageLabel(stage: TraceStageKey | ""): string {
  return stage ? `正在${traceStageLabels[stage]}` : "正在分析";
}

/** Stages before the current one are done; the current one is active. */
export function pendingSteps(stage: TraceStageKey | ""): ThoughtStep[] {
  const raw = stage ? traceStageOrder.indexOf(stage) : 0;
  const index = raw < 0 ? 0 : raw;
  return traceStageOrder.map((key, position) => ({
    key,
    label: traceStageLabels[key],
    state:
      position < index
        ? ("done" as const)
        : position === index
          ? ("active" as const)
          : ("upcoming" as const),
  }));
}

/** Real backend steps, shown once the response is stored. */
export function settledSteps(
  steps: readonly AnalysisStep[] | undefined,
): ThoughtStep[] {
  return (steps ?? [])
    .filter((step) => step.operation)
    .map((step, index) => ({
      key: `${index}-${step.operation}`,
      label: stepLabel(step.operation),
      state: step.status === "ok" ? ("done" as const) : ("failed" as const),
      detail: stepStatusDetail(step.status),
      operation: step.operation,
    }));
}

export function isTraceComplete(
  steps: readonly AnalysisStep[] | undefined,
): boolean {
  return (
    !!steps?.length &&
    steps.every((step) => step.operation && step.status === "ok")
  );
}

/** Compact timer: "1.4s" under a minute, "1m 3.2s" above. */
export function formatElapsed(ms: number): string {
  const tenths = Math.max(0, Math.round((Number.isFinite(ms) ? ms : 0) / 100));
  if (tenths < 600) return `${(tenths / 10).toFixed(1)}s`;
  const minutes = Math.floor(tenths / 600);
  const seconds = ((tenths % 600) / 10).toFixed(1);
  return `${minutes}m ${seconds}s`;
}
