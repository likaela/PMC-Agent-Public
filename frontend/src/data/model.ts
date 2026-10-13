// Public HTTP contract is deliberately separate from the UI display model.
export type DecimalText = string;
export type JsonObject = Record<string, unknown>;
export type ScenarioId =
  | "demo_scenario_50_vs_100"
  | "review_real_bom_simulation"
  | "kq_z112_review_slim"
  | "kq_z112_review_slim_synthetic";
export type AgentIntent =
  | "pmc_analysis"
  | "fact_query"
  | "production_capacity"
  | "clarification"
  | "inventory"
  | "inventory_batches"
  | "bom"
  | "inbound"
  | "purchase_planning";
export interface BackendRequest {
  message: string;
  session_id?: string;
  scenario_id?: ScenarioId;
  persist_session?: boolean;
  catalog_order?: string[];
}
export interface BackendResponse {
  intent: AgentIntent;
  status: string;
  answer: string;
  structured_data: JsonObject | null;
  provenance: JsonObject | JsonObject[] | null;
  warnings: string[];
  session_id?: string | null;
  conversation_id?: string | null;
  turn_id?: string | null;
  scenario_id?: ScenarioId | null;
  review_product?: string | null;
  review_mode?: string;
  dataset_version?: string | null;
}
export interface BackendSession {
  session_id: string;
  conversation_id: string;
  created_at: string;
  updated_at: string;
  context_version: number;
  context: JsonObject;
  scenario_id?: ScenarioId | null;
  product_context?: {
    product_code: string;
    scenario_id: ScenarioId | null;
    revision?: string;
  };
}
export interface BackendTurn {
  turn_id: string;
  sequence_no: number;
  user_query: string;
  response: BackendResponse;
  created_at: string;
}
export interface BackendHistory extends BackendSession {
  turns: BackendTurn[];
}
export type DataMode = "mock" | "live";
export type ResultStatus =
  | "partial"
  | "ready"
  | "inventory"
  | "zero"
  | "no-facts"
  | "conflict"
  | "uncalculable"
  | "purchase"
  | "blocked"
  | "bom"
  | "batches"
  | "inbound"
  | "clarification"
  | "result";
export type MaterialState =
  "shortage" | "unknown" | "sufficient" | "published" | "no-facts";
export interface Material {
  id: string;
  name: string;
  spec: string;
  required: DecimalText | null;
  available: DecimalText | null;
  shortage: DecimalText | null;
  code?: string;
  qtyPer?: DecimalText | null;
  onHand?: DecimalText | null;
  reserved?: DecimalText | null;
  purchaseQty?: DecimalText | null;
  recordedStock?: DecimalText | null;
  bookDifference?: DecimalText | null;
  recordCount?: string | null;
  inventoryUnit?: string | null;
  comparisonStatus?: string | null;
  recordFound?: boolean | null;
  completeness?: JsonObject;
  unit: string;
  state: MaterialState;
  sourceIds: string[];
}
export interface Source {
  id: string;
  title: string;
  kind:
    | "项目资料示例"
    | "人工构造演示样例"
    | "已发布事实"
    | "合成演示事实"
    | "Excel账面记录"
    | "人工业务确认";
  scope: string;
  reference: string;
  version: string | null;
  publishedAt: string | null;
  conditions: string;
  materialCode?: string | null;
  snapshotAt?: string | null;
  observedAt?: string | null;
  effectiveFrom?: string | null;
  importBatch?: string | null;
  sheet?: string | null;
  row?: string | null;
  sha256?: string | null;
}
export interface FrontendViewModel {
  conversation?: boolean;
  bookObservation?: boolean;
  quantityReview?: boolean;
  validationError?: boolean;
  id: string;
  status: ResultStatus;
  product: string | null;
  quantity: DecimalText | null;
  quantityUnit: string;
  title: string;
  answer: string;
  conclusion: string;
  nextAction: string;
  materials: Material[];
  sources: Source[];
  warnings: string[];
  queryType?: string;
  completeness?: JsonObject;
  backend?: BackendResponse;
  metrics?: { label: string; value: string | null }[];
  slimReview?: boolean;
  detailTable?: { columns: string[]; rows: (string | null)[][] };
  purchases?: PurchaseView[];
  purchase?: {
    material: string;
    shortage: DecimalText | null;
    safetyStock: DecimalText | null;
    suggested: DecimalText | null;
    unit: string;
    note: string;
  };
}
// Compatibility name retained for offline fixtures and existing components.
export type AgentResult = FrontendViewModel;
export interface PurchaseView {
  material: string;
  unit: string;
  shortage: DecimalText | null;
  suggested: DecimalText | null;
  cost: DecimalText | null;
  currency: string | null;
  supplier: string | null;
  leadTime: string | null;
  risk: string | null;
  reasons: string[];
  missingFields: string[];
}
export type ErrorCode =
  | "network"
  | "mapping"
  | "unconfigured"
  | "unsupported"
  | "timeout"
  | "session"
  | "invalid"
  | "server";
export class QueryError extends Error {
  constructor(
    public code: ErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "QueryError";
  }
}
export interface QueryInput {
  text: string;
  session_id?: string;
  scenario_id?: ScenarioId;
  persist_session?: boolean;
}
export interface AgentQueryService {
  query(input: QueryInput, signal: AbortSignal): Promise<AgentResult>;
}
export interface Scenario {
  id: string;
  label: string;
  prompt: string;
  category: "project" | "synthetic";
  result?: AgentResult;
  error?: "network" | "mapping";
}
export const statusText: Record<ResultStatus, string> = {
  partial: "部分数据 · 已知缺料",
  ready: "所列物料齐套",
  inventory: "库存已提供",
  zero: "库存为零",
  "no-facts": "暂无已发布数据",
  conflict: "条件冲突",
  uncalculable: "无法计算",
  purchase: "独立采购样例",
  blocked: "库存齐套不足",
  bom: "BOM 已提供",
  batches: "批次已提供",
  inbound: "在途事实已提供",
  clarification: "需要补充条件",
  result: "查询已完成",
};
export const materialText: Record<MaterialState, string> = {
  shortage: "缺料",
  unknown: "可用库存未知",
  sufficient: "满足需求",
  published: "已提供库存",
  "no-facts": "无已发布事实",
};
// String formatting only: do not round, calculate or coerce unknown to zero.
export function formatNumber(v: string | null | undefined): string {
  if (v == null) return "未提供";
  return v.replace(
    /^([+-]?)(\d+)(?=\.|$)/,
    (_, sign: string, digits: string) =>
      sign + digits.replace(/\B(?=(\d{3})+(?!\d))/g, ","),
  );
}
