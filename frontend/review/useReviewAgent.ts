// Browser persistence contains session pointers only; facts come from owned HTTP history.
import { computed, ref, shallowRef } from "vue";
import {
  QueryError,
  type AgentResult,
  type BackendTurn,
  type ScenarioId,
} from "../src/data/model";
import { createAgentClient } from "../src/services/apiClient";
import { adaptBackendResponse } from "../src/services/responseAdapter";
import { reviewRequest, type ReviewProduct } from "./reviewClient";
import { configureScenarios } from "./scenarioPolicy";
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
  session?: string;
}
interface Segment {
  session_id: string;
  product_code: string | null;
  analysis_mode: string;
  dataset_version: string | null;
  scenario_id: ScenarioId | null;
}
const chainKey = "pmc-authorized-review-conversation-v3";
const mode = ref("live"),
  scenario = ref<ScenarioId>(),
  catalog = ref<ReviewProduct[]>([]),
  productCode = ref<string | null>(null),
  analysisMode = ref("full");
const currentProduct = computed(() =>
  catalog.value.find((p) => p.product_code === productCode.value),
);
const currentScope = computed(() =>
  currentProduct.value?.modes.find((m) => m.mode === analysisMode.value),
);
const materials = ref<
  {
    material_code: string;
    name: string | null;
    qty_per: string;
    stock_qty: string | null;
    unit: string | null;
  }[]
>([]);
const sessionId = ref<string | null>(null),
  messages = ref<Message[]>([]),
  selectedId = ref<number | null>(null),
  pending = ref(false),
  notice = ref(""),
  draft = ref("");
const selected = computed(() =>
  messages.value.find((m) => m.id === selectedId.value),
);
const result = computed(() =>
  selected.value?.result?.conversation
    ? null
    : (selected.value?.result ?? null),
);
const controller = shallowRef<AbortController>();
const client = createAgentClient({ baseUrl: "/" });
let version = 0,
  messageId = 0,
  initialized = false,
  segments: Segment[] = [],
  catalogOrder: string[] = [];
function save() {
  try {
    sessionStorage.setItem(
      chainKey,
      JSON.stringify({ segments, active: sessionId.value, catalogOrder }),
    );
  } catch {
    /* HTTP works without persistence. */
  }
}
function check(metadata: any, s: Segment) {
  if (
    metadata.session_id !== s.session_id ||
    (metadata.review_product ?? null) !== s.product_code ||
    metadata.review_mode !== s.analysis_mode ||
    (metadata.dataset_version ?? null) !== s.dataset_version ||
    (metadata.scenario_id ?? null) !== s.scenario_id
  )
    throw new QueryError("session", "会话来源不一致，请新建分析。");
}
function activate(s: Segment) {
  sessionId.value = s.session_id;
  productCode.value = s.product_code;
  analysisMode.value = s.analysis_mode;
  scenario.value = s.scenario_id ?? undefined;
}
async function loadMaterials(s: Segment, token: number) {
  if (!s.product_code) return;
  try {
    const d = await reviewRequest(
      "/api/v1/review/products/" +
        encodeURIComponent(s.product_code) +
        "/materials?analysis_mode=" +
        encodeURIComponent(s.analysis_mode),
    );
    if (token !== version) return;
    if (
      d.analysis_mode !== s.analysis_mode ||
      d.dataset_version !== s.dataset_version
    )
      throw new Error("Scope mismatch");
    materials.value = d.materials;
  } catch {
    if (token === version) notice.value = "物料目录暂不可读取，请重试。";
  }
}
function cancel() {
  controller.value?.abort();
  version++;
  for (const m of messages.value.filter((m) => m.state === "pending")) {
    m.state = "cancelled";
    m.uncertain = true;
  }
  pending.value = false;
}
function reset() {
  cancel();
  sessionId.value = null;
  productCode.value = null;
  analysisMode.value = "full";
  scenario.value = undefined;
  messages.value = [];
  selectedId.value = null;
  materials.value = [];
  segments = [];
  catalogOrder = [];
  draft.value = "";
  notice.value = "";
  save();
}
async function chooseProduct(code: string, requestedMode?: string) {
  const p = catalog.value.find((p) => p.product_code === code),
    scope = p?.modes.find((m) => m.mode === (requestedMode ?? p.default_mode));
  if (!p || !scope) return false;
  cancel();
  const token = version;
  selectedId.value = null;
  pending.value = true;
  materials.value = [];
  const abort = new AbortController();
  controller.value = abort;
  try {
    const session = await reviewRequest(
      "/api/v1/agent/sessions",
      {
        product_code: code,
        analysis_mode: scope.mode,
        scenario_id: scope.scenario_id,
      },
      abort.signal,
    );
    const s: Segment = {
      session_id: session.session_id,
      product_code: code,
      analysis_mode: scope.mode,
      dataset_version: scope.dataset_version,
      scenario_id: scope.scenario_id,
    };
    check(session, s);
    if (token !== version || abort.signal.aborted) return false;
    segments.push(s);
    activate(s);
    save();
    await loadMaterials(s, token);
    if (token !== version) return false;
    notice.value = `已切换到${code}，历史分析仍保留。`;
    return true;
  } catch {
    if (token === version)
      notice.value = "产品切换未完成，当前产品没有改变。请重试。";
    return false;
  } finally {
    if (token === version) pending.value = false;
  }
}
async function initialize() {
  try {
    catalog.value = (await reviewRequest("/api/v1/review/catalog")).products;
    configureScenarios(
      catalog.value.flatMap((p) => p.modes.map((m) => m.scenario_id)),
    );
    const saved = JSON.parse(sessionStorage.getItem(chainKey) ?? "null");
    if (Array.isArray(saved?.segments)) {
      segments = saved.segments.filter((s: Segment) => catalog.value.some((p) =>
        p.product_code === s.product_code && p.modes.some((m) => m.mode === s.analysis_mode && m.dataset_version === s.dataset_version)));
      if (segments.length !== saved.segments.length) {
        sessionStorage.setItem(chainKey + ":archived:" + Date.now(), JSON.stringify(saved));
        notice.value = "旧BOM版本会话已归档；请新建当前产品分析，历史证据未修改。";
        save();
      }
      catalogOrder = Array.isArray(saved.catalogOrder)
        ? saved.catalogOrder
        : [];
      sessionId.value = segments.some((s) => s.session_id === saved.active) ? saved.active : segments.at(-1)?.session_id ?? null;
      await restoreSession();
    } else {
      const selection = JSON.parse(
          sessionStorage.getItem("pmc-authorized-review-selection-v2") ??
            "null",
        ),
        p = catalog.value.find(
          (p) => p.product_code === selection?.product_code,
        ),
        s = p?.modes.find(
          (m) =>
            m.mode === selection?.analysis_mode &&
            m.dataset_version === selection?.dataset_version,
        );
      if (p && s) {
        const pointer = JSON.parse(
          sessionStorage.getItem(
            "pmc-authorized-review-session-v2:" +
              p.product_code +
              ":" +
              s.mode +
              ":" +
              (s.dataset_version ?? "published"),
          ) ?? "null",
        );
        if (pointer?.session_id) {
          segments = [
            {
              session_id: pointer.session_id,
              product_code: p.product_code,
              analysis_mode: s.mode,
              dataset_version: s.dataset_version,
              scenario_id: s.scenario_id,
            },
          ];
          sessionId.value = pointer.session_id;
          await restoreSession();
        }
      }
    }
  } catch {
    notice.value = "无法读取授权产品目录，请检查登录及连接后刷新。";
  }
}
async function restoreSession(): Promise<BackendTurn[] | null> {
  if (pending.value) return null;
  if (!segments.length) return [];
  const token = ++version,
    abort = new AbortController();
  controller.value = abort;
  pending.value = true;
  const active = sessionId.value;
  try {
    const restored: Message[] = [],
      turns: BackendTurn[] = [];
    let activeSegment: Segment | undefined;
    for (const s of segments) {
      let after = 0;
      for (;;) {
        const page = await client.getSession(
          s.session_id,
          s.scenario_id ?? undefined,
          after,
          abort.signal,
        );
        check(page, s);
        if (s.session_id === active) activeSegment = s;
        for (const t of page.turns) {
          if (
            t.sequence_no <= after ||
            t.response.session_id !== s.session_id ||
            (t.response.scenario_id ?? null) !== s.scenario_id
          )
            throw new QueryError("session", "会话记录来源不一致。");
          after = t.sequence_no;
          turns.push(t);
          restored.push({
            id: ++messageId,
            question: t.user_query,
            requestedAt: t.created_at,
            sequenceNo: t.sequence_no,
            session: s.session_id,
            state: "done",
            result: adaptBackendResponse(t.response, t.user_query),
          });
        }
        if (page.turns.length < 200) break;
      }
    }
    if (token !== version || abort.signal.aborted) return null;
    if (activeSegment) activate(activeSegment);
    messages.value = restored;
    selectedId.value = restored.at(-1)?.id ?? null;
    save();
    if (activeSegment) await loadMaterials(activeSegment, token);
    notice.value = "已恢复当前账号的对话及各产品历史结果。";
    return turns;
  } catch (e) {
    if (token !== version || abort.signal.aborted) return null;
    if (e instanceof QueryError && e.code === "session") {
      segments = [];
      sessionId.value = null;
      productCode.value = null;
      messages.value = [];
      selectedId.value = null;
      save();
    }
    notice.value =
      "暂时无法恢复会话，请确认账号、数据版本及连接；不会重新计算历史结果。";
    return null;
  } finally {
    if (token === version) pending.value = false;
  }
}
async function send(candidate: unknown = draft.value) {
  const text = typeof candidate === "string" ? candidate.trim() : "";
  if (!text || pending.value) return;
  let resolved: any;
  const resolvingToken = ++version;
  pending.value = true;
  try {
    resolved = await reviewRequest("/api/v1/review/resolve", {
      message: text,
      session_id: sessionId.value,
      catalog_order: catalogOrder,
    });
    if (resolvingToken !== version) return;
    if (resolved.status !== "ok") {
      const r = {
        intent: "clarification",
        status: resolved.status,
        answer: resolved.answer,
        structured_data: { data: null, completed_steps: [] },
        provenance: null,
        warnings: [],
      };
      const m: Message = {
        id: ++messageId,
        question: text,
        requestedAt: new Date().toLocaleTimeString("zh-CN"),
        state: "done",
        result: adaptBackendResponse(r, text),
      };
      messages.value.push(m);
      selectedId.value = m.id;
      return;
    }
  } catch {
    if (resolvingToken === version) {
      notice.value = "问题中的产品不可访问，或连接暂时异常。";
      const m: Message = {
        id: ++messageId,
        question: text,
        requestedAt: new Date().toLocaleTimeString("zh-CN"),
        state: "error",
        error: new QueryError("invalid", "产品不可访问，或连接暂时异常。"),
      };
      messages.value.push(m);
      selectedId.value = m.id;
    }
    return;
  } finally {
    if (resolvingToken === version) pending.value = false;
  }
  const target =
    resolved.product_code ??
    productCode.value ??
    (resolved.analysis_mode ? catalog.value[0]?.product_code : null);
  if (
    !resolved.catalog_query &&
    target &&
    (target !== productCode.value ||
      (resolved.analysis_mode && resolved.analysis_mode !== analysisMode.value))
  ) {
    if (!(await chooseProduct(target, resolved.analysis_mode ?? undefined)))
      return;
  }
  if (!sessionId.value && !resolved.catalog_query) {
    notice.value = "请先查看产品目录，或告诉我产品编码。";
    return;
  }
  const token = ++version,
    abort = new AbortController();
  controller.value = abort;
  const s = segments.find((s) => s.session_id === sessionId.value);
  const m: Message = {
    id: ++messageId,
    question: text,
    requestedAt: new Date().toLocaleTimeString("zh-CN"),
    state: "pending",
    session: sessionId.value ?? undefined,
    attemptAfter: Math.max(
      0,
      ...messages.value
        .filter((m) => m.session === sessionId.value)
        .map((m) => m.sequenceNo ?? 0),
    ),
  };
  messages.value.push(m);
  selectedId.value = m.id;
  pending.value = true;
  draft.value = "";
  notice.value = "";
  try {
    const r = await client.queryAgent(
      {
        message: text,
        session_id: sessionId.value ?? undefined,
        scenario_id: scenario.value,
        persist_session: true,
        ...(/第([一二三四五六七八九]|\d+)个产品/.test(text)
          ? { catalog_order: catalogOrder }
          : {}),
      },
      abort.signal,
    );
    if (s) check(r, s);
    if (abort.signal.aborted) return;
    const current = messages.value.find((item) => item.id === m.id);
    if (!current) return;
    current.result = adaptBackendResponse(r, text);
    current.state = "done";
    current.sequenceNo = (m.attemptAfter ?? 0) + 1;
    if (token !== version) return;
    if (resolved.catalog_query) {
      const shown = r.structured_data?.data as
        { products?: { product_code: string }[] } | undefined;
      catalogOrder = (shown?.products ?? []).map((p) => p.product_code);
      if (!s && r.session_id) {
        const catalogSegment: Segment = {
          session_id: r.session_id,
          product_code: r.review_product ?? null,
          analysis_mode: r.review_mode ?? "full",
          dataset_version: r.dataset_version ?? null,
          scenario_id: r.scenario_id ?? null,
        };
        segments.push(catalogSegment);
        current.session = r.session_id;
      }
    }
    save();
  } catch (e) {
    if (token !== version || abort.signal.aborted) return;
    const current = messages.value.find((item) => item.id === m.id);
    if (current) {
      current.error =
        e instanceof QueryError
          ? e
          : new QueryError("network", "请求失败，请检查连接后重试。");
      current.state = "error";
      current.uncertain = true;
    }
    draft.value = text;
  } finally {
    if (token === version) pending.value = false;
  }
}
async function retry() {
  const m = selected.value;
  if (!m || pending.value) return;
  if (m.uncertain) {
    const turns = await restoreSession();
    if (turns === null) return;
    if (
      turns.some(
        (t) =>
          t.user_query === m.question && t.sequence_no > (m.attemptAfter ?? 0),
      )
    )
      return;
  }
  return send(m.question);
}
export function useAgent() {
  if (!initialized) {
    initialized = true;
    void initialize();
  }
  return {
    catalog,
    productCode,
    currentProduct,
    currentScope,
    analysisMode,
    materials,
    chooseProduct,
    mode,
    scenario,
    sessionId,
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
