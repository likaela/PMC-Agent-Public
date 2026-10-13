import {
  QueryError,
  type BackendRequest,
  type BackendResponse,
  type BackendSession,
  type BackendHistory,
  type ScenarioId,
  type AgentQueryService,
} from "../data/model";
import {
  adaptBackendResponse,
  validateBackendResponse,
} from "./responseAdapter";

export interface ClientOptions {
  baseUrl?: string;
  timeoutMs?: number;
  fetch?: typeof fetch;
}
export function configuredBaseUrl(): string {
  const value =
    (import.meta.env?.VITE_API_BASE_URL as string | undefined)?.trim() ?? "";
  return value === "/" ? "/" : value.replace(/\/+$/, "");
}
function publicError(status: number): QueryError {
  if (status === 404)
    return new QueryError("session", "当前会话不存在或已失效，请新建分析。");
  if (status === 409)
    return new QueryError("session", "当前会话已有新消息，请恢复会话后重试。");
  if (status === 400 || status === 422)
    return new QueryError("invalid", "查询条件不完整或无效，请检查输入。");
  return new QueryError("server", "分析服务暂时异常，请稍后重试。");
}
export function createAgentClient(options: ClientOptions = {}) {
  const configured = options.baseUrl ?? configuredBaseUrl();
  const base = configured.replace(/\/+$/, "");
  const timeout = options.timeoutMs ?? 30000;
  const fetcher = options.fetch ?? globalThis.fetch;
  async function request(
    path: string,
    body: unknown | undefined,
    signal?: AbortSignal,
  ): Promise<unknown> {
    if (!configured)
      throw new QueryError(
        "unconfigured",
        "请先配置后端 API 地址，再使用在线查询。",
      );
    if (!Number.isFinite(timeout) || timeout <= 0)
      throw new QueryError("unconfigured", "请求超时配置无效。");
    if (signal?.aborted) throw new DOMException("停止等待", "AbortError");
    const controller = new AbortController();
    let timedOut = false;
    const abort = () => controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeout);
    try {
      const response = await fetcher(`${base}${path}`, {
        method: body === undefined ? "GET" : "POST",
        headers:
          body === undefined
            ? undefined
            : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      // Reviewed public messages only; never render arbitrary exception bodies.
      if (!response.ok) throw publicError(response.status);
      try {
        return await response.json();
      } catch {
        throw new QueryError("mapping", "服务返回内容暂无法展示，请稍后重试。");
      }
    } catch (error) {
      if (signal?.aborted) throw new DOMException("停止等待", "AbortError");
      if (timedOut)
        throw new QueryError(
          "timeout",
          "分析等待超时，请恢复会话确认结果后重试。",
        );
      if (error instanceof QueryError) throw error;
      throw new QueryError("network", "无法连接分析服务，请检查连接后重试。");
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
    }
  }
  return {
    async queryAgent(
      input: BackendRequest,
      signal?: AbortSignal,
    ): Promise<BackendResponse> {
      const body: BackendRequest = { message: input.message };
      if (input.session_id) body.session_id = input.session_id;
      if (input.scenario_id) body.scenario_id = input.scenario_id;
      if (input.persist_session !== undefined)
        body.persist_session = input.persist_session;
      if (input.catalog_order !== undefined)
        body.catalog_order = input.catalog_order;
      return validateBackendResponse(
        await request("/api/v1/agent/query", body, signal),
      );
    },
    async createSession(
      scenarioId?: ScenarioId,
      signal?: AbortSignal,
      productCode?: string,
    ): Promise<BackendSession> {
      return validateSession(
        await request(
          "/api/v1/agent/sessions",
          {
            ...(scenarioId ? { scenario_id: scenarioId } : {}),
            ...(productCode ? { product_code: productCode } : {}),
          },
          signal,
        ),
      );
    },
    async getSession(
      sessionId: string,
      scenarioId?: ScenarioId,
      after = 0,
      signal?: AbortSignal,
    ): Promise<BackendHistory> {
      const params = new URLSearchParams({
        after: String(after),
        limit: "200",
      });
      if (scenarioId) params.set("scenario_id", scenarioId);
      const value = await request(
        `/api/v1/agent/sessions/${encodeURIComponent(sessionId)}?${params}`,
        undefined,
        signal,
      );
      const session = validateSession(value);
      const turns = (value as Record<string, unknown>).turns;
      if (!Array.isArray(turns))
        throw new QueryError("mapping", "会话记录暂无法展示。");
      const checked = turns.map((turn) => {
        if (
          !turn ||
          typeof turn !== "object" ||
          typeof turn.turn_id !== "string" ||
          typeof turn.user_query !== "string" ||
          typeof turn.created_at !== "string" ||
          !Number.isSafeInteger(turn.sequence_no) ||
          turn.sequence_no <= after
        )
          throw new QueryError("mapping", "会话记录暂无法展示。");
        return { ...turn, response: validateBackendResponse(turn.response) };
      });
      return { ...session, turns: checked };
    },
  };
}
function validateSession(value: unknown): BackendSession {
  if (!value || typeof value !== "object")
    throw new QueryError("mapping", "会话信息暂无法展示。");
  const s = value as Record<string, unknown>;
  if (
    typeof s.session_id !== "string" ||
    !s.session_id ||
    typeof s.conversation_id !== "string" ||
    typeof s.created_at !== "string" ||
    typeof s.updated_at !== "string" ||
    !Number.isSafeInteger(s.context_version) ||
    !s.context ||
    typeof s.context !== "object" ||
    Array.isArray(s.context)
  )
    throw new QueryError("mapping", "会话信息暂无法展示。");
  return value as BackendSession;
}
export const queryAgent = (input: BackendRequest, signal?: AbortSignal) =>
  createAgentClient().queryAgent(input, signal);
export const createSession = (scenarioId?: ScenarioId, signal?: AbortSignal) =>
  createAgentClient().createSession(scenarioId, signal);
export const getSession = (
  id: string,
  scenarioId?: ScenarioId,
  after = 0,
  signal?: AbortSignal,
) => createAgentClient().getSession(id, scenarioId, after, signal);
export function createLiveService(
  options: ClientOptions = {},
): AgentQueryService {
  const client = createAgentClient(options);
  return {
    async query(input, signal) {
      return adaptBackendResponse(
        await client.queryAgent(
          {
            message: input.text,
            session_id: input.session_id,
            scenario_id: input.scenario_id,
            persist_session: input.persist_session,
          },
          signal,
        ),
      );
    },
  };
}
