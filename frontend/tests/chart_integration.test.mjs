import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve, extname, basename, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

// Real R19 adapter, ResultPanel, BackendDetails and chart SFCs compiled to memory.
// Only the surrounding store and decorative children are fixtures; no HTTP/DB.
const frontend = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dependencies = process.env.PMC_FRONTEND_DEPENDENCY_ROOT || frontend;
const dependency = createRequire(join(dependencies, "package.json"));
const ts = dependency("typescript"), vue = dependency("vue");
const { parse, compileScript } = dependency("@vue/compiler-sfc");
const { renderToString } = dependency("@vue/server-renderer");
const actual = new Set(["ResultPanel.vue", "BackendDetails.vue", "InventoryGapChart.vue",
  "ShortageBarChart.vue", "InventoryTrendChart.vue"]);

function harness() {
  let writes = 0;
  const state = {
    pending: vue.ref(false), selected: vue.ref(null), session: vue.ref(undefined),
    messages: vue.ref([]), canSend: vue.ref(true),
    retry() { throw new Error("Rendering cannot retry"); }, executeInventory() { writes++; },
  };
  state.result = vue.computed(() => state.selected.value?.result);
  const cache = new Map();
  function load(filename) {
    filename = resolve(filename);
    if (!extname(filename)) filename += ".ts";
    if (cache.has(filename)) return cache.get(filename);
    if (filename.endsWith(".css")) return {};
    if (filename.endsWith(".vue") && !actual.has(basename(filename)))
      return { default: vue.defineComponent({ setup(_, { slots }) {
        return () => vue.h("span", { "data-component": basename(filename) }, slots.default?.());
      } }) };
    let source = readFileSync(filename, "utf8");
    if (filename.endsWith(".vue")) {
      const { descriptor, errors } = parse(source, { filename });
      assert.deepEqual(errors, []);
      source = compileScript(descriptor, { id: "chart-integration-" + basename(filename), inlineTemplate: true }).content;
    }
    const output = ts.transpileModule(source.replaceAll("import.meta.env", '({ VITE_STREAMLIT: "false" })'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    const module = { exports: {} };
    new Function("require", "module", "exports", output)((name) => {
      if (name === "vue") return vue;
      if (name === "../composables/useAgent") return { useAgent: () => state };
      if (name.startsWith(".")) return load(resolve(dirname(filename), name));
      return dependency(name);
    }, module, module.exports);
    cache.set(filename, module.exports);
    return module.exports;
  }
  const adapt = load(resolve(frontend, "src/services/responseAdapter.ts")).adaptBackendResponse;
  const chart = load(resolve(frontend, "src/services/analysisChart.ts"));
  const Panel = load(resolve(frontend, "src/components/ResultPanel.vue")).default;
  const Details = load(resolve(frontend, "src/components/BackendDetails.vue")).default;
  function select(payload, id = 1, history = false) {
    const result = adapt(payload);
    if (history) result.backend.raw = { response: payload, fact_references: [] };
    const message = { id, question: "Synthetic query", state: "done", result };
    state.messages.value = [message]; state.selected.value = message;
    state.session.value = { session_id: payload.session_id, product_code: result.product, mode: "current", scenario_id: null };
    return result;
  }
  async function render(includeTable = true) {
    return renderToString(vue.createSSRApp({ render: () => vue.h(vue.Fragment, [
      vue.h(Panel), ...(includeTable && state.result.value ? [vue.h(Details, { result: state.result.value })] : []),
    ]) }));
  }
  return { state, adapt, chart, select, render, writes: () => writes };
}
const row = (changes = {}) => ({
  material_code: "TEST-MAT-A", name: "Synthetic material A", unit: "片", inventory_unit: "片", stock_unit: "片",
  qty_per: "2.00", required_qty: "20.00", recorded_stock_qty: "12.00", actual_stock_qty: "12.00",
  stock_qty_in_bom_unit: "12.00", available_qty: null, on_hand_qty: null,
  shortage_qty: "8.00", book_shortage_qty: "8.00", suggested_purchase_qty: "10.00",
  comparison_status: "comparable", quantity_status: "shortage", record_found: true, duplicate_free: true, ...changes,
});
function payload(kind = "production", changes = {}) {
  return { intent: "production_capacity", status: "ok", answer: "Synthetic evidence only.",
    session_id: "TEST-SESSION-A", conversation_id: "TEST-CONVERSATION-A", turn_id: "TEST-TURN-A",
    structured_data: { query_type: "production_capacity",
      presentation: { result_type: kind, show_maximum: kind === "maximum", user_warnings: [] },
      data: { product: "TEST-PRODUCT-A", revision: "V01", product_unit: "片", requested_quantity: 10,
        inventory_sufficient_for_requested: false, materials: [row()], recommendations: [], products: [], lines: [] },
      completed_steps: [] },
    provenance: { source_file: "synthetic-chart-input.json" }, warnings: [], ...changes };
}
const count = (html, token) => html.split(token).length - 1;
function noCharts(html) { assert.doesNotMatch(html, /class="(?:gap-chart|shortage-chart|trend-chart)"/); }

test("real adapter, chart SFCs and table retain the same Decimal strings", async () => {
  const h = harness(), input = payload();
  input.structured_data.data.materials = [row({
    required_qty: "0.10000000000000000100", recorded_stock_qty: "0.02000000000000000000",
    actual_stock_qty: "0.02000000000000000000", stock_qty_in_bom_unit: "0.02000000000000000000",
    shortage_qty: "0.08000000000000000100",
  })];
  const before = structuredClone(input), result = h.select(input);
  const [g] = h.chart.buildGapGroups(result.backend.rows, result.backend.resultType, result.backend.raw);
  assert.equal(g.items[0].demand.text, result.backend.rows[0].required_qty);
  assert.equal(g.items[0].stock.text, result.backend.rows[0].stock_qty_in_bom_unit);
  const html = await h.render();
  assert.match(html, /class="gap-chart"/); assert.match(html, /class="shortage-chart"/);
  assert.ok(count(html, "0.10000000000000000100") >= 2);
  assert.ok(count(html, "0.02000000000000000000") >= 2);
  assert.match(html, /0\.08000000000000000100/);
  assert.deepEqual(input, before); assert.equal(h.writes(), 0);
});
test("normalized stock uses BOM units while original stock retains its own unit", async () => {
  const h = harness(), input = payload();
  input.structured_data.data.materials = [row({ unit: "克", stock_unit: "公斤", inventory_unit: "公斤",
    required_qty: "500.00", recorded_stock_qty: "1.00", actual_stock_qty: "1.00",
    stock_qty_in_bom_unit: "1000.00", shortage_qty: "0.00", suggested_purchase_qty: null, quantity_status: "sufficient" })];
  const result = h.select(input), [g] = h.chart.buildGapGroups(result.backend.rows, "production", result.backend.raw);
  assert.equal(g.unit, "克"); assert.equal(g.items[0].stock.text, "1000.00");
  assert.equal(g.items[0].stock.basis, "核对库存（物料单位）");
  assert.equal(result.backend.rows[0].recorded_stock_qty, "1.00");
  assert.equal(result.backend.rows[0].stock_unit, "公斤");
  assert.equal(result.backend.rows[0].stock_qty_in_bom_unit, "1000.00");
  const html = await h.render();
  assert.match(html, /核对库存（物料单位）/); assert.match(html, /1000\.00/);
  assert.match(html, /公斤/); assert.doesNotMatch(html, /库存 1\.00 克/);
});
test("missing stock and real zero stay distinct through adapter and SVG", async () => {
  const h = harness(), input = payload();
  input.structured_data.data.materials = [
    row({ material_code: "TEST-UNKNOWN", recorded_stock_qty: null, actual_stock_qty: null,
      stock_qty_in_bom_unit: null, shortage_qty: null, suggested_purchase_qty: null,
      record_found: false, comparison_status: "no_stock", quantity_status: "unknown" }),
    row({ material_code: "TEST-ZERO", recorded_stock_qty: "0.00", actual_stock_qty: "0.00",
      stock_qty_in_bom_unit: "0.00", shortage_qty: "20.00" }),
  ];
  const result = h.select(input), [g] = h.chart.buildGapGroups(result.backend.rows, "production", result.backend.raw);
  assert.equal(g.items[0].stock, null); assert.equal(g.items[1].stock.text, "0.00");
  assert.equal(result.backend.rows[0].recorded_stock_qty, "未查询到");
  assert.equal(result.backend.rows[1].recorded_stock_qty, "0.00");
  const html = await h.render();
  assert.match(html, /class="gap-slot"/); assert.match(html, /class="gap-bar stock zero"/);
  assert.match(html, /未查询到/); assert.match(html, /库存 0/);
});
test("mixed units stay separate and shortage/ordering quantities are not recalculated", async () => {
  const h = harness(), input = payload("shortage");
  input.structured_data.data.materials = [
    row({ material_code: "TEST-PIECE", shortage_qty: "8.000", suggested_purchase_qty: "10.000" }),
    row({ material_code: "TEST-LENGTH", unit: "米", stock_unit: "米", inventory_unit: "米",
      shortage_qty: "0.1250", suggested_purchase_qty: "0.5000" }),
  ];
  const result = h.select(input), groups = h.chart.buildShortageGroups(result.backend.rows, "shortage", result.backend.raw);
  assert.deepEqual(groups.map((g) => g.unit), ["片", "米"]);
  assert.equal(groups[0].items[0].shortage.text, "8.000");
  assert.equal(groups[1].items[0].shortage.text, "0.1250");
  assert.equal(groups[1].items[0].suggested.text, "0.5000");
  const html = await h.render();
  assert.match(html, /单位：片/); assert.match(html, /单位：米/); assert.match(html, /0\.1250/);
  assert.equal(h.writes(), 0);
});
for (const kind of ["catalog", "product_overview", "bom", "inventory", "maximum", "purchase"])
  test(kind + " does not gain unrelated production charts", async () => {
    const h = harness(); h.select(payload(kind)); noCharts(await h.render());
  });
for (const status of ["invalid_query", "needs_input", "query_failed", "data_unavailable"])
  test(status + " cannot expose charts even with an older numeric payload", async () => {
    const h = harness(); h.select(payload("production", { status })); noCharts(await h.render());
  });
test("pending and empty results cannot expose an older chart", async () => {
  const h = harness(); h.select(payload()); assert.match(await h.render(), /class="gap-chart"/);
  h.state.pending.value = true; noCharts(await h.render(false));
  h.state.pending.value = false; h.state.selected.value = null; noCharts(await h.render(false));
});
test("selected product and history keep their own table and chart values", async () => {
  const h = harness(), first = payload(); h.select(first);
  assert.match(await h.render(), /TEST-MAT-A/);
  const second = payload(); second.session_id = "TEST-SESSION-B";
  second.structured_data.data.product = "TEST-PRODUCT-B"; second.structured_data.data.requested_quantity = 50;
  second.structured_data.data.materials = [row({ material_code: "TEST-MAT-B", required_qty: "150.000",
    stock_qty_in_bom_unit: "100.000", recorded_stock_qty: "100.000", actual_stock_qty: "100.000", shortage_qty: "50.000" })];
  const result = h.select(second, 2), html = await h.render();
  assert.match(html, /TEST-MAT-B/); assert.match(html, /150\.000/); assert.doesNotMatch(html, /TEST-MAT-A/);
  assert.equal(h.chart.buildGapGroups(result.backend.rows, "production", result.backend.raw)[0].items[0].demand.text, "150.000");
  h.select(first, 1, true); const restored = await h.render();
  assert.match(restored, /TEST-MAT-A/); assert.doesNotMatch(restored, /TEST-MAT-B/); assert.match(restored, /20\.00/);
});
test("history response wrapper renders explicit snapshots but ignores fact references", async () => {
  const h = harness(), input = payload("inventory");
  input.structured_data.inventory_history = [
    { material_code: "TEST-MAT-A", product: "TEST-PRODUCT-A", unit: "片", snapshot_at: "2026-10-01T00:00:00+08:00", recorded_stock_qty: "12.00" },
    { material_code: "TEST-MAT-A", product: "TEST-PRODUCT-A", unit: "片", snapshot_at: "2026-10-02T00:00:00+08:00", recorded_stock_qty: "8.00" },
  ];
  const result = h.select(input, 1, true);
  result.backend.raw.fact_references = [{ result: { data: { inventory_history: [
    { material_code: "INJECTED-REF", unit: "片", date: "2026-01-01", recorded_stock_qty: "99999" },
    { material_code: "INJECTED-REF", unit: "片", date: "2026-01-02", recorded_stock_qty: "99998" },
  ] } } }];
  const series = h.chart.buildInventoryTrends(result.backend.raw);
  assert.equal(series.length, 1); assert.deepEqual(series[0].points.map((p) => p.text), ["12.00", "8.00"]);
  const html = await h.render();
  assert.match(html, /class="trend-chart"/); assert.match(html, /2026-10-01/); assert.match(html, /12\.00/);
  assert.doesNotMatch(html, /INJECTED-REF|99999/); assert.doesNotMatch(html, /class="gap-chart"/);
});
test("one snapshot cannot turn into an invented trend after restoring history", async () => {
  const h = harness(), input = payload("inventory");
  input.structured_data.inventory_history = [
    { material_code: "TEST-MAT-A", unit: "片", snapshot_at: "2026-10-01T00:00:00+08:00", recorded_stock_qty: "12.00" },
  ];
  h.select(input, 1, true); noCharts(await h.render());
});
test("chart insertion preserves the actual preview confirmation gate", async () => {
  const h = harness(), input = payload(); input.structured_data.operation = "inventory_preview";
  Object.assign(input.structured_data.data, { session_id: input.session_id, preview_id: "TEST-PREVIEW",
    expires_at: "2099-01-01T00:00:00+00:00", can_execute: true, execution_available: true, executed: false });
  const result = h.select(input); let html = await h.render();
  noCharts(html); assert.match(html, /明确确认库存扣减/); assert.match(html, /库存执行凭据/);
  assert.equal(count(html, "inventory-confirmation-form"), 1); assert.equal(h.writes(), 0);
  result.backend.sessionId = "ANOTHER-SESSION"; html = await h.render();
  assert.doesNotMatch(html, /inventory-confirmation-form/); assert.equal(h.writes(), 0);
});
