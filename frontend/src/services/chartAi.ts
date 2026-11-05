/** Only server-reported analysis and execution events are rendered. */
import type { AgentExecutionEvent, ChartAiView } from "./backendTypes";
type Obj = Record<string, unknown>;
const obj = (value: unknown): Obj => value && typeof value === "object" && !Array.isArray(value) ? value as Obj : {};
const strings = (value: unknown): string[] | undefined => Array.isArray(value) && value.every(x => typeof x === "string") ? value : undefined;
export function adaptChartAi(value: unknown): ChartAiView | undefined {
  if (value == null) return;
  const raw = obj(value), sections = obj(raw.sections);
  if (raw.status === "not_applicable") return { status: "not_applicable" };
  if (raw.status === "loading") return { status: "loading" };
  const findings = strings(sections.findings), actions = strings(sections.actions);
  if (raw.status === "ok" && raw.source === "llm" && typeof sections.conclusion === "string" &&
      sections.conclusion.trim() && typeof sections.impact === "string" && sections.impact.trim() && findings && actions)
    return { status: "ok", source: "llm", sections: { conclusion: sections.conclusion, findings,
      impact: sections.impact, actions } };
  return { status: "unavailable", ...(typeof raw.error_code === "string" ? { errorCode: raw.error_code } : {}) };
}
const labels: Record<string, string> = { intent: "任务", product: "产品", product_code: "产品", material_code: "物料",
  quantity: "数量", unit: "单位", tool: "工具", tool_name: "工具", count: "项数", material_count: "物料项数",
  requested_quantity: "目标数量", comparable_material_count: "已核算物料", missing_stock_material_count: "库存未匹配", result_type: "结果类型", model: "模型", provider: "服务", query: "问题", message: "问题" };
export function adaptExecutionTrace(value: unknown): AgentExecutionEvent[] {
  if (!Array.isArray(value)) return [];
  return value.map(obj).filter(x => typeof x.stage === "string" && typeof x.status === "string" && typeof x.summary === "string")
    .map(x => ({ stage: x.stage as string, status: x.status as string, summary: x.summary as string,
      ...(typeof x.duration_ms === "number" && Number.isFinite(x.duration_ms) && x.duration_ms >= 0 ? { durationMs: x.duration_ms } : {}),
      details: Object.entries(obj(x.details)).filter(([key, v]) => key in labels &&
        (typeof v === "string" || typeof v === "number" && Number.isFinite(v) || typeof v === "boolean"))
        .map(([key, v]) => ({ label: labels[key], value: String(v) })) }));
}
