import { QueryError, type AgentQueryService } from "../data/model";
import { adaptBackendResponse } from "./responseAdapter";
import type { Product, HistoryPage, ProductResolution } from "./backendTypes";
import { streamlitRequest } from "./streamlitBridge";
const prefix = "/api/v1/agent";
// All requests stay same-origin. Vite proxies development only; production needs a reverse proxy.
export async function requestJson(
  path: string,
  signal: AbortSignal,
  body?: unknown,
  method?: "DELETE",
  extraHeaders?: Record<string, string>,
): Promise<unknown> {
  let response: Response;
  try {
    const transport = import.meta.env.VITE_STREAMLIT === "true" ? streamlitRequest : fetch;
    response = await transport(path, {
      signal,
      method: method ?? (body === undefined ? "GET" : "POST"),
      headers: { "Content-Type": "application/json", ...extraHeaders },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new QueryError(
      "network",
      "无法连接后端，请检查服务后重试。",
      "CONNECTION_FAILED",
    );
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new QueryError(
      response.ok ? "mapping" : "network",
      response.ok
        ? "后端没有返回有效 JSON，请检查同源代理。"
        : "后端连接失败或代理不可用，请检查后端服务后刷新历史。",
      "INVALID_JSON",
      response.status,
    );
  }
  const value = payload as Record<string, unknown>;
  if (!response.ok)
    throw new QueryError(
      "network",
      typeof value?.user_message === "string"
        ? value.user_message
        : `请求失败（HTTP ${response.status}）`,
      typeof value?.code === "string" ? value.code : "HTTP_ERROR",
      response.status,
      payload,
    );
  return payload;
}
export function createLiveService(): AgentQueryService {
  return {
    async catalog(signal) {
      const value = (await requestJson(`${prefix}/catalog`, signal)) as {
        products?: Product[];
      };
      if (
        !Array.isArray(value.products) ||
        value.products.some(
          (p) =>
            !p.product_code ||
            !Array.isArray(p.modes) ||
            !p.modes.some((m) => m.mode === p.default_mode),
        )
      )
        throw new QueryError("mapping", "产品目录结构不完整。");
      // Published product scopes only. Demo/scenario data cannot enter this app.
      return value.products
        .filter(
          (p) =>
            p.synthetic === false &&
            p.modes.some(
              (m) => m.mode === p.default_mode && m.scenario_id === null,
            ),
        )
        .map((p) => ({
          ...p,
          modes: p.modes.filter((m) => m.scenario_id === null),
        }));
    },
    async resolve(text, session, catalogOrder, signal) {
      const value = (await requestJson(`${prefix}/resolve`, signal, {
        message: text,
        session_id: session?.session_id ?? null,
        scenario_id: null,
        catalog_order: catalogOrder,
      })) as ProductResolution;
      if (
        typeof value.status !== "string" ||
        !(value.product_code === null || typeof value.product_code === "string")
      )
        throw new QueryError("mapping", "产品识别响应不完整，请稍后重试。");
      return value;
    },
    async createSession(product, scenario, signal) {
      if (scenario !== null)
        throw new QueryError("unsupported", "当前版本仅连接已发布业务数据。");
      const value = (await requestJson(`${prefix}/sessions`, signal, {
        product_code: product,
        scenario_id: scenario,
      })) as { session_id: string; conversation_id: string };
      if (!value.session_id || !value.conversation_id)
        throw new QueryError("mapping", "后端未返回有效会话 ID。");
      return value;
    },
    async history(binding, after, signal) {
      if (binding.scenario_id !== null)
        throw new QueryError("unsupported", "该记录不属于当前业务数据范围。");
      const query = new URLSearchParams({ after: String(after), limit: "50" });
      if (binding.scenario_id) query.set("scenario_id", binding.scenario_id);
      const value = (await requestJson(
        `${prefix}/sessions/${encodeURIComponent(binding.session_id)}?${query}`,
        signal,
      )) as HistoryPage;
      if (!Array.isArray(value.turns))
        throw new QueryError("mapping", "历史记录结构不完整。");
      if (
        value.product_context?.product_code &&
        value.product_context.product_code !== binding.product_code
      )
        throw new QueryError(
          "mapping",
          "历史会话与所选产品不一致。",
          "SESSION_BINDING_MISMATCH",
        );
      return value;
    },
    async clearSessions(signal) {
      return requestJson(`${prefix}/sessions`, signal, undefined, "DELETE");
    },
    async executeInventory(input, credential, signal) {
      if (import.meta.env.VITE_STREAMLIT === "true")
        throw new QueryError("unsupported", "当前连接仅支持查询与预览，未开放库存执行。");
      if (input.confirmed !== true || !input.session_id || !input.preview_id || !credential)
        throw new QueryError("unsupported", "请先核对当前预览并明确确认执行。");
      // This is deliberately separate from query(), with no automatic retry.
      // Credentials belong only in this request header, never the conversation.
      if (signal.aborted) throw new DOMException("Cancelled", "AbortError");
      const controller = new AbortController();
      const abort = () => controller.abort();
      signal.addEventListener("abort", abort, { once: true });
      let timedOut = false;
      const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 30_000);
      try {
        return adaptBackendResponse(await requestJson(
          "/api/v1/inventory/execute",
          controller.signal,
          {
            session_id: input.session_id,
            preview_id: input.preview_id,
            confirmed: true,
            ...(input.scenario_id == null ? {} : { scenario_id: input.scenario_id }),
          },
          undefined,
          { "X-Inventory-Write-Token": credential },
        ));
      } catch (error) {
        if (timedOut) throw new QueryError("network", "执行等待超时，请同步历史核实结果；不会自动重试。", "EXECUTION_TIMEOUT");
        throw error;
      } finally {
        clearTimeout(timer);
        signal.removeEventListener("abort", abort);
      }
    },
    async getInventoryReceipt(input, signal) {
      if (!input.session_id || !input.preview_id)
        throw new QueryError("unsupported", "请提供原会话与领料预览后核实回执。");
      const query = new URLSearchParams({ session_id: input.session_id });
      return adaptBackendResponse(await requestJson(
        `/api/v1/inventory/receipts/${encodeURIComponent(input.preview_id)}?${query}`,
        signal,
      ));
    },
    async query(input, signal) {
      if (!input.session)
        throw new QueryError("unconfigured", "请在问题中提供产品型号。");
      if (input.session.scenario_id !== null)
        throw new QueryError("unsupported", "当前版本仅连接已发布业务数据。");
      return adaptBackendResponse(
        await requestJson(`${prefix}/query`, signal, {
          message: input.text,
          session_id: input.session.session_id,
          persist_session: true,
          scenario_id: input.session.scenario_id,
        }),
      );
    },
  };
}
