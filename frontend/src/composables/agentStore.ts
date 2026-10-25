import { computed, ref, shallowRef } from "vue";
import {
  QueryError,
  type AgentResult,
  type AgentQueryService,
} from "../data/model";
import type { Product, SessionBinding } from "../services/backendTypes";
import { adaptBackendResponse } from "../services/responseAdapter";
import type { TraceStageKey } from "../services/analysisTrace";
export interface Message {
  id: number;
  question: string;
  requestedAt: string;
  state: "pending" | "done" | "error" | "cancelled";
  result?: AgentResult;
  error?: QueryError;
  turnId?: string;
  /** Client-measured wait for this turn; absent for history restored from the backend. */
  elapsedMs?: number;
  inventoryExecution?: boolean;
}
const indexKey = "pmc-public-sessions-v2";
// 历史回答可能来自旧核算口径；升级存储版本时一次性丢弃，
// 避免刷新后仍自动恢复与当前口径不符的结论。
try {
  const legacyKeys = ["pmc-public-sessions-v1"];
  if (legacyKeys.some((key) => localStorage.getItem(key) !== null)) {
    for (const key of [...legacyKeys, "pmc-active-session"])
      localStorage.removeItem(key);
  }
} catch {
  /* private browsing or non-browser runtime */
}
function readIndex(): SessionBinding[] {
  try {
    const rows = JSON.parse(localStorage.getItem(indexKey) || "[]");
    return Array.isArray(rows)
      ? rows.filter(
          (x) =>
            x &&
            typeof x.session_id === "string" &&
            typeof x.product_code === "string" &&
            typeof x.mode === "string" &&
            x.scenario_id === null,
        )
      : [];
  } catch {
    return [];
  }
}
export interface AgentStoreOptions {
  /**
   * Minimum time the analysing panel stays visible before a result replaces it.
   * Pure presentation pacing: the local backend often answers in ~0.3s, too fast
   * for the request-stage line to be readable. Set 0 to reveal results instantly.
   */
  minimumPendingMs?: number;
}
export function createAgentStore(
  service: AgentQueryService,
  options: AgentStoreOptions = {},
) {
  const minimumPendingMs = options.minimumPendingMs ?? 1100;
  const messages = ref<Message[]>([]),
    selectedId = ref<number | null>(null),
    pending = ref(false),
    notice = ref(""),
    draft = ref("");
  const products = ref<Product[]>([]),
    productCode = ref(""),
    scopeMode = ref(""),
    session = ref<SessionBinding>(),
    knownSessions = ref(readIndex()),
    connecting = ref(false),
    connectionError = ref<QueryError>(),
    hasMore = ref(false),
    needsSync = ref(false),
    ready = ref(false);
  // Request phases actually executed by this browser, not invented model reasoning.
  const pendingStage = ref<TraceStageKey | "">(""),
    pendingStartedAt = ref(0);
  const controller = shallowRef<AbortController>();
  let sequence = 0,
    messageId = 0,
    after = 0,
    queryStarted = false;
  // Only a catalog actually shown in this conversation can define "第一个产品".
  let catalogOrder: string[] = [];
  let unresolvedQuestion = "";
  // A response can be lost after a transaction committed. Keep its identity
  // until a saved receipt is found; never turn recovery into a second write.
  // Uncertainty is bound to the session that produced it: another independent
  // session must stay usable instead of inheriting this one's pending state.
  type ExecutionIdentity = { sessionId: string; previewId: string; productCode: string };
  const unresolvedExecutions = new Map<string, ExecutionIdentity>();
  const uncertainSessions = new Set<string>();
  function currentSessionId() {
    return session.value?.session_id ?? "";
  }
  function unresolvedFor(sessionId?: string) {
    return sessionId ? unresolvedExecutions.get(sessionId) : undefined;
  }
  function markUncertain(sessionId: string, uncertain: boolean) {
    if (!sessionId) return;
    if (uncertain) uncertainSessions.add(sessionId);
    else uncertainSessions.delete(sessionId);
    refreshNeedsSync();
  }
  function refreshNeedsSync() {
    const id = currentSessionId();
    needsSync.value =
      !!id && (unresolvedExecutions.has(id) || uncertainSessions.has(id));
  }
  function receiptMatches(value: AgentResult, identity: ExecutionIdentity) {
    const backend = value.backend, operation = backend?.inventoryOperation;
    return backend?.sessionId === identity.sessionId && operation?.kind === "execution" &&
      operation.previewId === identity.previewId && operation.boundSessionId === identity.sessionId &&
      (value.product == null || value.product === identity.productCode);
  }
  const product = computed(() =>
    products.value.find((p) => p.product_code === productCode.value),
  );
  const scope = computed(() =>
    product.value?.modes.find((m) => m.mode === scopeMode.value),
  );
  const selected = computed(() =>
    messages.value.find((m) => m.id === selectedId.value),
  );
  const result = computed(() => selected.value?.result);
  const canSend = computed(
    () =>
      ready.value &&
      !pending.value &&
      !connecting.value &&
      !connectionError.value &&
      !needsSync.value &&
      !hasMore.value,
  );
  const recentSessions = computed(() =>
    knownSessions.value.filter(validBinding),
  );
  function validBinding(s: SessionBinding) {
    return (
      s.scenario_id === null &&
      products.value.some(
        (p) =>
          p.product_code === s.product_code &&
          p.modes.some((m) => m.mode === s.mode && m.scenario_id === null),
      )
    );
  }
  function remember() {
    if (!session.value) return;
    knownSessions.value = [
      session.value,
      ...knownSessions.value.filter(
        (s) => s.session_id !== session.value!.session_id,
      ),
    ].slice(0, 100);
    try {
      localStorage.setItem(indexKey, JSON.stringify(knownSessions.value));
      localStorage.setItem("pmc-active-session", session.value.session_id);
    } catch {
      /* Persistence is optional; requests still work. */
    }
  }
  function invalidate() {
    controller.value?.abort();
    sequence++;
    pending.value = false;
    pendingStage.value = "";
    pendingStartedAt.value = 0;
    connecting.value = false;
  }
  function cancel() {
    if (!pending.value) return;
    invalidate();
    const active = messages.value.find((m) => m.state === "pending");
    if (active) {
      active.state = "cancelled";
      draft.value = active.question;
    }
    markUncertain(currentSessionId(), queryStarted);
    notice.value = needsSync.value
      ? "已停止等待。后端可能已保存结果，请同步对话后继续。"
      : "已停止等待，未发送业务查询。可以修改问题后重试。";
  }
  function clearMessages() {
    messages.value = [];
    selectedId.value = null;
    notice.value = "";
    connectionError.value = undefined;
    hasMore.value = false;
    after = 0;
    unresolvedQuestion = "";
    catalogOrder = [];
    refreshNeedsSync();
  }
  function reset() {
    invalidate();
    clearMessages();
    session.value = undefined;
    productCode.value = "";
    scopeMode.value = "";
    draft.value = "";
    refreshNeedsSync();
    try {
      localStorage.removeItem("pmc-active-session");
    } catch {}
  }
  /** 清空对话历史：后端会话与本地索引一起清，回到全新状态。 */
  async function clearHistory() {
    reset();
    unresolvedExecutions.clear();
    uncertainSessions.clear();
    refreshNeedsSync();
    knownSessions.value = [];
    try {
      localStorage.removeItem(indexKey);
      localStorage.removeItem("pmc-active-session");
    } catch {}
    try {
      await service.clearSessions?.(new AbortController().signal);
    } catch {
      /* 本地已清空；后端不可用时不阻塞用户 */
    }
  }
  function errorOf(e: unknown) {
    return e instanceof QueryError
      ? e
      : new QueryError(
          "network",
          "请求失败，请检查后端连接后重试。",
          "CONNECTION_FAILED",
        );
  }
  async function initialize(force = false) {
    if (connecting.value || pending.value || (ready.value && !force)) return;
    const token = ++sequence,
      abort = new AbortController();
    controller.value = abort;
    connecting.value = true;
    connectionError.value = undefined;
    try {
      const catalog = await service.catalog!(abort.signal);
      if (token !== sequence) return;
      products.value = catalog.filter(
        (p) =>
          p.synthetic === false &&
          p.modes.some(
            (m) => m.mode === p.default_mode && m.scenario_id === null,
          ),
      );
      ready.value = true;
      if (session.value) {
        if (!validBinding(session.value))
          throw new QueryError(
            "unsupported",
            "当前会话的数据范围已不可用，请新建对话。",
            "ARCHIVED_CONFIGURATION",
          );
        return;
      }
      let active: string | null = null;
      try {
        active = localStorage.getItem("pmc-active-session");
      } catch {}
      const saved = recentSessions.value.find((s) => s.session_id === active);
      if (saved) {
        connecting.value = false;
        await restore(saved);
      } else if (active) {
        try {
          localStorage.removeItem("pmc-active-session");
        } catch {}
      }
    } catch (e) {
      if (token === sequence) connectionError.value = errorOf(e);
    } finally {
      if (token === sequence) connecting.value = false;
    }
  }
  async function restore(binding: SessionBinding) {
    if (pending.value || connecting.value || !validBinding(binding)) return;
    invalidate();
    clearMessages();
    session.value = { ...binding };
    productCode.value = binding.product_code;
    scopeMode.value = binding.mode;
    refreshNeedsSync();
    remember();
    await loadHistory();
  }
  async function loadHistory(more = false) {
    if (!session.value || connecting.value || pending.value) return;
    const token = ++sequence,
      abort = new AbortController();
    controller.value = abort;
    connecting.value = true;
    connectionError.value = undefined;
    try {
      const page = await service.history!(
        { ...session.value },
        more ? after : 0,
        abort.signal,
      );
      if (token !== sequence) return;
      const mapped = page.turns.map((t) => {
        const r = adaptBackendResponse(t.response);
        if (r.backend)
          r.backend.raw = {
            response: t.response,
            fact_references: t.fact_references,
          };
        return {
          id: ++messageId,
          question: t.user_query,
          requestedAt: t.created_at,
          state: "done" as const,
          result: r,
          turnId: t.turn_id,
        };
      });
      messages.value = more
        ? [
            ...messages.value,
            ...mapped.filter(
              (m) => !messages.value.some((o) => o.turnId === m.turnId),
            ),
          ]
        : mapped;
      selectedId.value = messages.value.at(-1)?.id ?? null;
      after = page.turns.at(-1)?.sequence_no ?? (more ? after : 0);
      hasMore.value = page.turns.length === 50;
      // Only this session's own uncertainty can be resolved or reported here.
      const activeSessionId = session.value.session_id;
      const unresolved = unresolvedFor(activeSessionId);
      const receipt = unresolved && messages.value.find((message) => message.result &&
        receiptMatches(message.result, unresolved) && message.result.backend!.inventoryOperation!.executed);
      if (receipt && unresolved) {
        unresolvedExecutions.delete(unresolved.sessionId);
        uncertainSessions.delete(unresolved.sessionId);
      }
      let recoveredFromLedger = false;
      const latest = messages.value.at(-1)?.result;
      const preview = latest?.backend?.inventoryOperation;
      // A refresh may lose an in-memory uncertainty flag. Check the latest
      // write-enabled preview's durable receipt before offering it again.
      const recovery = unresolved ?? (preview?.kind === "preview" && preview.executionAvailable &&
        preview.previewId && latest?.backend?.sessionId === session.value.session_id &&
        latest.product === session.value.product_code ? {
          sessionId: session.value.session_id, previewId: preview.previewId, productCode: session.value.product_code,
        } : undefined);
      if (!receipt && recovery && service.getInventoryReceipt && recovery.sessionId === session.value.session_id &&
          recovery.productCode === session.value.product_code) {
        const durable = await service.getInventoryReceipt({ session_id: recovery.sessionId, preview_id: recovery.previewId }, abort.signal);
        if (token !== sequence || abort.signal.aborted) return;
        if (!receiptMatches(durable, recovery))
          throw new QueryError("mapping", "库存回执与原会话或预览不一致，请继续核实。", "EXECUTION_BINDING_MISMATCH");
        if (durable.backend!.inventoryOperation!.executed) {
          const recovered: Message = { id: ++messageId, question: "核实本次库存执行回执",
            requestedAt: new Date().toLocaleTimeString("zh-CN", { hour12: false }), state: "done", result: durable,
            turnId: durable.backend?.turnId, inventoryExecution: true };
          messages.value.push(recovered);
          selectedId.value = recovered.id;
          unresolvedExecutions.delete(recovery.sessionId);
          uncertainSessions.delete(recovery.sessionId);
          recoveredFromLedger = true;
        }
      }
      refreshNeedsSync();
      session.value.conversation_id = page.conversation_id;
      session.value.turn_id = messages.value.at(-1)?.turnId;
      remember();
      notice.value = recoveredFromLedger
        ? "已从库存账本核实执行回执，没有重新执行；会话历史尚未同步时以该回执为准。"
        : needsSync.value
        ? "尚未找到本次库存执行回执，请继续核实；不会自动重新执行。"
        : hasMore.value
        ? "还有历史记录，加载完成后可继续提问。"
        : "已恢复对话，可以接着问。";
    } catch (e) {
      if (token === sequence) {
        connectionError.value = errorOf(e);
        markUncertain(session.value?.session_id ?? "", true);
      }
    } finally {
      if (token === sequence) connecting.value = false;
    }
  }
  function guidance(answer: string, title = "请补充产品信息"): AgentResult {
    return {
      id: `guide-${messageId}`,
      status: "text",
      product: null,
      quantity: null,
      quantityUnit: "",
      title,
      answer,
      conclusion: "",
      nextAction: "",
      materials: [],
      sources: [],
      warnings: [],
    };
  }
  function catalogResult(): AgentResult {
    catalogOrder = products.value.map((p) => p.product_code);
    const answer = products.value.length
      ? `当前可查询：${products.value.map((p, i) => `${i + 1}. ${p.product_code}${p.product_name ? `（${p.product_name}）` : ""}`).join("；")}。\n直接告诉我产品与问题即可，例如“${products.value[0]!.product_code} 的 BOM 是什么？”`
      : "当前没有可查询的已发布业务产品，请联系数据维护人员。";
    return {
      ...guidance(answer, "当前可查询的产品"),
      sources: [
        {
          id: "catalog",
          kind: "接口数据",
          title: "已发布产品目录",
          scope: "当前可访问的业务产品",
          reference: "GET /api/v1/agent/catalog",
          version: null,
          publishedAt: null,
          conditions: "由后端目录提供；仅展示非合成产品及其已发布业务范围。",
        },
      ],
    };
  }
  async function send(text = draft.value) {
    text = text.trim();
    if (!text || text.length > 2000 || !canSend.value) return;
    const id = ++messageId,
      token = ++sequence,
      abort = new AbortController();
    controller.value = abort;
    queryStarted = false;
    const startedAt = Date.now();
    messages.value.push({
      id,
      question: text,
      state: "pending",
      requestedAt: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
    });
    selectedId.value = id;
    pending.value = true;
    pendingStartedAt.value = startedAt;
    pendingStage.value = "resolve";
    notice.value = "";
    draft.value = "";
    const current = () => token === sequence && !abort.signal.aborted;
    // Presentation pacing only: a fast local backend must not hide the request stages.
    const pace = async () => {
      const remaining = minimumPendingMs - (Date.now() - startedAt);
      if (remaining > 0)
        await new Promise((resolve) => setTimeout(resolve, remaining));
    };
    async function finish(data: AgentResult) {
      await pace();
      if (!current()) return false;
      const message = messages.value.find((m) => m.id === id)!;
      message.result = data;
      message.state = "done";
      message.turnId = data.backend?.turnId;
      return true;
    }
    try {
      const resolved = await service.resolve!(
        text,
        session.value ? { ...session.value } : undefined,
        catalogOrder,
        abort.signal,
      );
      if (!current()) return;
      if (resolved.catalog_query) {
        await finish(catalogResult());
        return;
      }
      if (resolved.status !== "ok") {
        await finish(
          guidance(resolved.answer || "请提供明确的产品型号与问题。"),
        );
        return;
      }
      const p = products.value.find(
        (p) => p.product_code === resolved.product_code,
      );
      if (!p) {
        if (resolved.product_code) {
          await finish(
            guidance(
              "该产品不属于当前已发布业务范围。可以问“有哪些产品可以查询？”查看可用产品。",
              "当前产品不可用",
            ),
          );
        } else {
          unresolvedQuestion = text;
          await finish(
            guidance(
              "想分析哪个产品？请告诉我产品型号，我会接着处理刚才的问题。也可以问“有哪些产品可以查询？”",
            ),
          );
        }
        return;
      }
      const requestedMode =
        resolved.analysis_mode ||
        (session.value?.product_code === p.product_code
          ? scopeMode.value
          : p.default_mode);
      const m = p.modes.find(
        (m) => m.mode === requestedMode && m.scenario_id === null,
      );
      if (!m) {
        await finish(
          guidance(
            "该分析范围目前不可用。请使用产品当前已发布的 BOM 与库存数据。",
            "请明确分析范围",
          ),
        );
        return;
      }
      const executionText = unresolvedQuestion
        ? `${unresolvedQuestion}\n产品：${p.product_code}\n补充：${resolved.message || text}`
        : resolved.message || text;
      const switching =
        !!session.value &&
        (session.value.product_code !== p.product_code ||
          session.value.mode !== m.mode);
      if (!session.value || switching) {
        pendingStage.value = "session";
        const s = await service.createSession!(
          p.product_code,
          null,
          abort.signal,
        );
        if (!current()) return;
        if (switching) {
          messages.value = messages.value.filter((x) => x.id === id);
          after = 0;
          hasMore.value = false;
          catalogOrder = [];
          notice.value = `已切换到 ${p.product_code}，原分析保留在最近对话中。`;
        }
        productCode.value = p.product_code;
        scopeMode.value = m.mode;
        session.value = {
          ...s,
          product_code: p.product_code,
          mode: m.mode,
          scenario_id: null,
        };
        // The new session has its own uncertainty state; the previous session's
        // unresolved execution stays recorded against that session only.
        refreshNeedsSync();
        remember();
      }
      unresolvedQuestion = "";
      queryStarted = true;
      pendingStage.value = "query";
      const data = await service.query(
        { text: executionText, session: { ...session.value! } },
        abort.signal,
      );
      if (!current()) return;
      if (!(await finish(data))) return;
      if (session.value && data.backend) {
        session.value.session_id =
          data.backend.sessionId || session.value.session_id;
        session.value.conversation_id =
          data.backend.conversationId || session.value.conversation_id;
        session.value.turn_id = data.backend.turnId || session.value.turn_id;
        remember();
      }
    } catch (e) {
      await pace();
      if (!current()) return;
      const message = messages.value.find((m) => m.id === id)!;
      message.error = errorOf(e);
      message.state = "error";
      draft.value = text;
      if (
        [
          "SESSION_NOT_FOUND",
          "SESSION_CONFLICT",
          "ARCHIVED_CONFIGURATION",
        ].includes(message.error.backendCode || "") ||
        message.error.code === "network"
      ) {
        connectionError.value = message.error;
        markUncertain(session.value?.session_id ?? "", true);
      }
    } finally {
      if (token === sequence) {
        const message = messages.value.find((m) => m.id === id);
        if (message) message.elapsedMs = Math.max(0, Date.now() - startedAt);
        pending.value = false;
        pendingStage.value = "";
        pendingStartedAt.value = 0;
      }
    }
  }
  function select(id: number) {
    if (!pending.value) {
      selectedId.value = id;
      notice.value = "";
    }
  }
  async function executeInventory(credential: string, confirmed = false) {
    const previous = selected.value;
    const preview = previous?.result?.backend?.inventoryOperation;
    const binding = session.value ? { ...session.value } : undefined;
    const expiration = preview?.expiresAt ? Date.parse(preview.expiresAt) : NaN;
    if (!confirmed || !credential || !canSend.value || !service.executeInventory || !binding || !previous ||
        previous?.id !== messages.value.at(-1)?.id || preview?.kind !== "preview" ||
        !preview.canExecute || !preview.executionAvailable || !preview.previewId ||
        !Number.isFinite(expiration) || expiration <= Date.now() ||
        preview.boundSessionId !== binding.session_id ||
        previous.result?.backend?.sessionId !== binding.session_id ||
        previous.result?.product !== binding.product_code) {
      notice.value = "请先核对当前会话的最新有效预览；本次未发送库存执行请求。";
      return;
    }
    const id = ++messageId, token = ++sequence, abort = new AbortController();
    const previewId = preview.previewId;
    controller.value = abort;
    unresolvedExecutions.set(binding.session_id, {
      sessionId: binding.session_id, previewId, productCode: binding.product_code,
    });
    uncertainSessions.delete(binding.session_id);
    refreshNeedsSync();
    messages.value.push({ id, question: "确认执行本次库存预览", state: "pending",
      requestedAt: new Date().toLocaleTimeString("zh-CN", { hour12: false }), inventoryExecution: true });
    selectedId.value = id;
    pending.value = true;
    queryStarted = true;
    pendingStage.value = "query";
    pendingStartedAt.value = Date.now();
    notice.value = "";
    try {
      const data = await service.executeInventory({ session_id: binding.session_id,
        preview_id: previewId, confirmed: true, scenario_id: binding.scenario_id }, credential, abort.signal);
      if (token !== sequence || abort.signal.aborted) return;
      const knownRejection = data.backend && ["preview_expired", "stale_preview", "data_unavailable",
        "confirmation_required", "task_conflict", "invalid_query"].includes(data.backend.status) &&
        data.backend.inventoryOperation?.executed === false;
      // Current APIs bind all results. Legacy explicit rejections may omit data,
      // but must still belong to the session used by this one request.
      const legacyRejection = knownRejection && data.backend?.sessionId === binding.session_id &&
        !data.backend.inventoryOperation?.previewId;
      if (!data.backend || (!receiptMatches(data, { sessionId: binding.session_id, previewId, productCode: binding.product_code }) && !legacyRejection))
        throw new QueryError("mapping", "执行回执暂无法核实，请同步历史确认结果。", "EXECUTION_BINDING_MISMATCH");
      const message = messages.value.find((entry) => entry.id === id)!;
      message.result = data;
      message.state = "done";
      message.turnId = data.backend.turnId;
      unresolvedExecutions.delete(binding.session_id);
      uncertainSessions.delete(binding.session_id);
      refreshNeedsSync();
      session.value!.turn_id = data.backend.turnId || session.value!.turn_id;
      remember();
    } catch (error) {
      if (token !== sequence || abort.signal.aborted) return;
      const message = messages.value.find((entry) => entry.id === id)!;
      message.error = errorOf(error);
      message.state = "error";
      const status = message.error.httpStatus;
      const rejected = status !== undefined && [400, 401, 403, 404, 410, 422].includes(status);
      if (rejected) {
        // Explicit rejection is a definite outcome: no receipt to look up.
        unresolvedExecutions.delete(binding.session_id);
        uncertainSessions.delete(binding.session_id);
      }
      refreshNeedsSync();
      notice.value = rejected ? "库存执行未获通过，本次不会自动重试。"
        : "库存执行结果尚未确认，请同步历史核实；不会自动重试或创建新扣减任务。";
    } finally {
      // The component also clears its input before calling this method.
      credential = "";
      if (token === sequence) {
        pending.value = false;
        pendingStage.value = "";
        pendingStartedAt.value = 0;
      }
    }
  }
  function retry() {
    if (selected.value?.inventoryExecution || unresolvedFor(currentSessionId())) return loadHistory();
    if (selected.value) return send(selected.value.question);
  }
  return {
    messages,
    selected,
    selectedId,
    result,
    pending,
    pendingStage,
    pendingStartedAt,
    notice,
    draft,
    send,
    retry,
    cancel,
    reset,
    select,
    products,
    productCode,
    scopeMode,
    product,
    scope,
    session,
    recentSessions,
    connecting,
    connectionError,
    hasMore,
    needsSync,
    canSend,
    ready,
    initialize,
    restore,
    loadHistory,
    clearHistory,
    executeInventory,
  };
}
