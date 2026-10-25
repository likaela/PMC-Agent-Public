import type {
  BackendView,
  Product,
  SessionBinding,
  HistoryPage,
  ProductResolution,
  InventoryExecuteInput,
} from "../services/backendTypes";
// Frontend display model only. This is NOT an agreed backend schema.
export type ResultStatus =
  | "partial"
  | "ready"
  | "inventory"
  | "zero"
  | "no-facts"
  | "conflict"
  | "uncalculable"
  | "purchase"
  | "text";
export type MaterialState =
  "shortage" | "unknown" | "sufficient" | "published" | "no-facts";
export interface Material {
  id: string;
  name: string;
  spec: string;
  required: number | null;
  available: number | null;
  shortage: number | null;
  unit: string;
  state: MaterialState;
  sourceIds: string[];
}
export interface Source {
  id: string;
  title: string;
  kind: "接口数据";
  recordId?: string | null;
  scope: string;
  reference: string;
  version: string | null;
  publishedAt: string | null;
  conditions: string;
}
export interface AgentResult {
  backend?: BackendView;
  id: string;
  status: ResultStatus;
  product: string | null;
  quantity: number | null;
  quantityUnit: string;
  title: string;
  answer: string;
  conclusion: string;
  nextAction: string;
  materials: Material[];
  sources: Source[];
  warnings: string[];
  purchase?: {
    material: string;
    shortage: number;
    safetyStock: number;
    suggested: number;
    unit: string;
    note: string;
  };
}
export type ErrorCode = "network" | "mapping" | "unconfigured" | "unsupported";
export class QueryError extends Error {
  constructor(
    public code: ErrorCode,
    message: string,
    public backendCode?: string,
    public httpStatus?: number,
    public diagnostics?: unknown,
  ) {
    super(message);
    this.name = "QueryError";
  }
}
export interface QueryInput {
  text: string;
  session?: SessionBinding;
}
export interface AgentQueryService {
  catalog?(signal: AbortSignal): Promise<Product[]>;
  resolve?(
    text: string,
    session: SessionBinding | undefined,
    catalogOrder: string[],
    signal: AbortSignal,
  ): Promise<ProductResolution>;
  createSession?(
    product: string,
    scenario: string | null,
    signal: AbortSignal,
  ): Promise<{ session_id: string; conversation_id: string }>;
  history?(
    binding: SessionBinding,
    after: number,
    signal: AbortSignal,
  ): Promise<HistoryPage>;
  /** 清空后端保存的对话历史；仅影响会话记录，不影响业务数据。 */
  clearSessions?(signal: AbortSignal): Promise<unknown>;
  /** Only a separate, explicitly confirmed action may call this method. */
  executeInventory?(
    input: InventoryExecuteInput,
    credential: string,
    signal: AbortSignal,
  ): Promise<AgentResult>;
  /** Read a durable receipt without repeating an inventory write. */
  getInventoryReceipt?(
    input: Pick<InventoryExecuteInput, "session_id" | "preview_id">,
    signal: AbortSignal,
  ): Promise<AgentResult>;
  query(input: QueryInput, signal: AbortSignal): Promise<AgentResult>;
}
export const statusText: Record<ResultStatus, string> = {
  partial: "部分数据 · 已知缺料",
  ready: "所列物料齐套",
  inventory: "库存已提供",
  zero: "库存为零",
  "no-facts": "暂无已发布数据",
  conflict: "条件冲突",
  uncalculable: "无法计算",
  purchase: "采购建议",
  text: "文字回答",
};
export const materialText: Record<MaterialState, string> = {
  shortage: "缺料",
  unknown: "待补充库存",
  sufficient: "满足需求",
  published: "已提供库存",
  "no-facts": "无已发布事实",
};
export const formatNumber = (v: number | null | undefined) =>
  v == null ? "未提供" : new Intl.NumberFormat("zh-CN").format(v);
