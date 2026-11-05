import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve, extname, basename, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
const frontend = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dependencies = process.env.PMC_FRONTEND_DEPENDENCY_ROOT || frontend;
const dependency = createRequire(join(dependencies, "package.json"));
const ts = dependency("typescript"), vue = dependency("vue");
const { parse, compileScript } = dependency("@vue/compiler-sfc");
const { renderToString } = dependency("@vue/server-renderer");
const actual = new Set(["ResultPanel.vue", "InventoryGapChart.vue", "ShortageBarChart.vue", "InventoryTrendChart.vue", "ChartAiInterpretation.vue", "AgentExecutionTrace.vue", "RecommendationPanel.vue"]);
function harness() {
  const state = { pending: vue.ref(false), selected: vue.ref(null), session: vue.ref(undefined), messages: vue.ref([]), canSend: vue.ref(true), retry() { throw Error("Render cannot retry"); }, executeInventory() { throw Error("Render cannot execute"); } };
  state.result = vue.computed(() => state.selected.value?.result);
  const cache = new Map();
  function load(filename) {
    filename = resolve(filename); if (!extname(filename)) filename += ".ts";
    if (cache.has(filename)) return cache.get(filename);
    if (filename.endsWith(".css")) return {};
    if (filename.endsWith(".vue") && !actual.has(basename(filename))) return { default: vue.defineComponent({ setup(_, { slots }) { return () => vue.h("span", slots.default?.()); } }) };
    let source = readFileSync(filename, "utf8");
    if (filename.endsWith(".vue")) { const { descriptor, errors } = parse(source, { filename }); assert.deepEqual(errors, []); source = compileScript(descriptor, { id: "chart-ai-" + basename(filename), inlineTemplate: true }).content; }
    const output = ts.transpileModule(source.replaceAll("import.meta.env", '({ VITE_STREAMLIT: "false" })'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
    const module = { exports: {} };
    new Function("require", "module", "exports", output)((name) => {
      if (name === "vue") return vue;
      if (name === "./streamlitBridge") return { streamlitRequest() { throw Error("Bridge disabled in this test"); } };
      if (name === "../composables/useAgent") return { useAgent: () => state };
      if (name.startsWith(".")) return load(resolve(dirname(filename), name));
      return dependency(name);
    }, module, module.exports);
    cache.set(filename, module.exports); return module.exports;
  }
  const adapt = load(resolve(frontend, "src/services/responseAdapter.ts")).adaptBackendResponse;
  const Panel = load(resolve(frontend, "src/components/ResultPanel.vue")).default;
  function select(input, id = 1, restored = false) { const result = adapt(input); if (restored) result.backend.raw = { response: input, fact_references: [] }; const message = { id, question: "Synthetic question", state: "done", result }; state.selected.value = message; state.messages.value = [message]; return result; }
  const render = () => renderToString(vue.createSSRApp({ render: () => vue.h(Panel) }));
  const component = (name, props) => renderToString(vue.createSSRApp({ render: () => vue.h(load(resolve(frontend, "src/components/" + name + ".vue")).default, props) }));
  return { state, adapt, select, render, component, module: name => load(resolve(frontend, name)) };
}
function payload() { return { intent: "production_capacity", status: "ok", answer: "本次10片需求有1项不足。", session_id: "SYNTHETIC-SESSION", structured_data: { query_type: "production_capacity", presentation: { result_type: "production", user_warnings: [] }, data: { product: "TEST-PRODUCT", revision: "V01", requested_quantity: 10, product_unit: "片", inventory_sufficient_for_requested: false, materials: [{ material_code: "TEST-MAT-A", name: "测试物料", unit: "片", required_qty: "20.00", recorded_stock_qty: "12.00", stock_qty_in_bom_unit: "12.00", shortage_qty: "8.00", comparison_status: "comparable", quantity_status: "shortage", record_found: true }] }, chart_ai: { status: "ok", source: "llm", sections: { conclusion: "本次需求存在物料约束。", findings: ["TEST-MAT-A需求20.00片，库存12.00片，缺口8.00片。"], impact: "当前库存不能满足10片需求。", actions: ["核实TEST-MAT-A的库存及在途情况。"] } }, execution_trace: [{ stage: "llm_understanding", status: "ok", duration_ms: 125, summary: "识别生产需求核对任务。", details: { product_code: "TEST-PRODUCT", quantity: 10 } }, { stage: "tools", status: "ok", duration_ms: 5, summary: "已执行BOM与库存核对。", details: { tool_name: "enterprise_book_demand_analysis" } }] }, warnings: [] }; }
test("AI interpretation follows the comparison chart and precedes the shortage list", async () => { const h = harness(); h.select(payload()); const html = await h.render(); assert.ok(html.indexOf('class="gap-chart"') < html.indexOf('class="chart-ai"')); assert.ok(html.indexOf('</svg>') < html.indexOf('class="chart-ai"')); assert.ok(html.indexOf('class="chart-ai"') < html.indexOf('class="shortage-chart"')); for (const title of ["核心结论", "关键物料发现", "生产计划影响", "后续核实建议"]) assert.ok(html.includes(title)); assert.match(html, /20\.00片，库存12\.00片，缺口8\.00片/); });
test("execution process displays only real supplied backend events", async () => { const h = harness(); h.select(payload()); const html = await h.render(); assert.match(html, /Agent 执行过程/); assert.match(html, /DeepSeek 理解/); assert.match(html, /enterprise_book_demand_analysis/); assert.match(html, /TEST-PRODUCT/); assert.doesNotMatch(html, /思维链|第一优先采购/); });
test("legacy responses do not acquire invented AI interpretation or execution events", async () => { const h = harness(), p = payload(); delete p.structured_data.chart_ai; delete p.structured_data.execution_trace; h.select(p); const html = await h.render(); assert.doesNotMatch(html, /class="chart-ai"|Agent 执行过程/); });
for (const status of ["loading", "unavailable", "not_applicable"]) test("chart AI status " + status + " remains explicit", async () => { const h = harness(), p = payload(); p.structured_data.chart_ai = { status, error_code: "SAFE_TEST" }; h.select(p); const html = await h.render(); if (status === "not_applicable") assert.doesNotMatch(html, /class="chart-ai"/); else { assert.match(html, /AI 分析解读/); assert.match(html, status === "loading" ? /正在生成分析解读/ : /分析解读暂未完成/); assert.doesNotMatch(html, /SAFE_TEST|TEST-MAT-A需求20/); } });
test("malformed or non-model interpretation cannot masquerade as DeepSeek success", async () => { const h = harness(), p = payload(); p.structured_data.chart_ai.sections.findings = "invalid"; h.select(p); assert.match(await h.render(), /分析解读暂未完成/); p.structured_data.chart_ai = { status: "ok", source: "deterministic", sections: payload().structured_data.chart_ai.sections }; h.select(p); assert.match(await h.render(), /分析解读暂未完成/); });
test("model failure cannot show an old chart or interpretation", async () => { const h = harness(), p = payload(); p.status = "model_unavailable"; p.answer = "问题理解未完成，请重试。"; h.select(p); const html = await h.render(); assert.match(html, /问题理解未完成/); assert.doesNotMatch(html, /class="chart-ai"|class="gap-chart"|class="shortage-chart"/); });
test("pending query hides the previous chart interpretation", async () => { const h = harness(); h.select(payload()); h.state.pending.value = true; assert.doesNotMatch(await h.render(), /TEST-MAT-A需求|class="chart-ai"/); });
test("product switching and restored history retain the selected interpretation", async () => { const h = harness(), first = payload(), second = payload(); second.structured_data.chart_ai.sections.conclusion = "SECOND-RESULT"; second.structured_data.data.product = "TEST-PRODUCT-B"; h.select(second, 2); assert.match(await h.render(), /SECOND-RESULT/); h.select(first, 1, true); const html = await h.render(); assert.match(html, /本次需求存在物料约束/); assert.doesNotMatch(html, /SECOND-RESULT/); });
test("unknown stock differs from confirmed shortage in summary cards", () => { const h = harness(), p = payload(), d = p.structured_data.data; d.missing_stock_material_count = 1; d.materials[0] = { ...d.materials[0], record_found: false, comparison_status: "no_stock", quantity_status: "unknown", shortage_qty: null }; d.materials.push({ ...payload().structured_data.data.materials[0], material_code: "TEST-KNOWN", quantity_status: "sufficient", shortage_qty: "0.00" }); const result = h.adapt(p); assert.equal(result.backend.metrics.find(x => x.label === "需求满足").value, "已核算部分满足"); });
test("actual shortage remains visible even when other materials have no records", () => { const h = harness(), p = payload(); p.structured_data.data.missing_stock_material_count = 1; assert.equal(h.adapt(p).backend.metrics.find(x => x.label === "需求满足").value, "存在缺口"); });
test("analysis and event text is escaped rather than treated as HTML", async () => { const h = harness(), p = payload(); p.structured_data.chart_ai.sections.conclusion = '<script>BAD()</script>'; h.select(p); const html = await h.render(); assert.doesNotMatch(html, /<script>BAD/); assert.match(html, /&lt;script&gt;/); });
test("rendering leaves source response and numerical strings unchanged", async () => { const h = harness(), p = payload(), before = structuredClone(p); h.select(p); await h.render(); assert.deepEqual(p, before); });
test("business recommendations use the supplied next steps without a disclaimer", async () => { const h = harness(); const result = h.select(payload()); const html = await h.component("RecommendationPanel", { result }); assert.match(html, /核实TEST-MAT-A的库存及在途情况/); assert.doesNotMatch(html, /仅供参考|不保证|不承担责任|第一优先采购/); });
test("malformed events and private diagnostics are not fabricated into execution stages", async () => { const h = harness(), p = payload(); p.structured_data.execution_trace = [{ stage: "fake", status: "ok" }, { stage: "llm_understanding", status: "ok", duration_ms: -1, summary: "已理解", details: { api_key: "NEVER-RENDER", chain_of_thought: "NEVER-RENDER" } }]; const result = h.select(p); assert.equal(result.backend.executionTrace.length, 1); const html = await h.render(); assert.doesNotMatch(html, /NEVER-RENDER|-1ms/); });

test("no matched inventory cannot claim even a partially satisfied result", () => { const h = harness(), p = payload(), d = p.structured_data.data; d.missing_stock_material_count = 1; d.materials[0] = { ...d.materials[0], record_found: false, comparison_status: "no_stock", quantity_status: "unknown", shortage_qty: null }; assert.equal(h.adapt(p).backend.metrics.find(x => x.label === "需求满足").value, "库存待匹配"); });

function browserStorage() { const memory = new Map(); globalThis.localStorage = { getItem: key => memory.get(key) ?? null, setItem: (key, value) => memory.set(key, String(value)), removeItem: key => memory.delete(key) }; }
async function preparedAiStore(resolution, queryResult = payload(), options = {}) {
  browserStorage(); const h = harness(), calls = [];
  const service = {
    catalog: async () => [{ product_code: "TEST-PRODUCT", product_name: "Synthetic fixture", synthetic: false, default_mode: "current", modes: [{ mode: "current", scenario_id: null }] }],
    resolve: async (text, session, catalogOrder) => { calls.push({ method: "resolve", text, session, catalogOrder }); if (options.resolveError) throw options.resolveError; return typeof resolution === "function" ? resolution(text) : resolution; },
    createSession: async () => { calls.push({ method: "session" }); return { session_id: options.productSessionId || "SYNTHETIC-SESSION", conversation_id: "SYNTHETIC-CONVERSATION" }; },
    query: async input => { calls.push({ method: "query", input }); if (options.queryError) throw options.queryError; return h.adapt(options.queryResult ? options.queryResult(input) : queryResult); },
    history: async binding => { calls.push({ method: "history", binding }); return { session_id: binding.session_id, conversation_id: "SYNTHETIC-CONVERSATION", turns: options.history?.[binding.session_id] || [] }; },
  };
  const store = h.module("src/composables/agentStore.ts").createAgentStore(service, { minimumPendingMs: 0 });
  await store.initialize(); return { store, calls, h, service };
}
test("client sends the server understanding reference to the real same-origin query endpoint", async () => {
  const h = harness(), requests = []; const previousFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => { requests.push({ url, init }); return new Response(JSON.stringify(payload()), { status: 200 }); };
  try { const service = h.module("src/services/apiClient.ts").createLiveService();
    await service.query({ text: "有哪些产品", understandingId: "SYNTHETIC-TICKET" }, new AbortController().signal);
    assert.deepEqual(requests.map(x => x.url), ["/api/v1/agent/query"]);
    assert.deepEqual(JSON.parse(requests[0].init.body), { message: "有哪些产品", session_id: null, persist_session: true, scenario_id: null, understanding_id: "SYNTHETIC-TICKET" });
    assert.equal(requests[0].init.headers.Authorization, undefined);
  } finally { globalThis.fetch = previousFetch; }
});
test("AI-enabled catalog uses the server answer and trace instead of a local catalog response", async () => {
  const p = payload(); p.answer = "SERVER-CATALOG-ANSWER"; p.structured_data.presentation.result_type = "catalog"; p.structured_data.data = { products: [] };
  const { store, calls } = await preparedAiStore({ status: "ok", product_code: null, catalog_query: true, understanding_id: "TICKET-CATALOG" }, p);
  await store.send("有哪些产品");
  assert.deepEqual(calls.map(x => x.method), ["resolve", "query"]);
  assert.equal(store.selected.value.result.answer, "SERVER-CATALOG-ANSWER");
  assert.equal(calls[1].input.understandingId, "TICKET-CATALOG"); assert.equal(calls[1].input.session, undefined);
  assert.equal(store.selected.value.result.backend.executionTrace.length, 2);
});
for (const resolution of [
  { status: "ok", product_code: null, catalog_query: false },
  { status: "needs_input", product_code: null, catalog_query: false },
  { status: "ok", product_code: "UNKNOWN-PRODUCT", catalog_query: false },
]) test("AI-enabled greeting, clarification or unknown product is answered by the backend: " + resolution.status + "/" + resolution.product_code, async () => {
  const p = payload(); p.answer = "SERVER-CONTROLLED-ANSWER";
  const { store, calls } = await preparedAiStore({ ...resolution, understanding_id: "TICKET-CLARIFY" }, p);
  await store.send("你好，帮我看看");
  assert.deepEqual(calls.map(x => x.method), ["resolve", "query"]); assert.equal(store.selected.value.result.answer, "SERVER-CONTROLLED-ANSWER");
});
test("AI ticket preserves original user text rather than submitting the resolver's rewritten message", async () => {
  const { store, calls } = await preparedAiStore({ status: "ok", product_code: "TEST-PRODUCT", catalog_query: false, message: "REWRITTEN-NORMALIZATION", understanding_id: "TICKET-ORIGINAL" });
  await store.send("原始问题：生产10片");
  assert.deepEqual(calls.map(x => x.method), ["resolve", "session", "query"]);
  assert.equal(calls[2].input.text, "原始问题：生产10片"); assert.equal(calls[2].input.understandingId, "TICKET-ORIGINAL");
});
test("failed model understanding cannot become a local successful answer", async () => {
  const { store, calls } = await preparedAiStore({}, payload(), { resolveError: Error("Synthetic timeout") });
  await store.send("生产10片"); assert.equal(store.selected.value.state, "error"); assert.equal(store.selected.value.result, undefined);
  assert.deepEqual(calls.map(x => x.method), ["resolve"]);
});
test("query failure after understanding cannot fall back to a local catalog answer", async () => {
  const { store, calls } = await preparedAiStore({ status: "ok", product_code: null, catalog_query: true, understanding_id: "TICKET-ERROR" }, payload(), { queryError: Error("Synthetic query failure") });
  await store.send("有哪些产品"); assert.equal(store.selected.value.state, "error"); assert.equal(store.selected.value.result, undefined);
  assert.deepEqual(calls.map(x => x.method), ["resolve", "query"]);
});
test("client rejects an explicit model-unavailable resolution without inventing a product", async () => {
  const h = harness(), previousFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ status: "model_unavailable", product_code: null, answer: "问题理解未完成，请重试。" }), { status: 200 });
  try { await assert.rejects(h.module("src/services/apiClient.ts").createLiveService().resolve("生产10片", undefined, [], new AbortController().signal), error => error.backendCode === "MODEL_UNAVAILABLE"); }
  finally { globalThis.fetch = previousFetch; }
});

function catalogPayload() { const p = payload(); p.session_id = "SYNTHETIC-CATALOG-SESSION"; p.turn_id = "SYNTHETIC-CATALOG-TURN"; p.answer = "可查询TEST-PRODUCT。"; p.structured_data.presentation.result_type = "catalog"; p.structured_data.data = { products: [{ product_code: "TEST-PRODUCT", product_name: "Synthetic fixture", revisions: [{ revision: "V01" }] }] }; p.structured_data.chart_ai = { status: "not_applicable" }; return p; }
function historyTurn(response, question) { return { turn_id: response.turn_id || "SYNTHETIC-TURN", sequence_no: 1, user_query: question, created_at: "2026-11-05T12:00:00+08:00", response, fact_references: [] }; }
test("an initial catalog session is saved and refresh restores its own execution trace", async () => {
  const p = catalogPayload(), options = { history: { [p.session_id]: [historyTurn(p, "有哪些产品")] } };
  const { store, calls, h, service } = await preparedAiStore({ status: "ok", product_code: null, catalog_query: true, understanding_id: "TICKET-CATALOG" }, p, options);
  await store.send("有哪些产品");
  assert.equal(store.session.value.session_id, p.session_id); assert.equal(store.session.value.product_code, ""); assert.equal(store.session.value.mode, "catalog");
  assert.equal(store.recentSessions.value[0].session_id, p.session_id);
  const refreshed = h.module("src/composables/agentStore.ts").createAgentStore(service, { minimumPendingMs: 0 });
  await refreshed.initialize();
  assert.equal(refreshed.messages.value.length, 1); assert.equal(refreshed.selected.value.question, "有哪些产品");
  assert.equal(refreshed.selected.value.result.backend.executionTrace.length, 2);
  assert.equal(refreshed.selected.value.result.backend.chartAi.status, "not_applicable");
  await refreshed.send("还有哪些产品");
  const resolved = calls.filter(x => x.method === "resolve").at(-1);
  assert.equal(resolved.session.session_id, p.session_id); assert.deepEqual(resolved.catalogOrder, ["TEST-PRODUCT"]);
  assert.equal(calls.filter(x => x.method === "session").length, 0);
});
test("an initial greeting is restored without inventing a selected product", async () => {
  const p = catalogPayload(); p.answer = "您好，请告诉我产品与问题。"; p.structured_data.presentation.result_type = "clarification"; p.structured_data.data = null;
  const { store, h, service } = await preparedAiStore({ status: "ok", product_code: null, catalog_query: false, understanding_id: "TICKET-GREETING" }, p,
    { history: { [p.session_id]: [historyTurn(p, "你好")] } });
  await store.send("你好"); const refreshed = h.module("src/composables/agentStore.ts").createAgentStore(service, { minimumPendingMs: 0 }); await refreshed.initialize();
  assert.equal(refreshed.product.value, undefined); assert.equal(refreshed.session.value.product_code, "");
  assert.equal(refreshed.selected.value.result.answer, p.answer); assert.equal(refreshed.selected.value.result.backend.executionTrace.length, 2);
  assert.equal(refreshed.selected.value.result.backend.rows.length, 0);
});
test("selecting a product after a catalog preserves both histories without mixing charts", async () => {
  const catalog = catalogPayload(), production = payload(); production.session_id = "SYNTHETIC-PRODUCT-SESSION"; production.turn_id = "SYNTHETIC-PRODUCTION-TURN";
  const resolution = text => text === "有哪些产品" ? { status: "ok", product_code: null, catalog_query: true, understanding_id: "CAT-TICKET" }
    : { status: "ok", product_code: "TEST-PRODUCT", catalog_query: false, understanding_id: "PROD-TICKET" };
  const { store } = await preparedAiStore(resolution, catalog, { productSessionId: production.session_id,
    queryResult: input => input.text === "有哪些产品" ? catalog : production,
    history: { [catalog.session_id]: [historyTurn(catalog, "有哪些产品")], [production.session_id]: [historyTurn(production, "生产10片TEST-PRODUCT")] } });
  await store.send("有哪些产品"); const catalogBinding = { ...store.session.value };
  await store.send("生产10片TEST-PRODUCT");
  assert.equal(store.session.value.product_code, "TEST-PRODUCT"); assert.equal(store.selected.value.result.backend.rows.length, 1);
  assert.equal(store.recentSessions.value.length, 2);
  await store.restore(catalogBinding);
  assert.equal(store.session.value.product_code, ""); assert.equal(store.selected.value.question, "有哪些产品");
  assert.equal(store.selected.value.result.backend.resultType, "catalog"); assert.equal(store.selected.value.result.backend.chartAi.status, "not_applicable");
  assert.equal(store.selected.value.result.backend.executionTrace.length, 2);
});
