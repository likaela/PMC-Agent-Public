// Dedicated state: no enterprise preference, scene list, offline data or router.
import { computed, ref, shallowRef } from "vue";
import {
  QueryError,
  type AgentResult,
  type BackendTurn,
} from "../src/data/model";
import { createAgentClient } from "../src/services/apiClient";
import { adaptBackendResponse } from "../src/services/responseAdapter";
import { trialScenario } from "./scenarioPolicy";

interface Message {
  id: number;
  question: string;
  requestedAt: string;
  state: "pending" | "done" | "error" | "cancelled";
  result?: AgentResult;
  error?: QueryError;
  sequenceNo?: number;
  attemptAfter?: number;
  uncertain?: boolean;
}
const mode = ref("live"),
  scenario = ref(trialScenario);
const sessionId = ref<string | null>(null),
  messages = ref<Message[]>([]);
const activeProduct = ref<string | null>(null);
const selectedId = ref<number | null>(null),
  pending = ref(false);
const notice = ref(""),
  draft = ref("");
const controller = shallowRef<AbortController>();
const client = createAgentClient({ baseUrl: "/" });
const key = "pmc-synthetic-trial-session-v1";
let version = 0,
  messageId = 0,
  initialized = false;
const selected = computed(() =>
  messages.value.find((m) => m.id === selectedId.value),
);
const result = computed(() =>
  selected.value?.result?.conversation
    ? null
    : (selected.value?.result ?? null),
);
function save() {
  try {
    if (sessionId.value)
      sessionStorage.setItem(
        key,
        JSON.stringify({
          session_id: sessionId.value,
          scenario_id: trialScenario,
        }),
      );
    else sessionStorage.removeItem(key);
  } catch {
    /* Storage unavailable: live HTTP still works. */
  }
}
function cancel() {
  if (!pending.value) return;
  controller.value?.abort();
  version++;
  const message = messages.value.find((m) => m.state === "pending");
  if (message) {
    message.state = "cancelled";
    message.uncertain = true;
  }
  pending.value = false;
  notice.value = "已停止等待，请恢复会话确认服务端结果。";
}
function reset() {
  cancel();
  version++;
  sessionId.value = null;
  activeProduct.value = null;
  save();
  messages.value = [];
  selectedId.value = null;
  draft.value = "";
  notice.value = "";
}
async function restoreSession(): Promise<BackendTurn[] | null> {
  if (pending.value) return null;
  if (!sessionId.value) {
    try {
      const pointer = JSON.parse(sessionStorage.getItem(key) ?? "null");
      if (
        pointer?.scenario_id === trialScenario &&
        typeof pointer.session_id === "string"
      )
        sessionId.value = pointer.session_id;
    } catch {
      /* Ignore malformed pointers, never select another data space. */
    }
  }
  if (!sessionId.value) return [];
  const restoring = sessionId.value,
    token = ++version;
  const abort = new AbortController();
  controller.value = abort;
  pending.value = true;
  try {
    const turns: BackendTurn[] = [];
    let product: string | null = null;
    let after = 0;
    for (;;) {
      const page = await client.getSession(
        restoring,
        trialScenario,
        after,
        abort.signal,
      );
      if (page.session_id !== restoring || page.scenario_id !== trialScenario)
        throw new QueryError("session", "会话来源不一致，请新建分析。");
      const task = page.context.production as
        { material_code?: string } | undefined;
      product =
        typeof task?.material_code === "string" ? task.material_code : null;
      for (const turn of page.turns) {
        if (
          turn.sequence_no <= after ||
          turn.response.session_id !== restoring ||
          turn.response.scenario_id !== trialScenario
        )
          throw new QueryError("mapping", "会话记录暂无法确认。");
        after = turn.sequence_no;
        turns.push(turn);
      }
      if (page.turns.length < 200) break;
    }
    if (token !== version || abort.signal.aborted) return null;
    activeProduct.value = product;
    messages.value = turns.map((t) => ({
      id: ++messageId,
      question: t.user_query,
      requestedAt: t.created_at,
      sequenceNo: t.sequence_no,
      state: "done",
      result: adaptBackendResponse(t.response, t.user_query),
    }));
    selectedId.value = messages.value.at(-1)?.id ?? null;
    notice.value = "已恢复当前账号的演示产品会话。";
    return turns;
  } catch (error) {
    if (token !== version || abort.signal.aborted) return null;
    if (error instanceof QueryError && error.code === "session") {
      sessionId.value = null;
      activeProduct.value = null;
      save();
      messages.value = [];
      selectedId.value = null;
      notice.value = "会话已失效或不属于当前账号，请新建分析。";
    } else notice.value = "暂时无法恢复会话，请检查连接后重试。";
    return null;
  } finally {
    if (token === version) pending.value = false;
  }
}
async function send(candidate: unknown = draft.value) {
  const text = typeof candidate === "string" ? candidate.trim() : "";
  if (!text || pending.value) return;
  const token = ++version,
    abort = new AbortController();
  controller.value = abort;
  const message: Message = {
    id: ++messageId,
    question: text,
    requestedAt: new Date().toLocaleTimeString("zh-CN"),
    state: "pending",
    attemptAfter: messages.value.at(-1)?.sequenceNo ?? 0,
  };
  messages.value.push(message);
  selectedId.value = message.id;
  pending.value = true;
  notice.value = "";
  draft.value = "";
  try {
    if (!sessionId.value) {
      const session = await client.createSession(trialScenario, abort.signal);
      if (token !== version || abort.signal.aborted) return;
      if (session.scenario_id !== trialScenario)
        throw new QueryError("mapping", "会话来源不一致，未发送分析请求。");
      sessionId.value = session.session_id;
      const task = session.context.production as
        { material_code?: string } | undefined;
      activeProduct.value =
        typeof task?.material_code === "string" ? task.material_code : null;
      save();
    }
    const response = await client.queryAgent(
      {
        message: text,
        session_id: sessionId.value,
        scenario_id: trialScenario,
        persist_session: true,
      },
      abort.signal,
    );
    if (token !== version || abort.signal.aborted) return;
    if (
      response.session_id !== sessionId.value ||
      response.scenario_id !== trialScenario
    )
      throw new QueryError("mapping", "会话来源不一致，请恢复后重试。");
    const vm = adaptBackendResponse(response, text);
    const current = messages.value.find((m) => m.id === message.id)!;
    current.result = vm;
    current.state = "done";
    current.sequenceNo = (message.attemptAfter ?? 0) + 1;
    save();
  } catch (error) {
    if (token !== version || abort.signal.aborted) return;
    const current = messages.value.find((m) => m.id === message.id)!;
    current.error =
      error instanceof QueryError
        ? error
        : new QueryError("network", "请求失败，请检查连接后重试。");
    current.state = "error";
    current.uncertain = true;
    draft.value = text;
  } finally {
    if (token === version) pending.value = false;
  }
}
async function retry() {
  const previous = selected.value;
  if (!previous || pending.value) return;
  if (previous.uncertain && sessionId.value) {
    const turns = await restoreSession();
    if (turns === null) return;
    if (
      turns.some(
        (t) =>
          t.sequence_no > (previous.attemptAfter ?? 0) &&
          t.user_query === previous.question,
      )
    )
      return;
  }
  return send(previous.question);
}
export function useAgent() {
  if (!initialized) {
    initialized = true;
    void restoreSession();
  }
  return {
    mode,
    scenario,
    sessionId,
    activeProduct,
    messages,
    selectedId,
    selected,
    result,
    pending,
    notice,
    draft,
    send,
    retry,
    cancel,
    reset,
    restoreSession,
    select: (id: number) => {
      if (!pending.value) selectedId.value = id;
    },
  };
}
