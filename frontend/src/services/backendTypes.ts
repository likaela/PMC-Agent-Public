export interface ProductMode {
  mode: string;
  label: string;
  scenario_id: string | null;
  line_count?: number;
}
export interface Product {
  product_code: string;
  product_name: string;
  unit?: string;
  revision?: string;
  synthetic: boolean;
  default_mode: string;
  modes: ProductMode[];
}
export interface ProductResolution {
  status: string;
  product_code: string | null;
  catalog_query: boolean;
  message?: string;
  answer?: string;
  explicit?: boolean;
  analysis_mode?: string | null;
}
export interface SessionBinding {
  session_id: string;
  conversation_id?: string;
  product_code: string;
  mode: string;
  scenario_id: string | null;
  turn_id?: string;
}
export interface BackendTurn {
  turn_id: string;
  sequence_no: number;
  user_query: string;
  created_at: string;
  response: unknown;
  fact_references: unknown[];
}
export interface HistoryPage {
  session_id: string;
  conversation_id: string;
  turns: BackendTurn[];
  product_context?: { product_code?: string; scenario_id?: string | null };
}
export interface AnalysisStep {
  operation: string;
  status: string;
}
export interface InventoryAllocation {
  materialCode: string | null;
  batch: string | null;
  receivedAt: string | null;
  quantity: string | null;
  before: string | null;
  after: string | null;
}
export interface InventoryOperationView {
  kind: "preview" | "execution";
  previewId: string | null;
  expiresAt: string | null;
  boundSessionId: string | null;
  canExecute: boolean;
  executionAvailable: boolean;
  executed: boolean;
  blockers: string[];
  allocations: InventoryAllocation[];
}
export interface InventoryExecuteInput {
  session_id: string;
  preview_id: string;
  confirmed: true;
  scenario_id?: string | null;
}
export interface BackendView {
  resultType: string;
  status: string;
  reused: boolean;
  sessionId?: string;
  conversationId?: string;
  turnId?: string;
  metrics: { label: string; value: string }[];
  columns: { key: string; label: string; numeric?: boolean }[];
  rows: Record<string, string>[];
  /** Backend-reported tool trace (structured_data.completed_steps); never inferred. */
  steps: AnalysisStep[];
  inventoryOperation?: InventoryOperationView;
  raw: unknown;
}
