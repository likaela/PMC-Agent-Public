import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
const require = createRequire(import.meta.url);
const ts = require("typescript");
const cache = new Map();
function loadFile(filename) {
  if (cache.has(filename)) return cache.get(filename);
  const source = readFileSync(filename, "utf8").replaceAll("import.meta.env", "({})");
  const output = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  }}).outputText;
  const module = { exports: {} };
  const localRequire = (name) => name.startsWith(".")
    ? loadFile(resolve(dirname(filename), name) + ".ts") : require(name);
  new Function("require", "module", "exports", output)(localRequire, module, module.exports);
  cache.set(filename, module.exports);
  return module.exports;
}
const loadTs = (relative) => loadFile(fileURLToPath(new URL(relative, import.meta.url)));
const chart = loadTs("../src/services/analysisChart.ts");
const { adaptBackendResponse } = loadTs("../src/services/responseAdapter.ts");


// R19-specific adapters, raw history envelopes, and data-honesty regressions.
const row = (changes = {}) => ({ material_code: "TEST-MAT", name: "测试物料", unit: "片",
  required_qty: "10.00", recorded_stock_qty: "8.00", shortage_qty: "2.00",
  suggested_purchase_qty: "3.00", status: "不满足需求", ...changes });
const payload = (rows, changes = {}) => ({ intent: "production_capacity", status: "ok", answer: "来自工具",
  structured_data: { query_type: "production_capacity",
    presentation: { result_type: "production" }, data: { product: "TEST-PRODUCT",
      requested_quantity: 1, product_unit: "片", materials: rows, ...changes } }, warnings: [] });
const history = (entries, data = {}) => ({ structured_data: { data: { inventory_history: entries, ...data } } });
const point = (date, value, changes = {}) => ({ material_code: "TEST-MAT", unit: "片",
  observed_at: date, available_qty: value, ...changes });

test("R19 adapter and chart share exact backend Decimal text", () => {
  const original = row({ recorded_stock_qty: "9007199254740993.000", required_qty: "9007199254740995.000",
    shortage_qty: "2.000", suggested_purchase_qty: "10.000" });
  const raw = payload([original]);
  const view = adaptBackendResponse(raw);
  const group = chart.buildGapGroups(view.backend.rows, view.backend.resultType, view.backend.raw)[0];
  assert.equal(group.items[0].stock.text, original.recorded_stock_qty);
  assert.equal(group.items[0].demand.text, original.required_qty);
  const shortage = chart.buildShortageGroups(view.backend.rows, view.backend.resultType, view.backend.raw)[0].items[0];
  assert.equal(shortage.shortage.text, "2.000");
  assert.equal(shortage.suggested.text, "10.000");
  assert.equal(view.backend.rows[0].recorded_stock_qty, original.recorded_stock_qty);
});

test("R19 backend-normalized stock is used, never front-end unit conversion", () => {
  const raw = payload([row({ unit: "克", stock_unit: "公斤", inventory_unit: "公斤",
    recorded_stock_qty: "0.500", actual_stock_qty: "0.500", stock_qty_in_bom_unit: "500.000",
    comparison_status: "comparable", required_qty: "600.000", shortage_qty: "100.000" })]);
  const view = adaptBackendResponse(raw);
  assert.equal(view.backend.rows[0].stock_qty_in_bom_unit, "500.000");
  const group = chart.buildGapGroups(view.backend.rows, view.backend.resultType, view.backend.raw)[0];
  assert.equal(group.unit, "克");
  assert.equal(group.items[0].stock.text, "500.000");
  assert.equal(group.items[0].stock.basis, "核对库存（物料单位）");
  assert.equal(group.items[0].demand.text, "600.000");
});

test("unconverted, incompatible, unknown or uncomparable stock cannot draw a value", () => {
  for (const meta of [
    { inventory_unit: "公斤" }, { stock_unit: "公斤" }, { inventory_unit: null },
    { comparison_status: "unit_mismatch" }, { comparison_status: "duplicate_pending" },
    { record_found: false }, { duplicate_free: false },
  ]) {
    const source = row(meta), raw = payload([source]);
    const view = adaptBackendResponse(raw);
    const groups = chart.buildGapGroups(view.backend.rows, view.backend.resultType, view.backend.raw);
    assert.equal(groups[0].items[0].stock, null, JSON.stringify(meta));
    assert.equal(chart.buildShortageGroups(view.backend.rows, view.backend.resultType, view.backend.raw)[0].items.length, 0);
  }
});

test("raw metadata index and material code must match before permitting stock", () => {
  const rows = [row()];
  const raw = payload([row({ material_code: "OTHER-MATERIAL", inventory_unit: "片", comparison_status: "comparable" })]);
  assert.equal(chart.buildGapGroups(rows, "production", raw)[0].items[0].stock, null);
});

test("normalized zero is genuine zero and must win over a positive raw value", () => {
  const r = row({ stock_qty_in_bom_unit: "0.000", recorded_stock_qty: "99",
    inventory_unit: "公斤", comparison_status: "comparable" });
  const group = chart.buildGapGroups([r], "production", payload([r]))[0];
  assert.equal(group.items[0].stock.value, 0);
  assert.equal(group.items[0].stock.text, "0.000");
});

test("backend comparable normalized stock is authoritative over original duplicate flags", () => {
  const source = row({ stock_unit: "公斤", inventory_unit: "公斤", duplicate_free: false,
    comparison_status: "comparable", stock_qty_in_bom_unit: "500.000", unit: "克",
    recorded_stock_qty: "0.500", required_qty: "600.000", shortage_qty: "100.000" });
  const raw = payload([source]), view = adaptBackendResponse(raw);
  const groups = chart.buildGapGroups(view.backend.rows, view.backend.resultType, view.backend.raw);
  assert.equal(groups[0].items[0].stock.text, "500.000");
  const shortages = chart.buildShortageGroups(view.backend.rows, view.backend.resultType, view.backend.raw);
  assert.equal(shortages[0].items[0].shortage.text, "100.000");
});

test("missing stock never becomes zero even when a demand or gap exists", () => {
  const r = row({ recorded_stock_qty: "未查询到", shortage_qty: "未查询到", suggested_purchase_qty: "未提供" });
  const gap = chart.buildGapGroups([r], "production")[0];
  assert.equal(gap.items[0].stock, null);
  assert.equal(gap.missingCount, 1);
  const shortage = chart.buildShortageGroups([r], "shortage")[0];
  assert.equal(shortage.items.length, 0);
  assert.equal(shortage.missingStockCount, 1);
});

test("Decimal underflow, overflow, nonfinite, booleans and numbers cannot invent chart facts", () => {
  const tiny = "0." + "0".repeat(400) + "1";
  for (const value of [tiny, "9".repeat(400), "NaN", "Infinity", "1e3", "1/2", "1.5.6", true, 10]) {
    assert.equal(chart.parseDecimal(value), null, String(value));
  }
  assert.equal(chart.parseDecimal("0.000"), 0);
  assert.equal(chart.parseDecimal("0.000000000000000000000001"), 1e-24);
});

test("bounded ticks are finite, strictly increasing and cover extreme values", () => {
  for (const max of [0, Number.MIN_VALUE, 1e-300, 1e-12, 1, 128, Number.MAX_VALUE]) {
    const ticks = chart.niceTicks(max);
    assert.equal(ticks[0], 0);
    assert.ok(ticks.length <= 14);
    assert.ok(ticks.every(Number.isFinite));
    assert.ok(ticks.every((value, i) => i === 0 || value > ticks[i - 1]));
    assert.ok(ticks.at(-1) >= max);
  }
});

test("shortage sort preserves distinctions beyond Number precision", () => {
  const groups = chart.buildShortageGroups([
    row({ material_code: "SMALL", shortage_qty: "9007199254740992.000" }),
    row({ material_code: "LARGE", shortage_qty: "9007199254740993.000" }),
  ], "shortage");
  assert.deepEqual(groups[0].items.map(x => x.code), ["LARGE", "SMALL"]);
  assert.match(chart.shortageSummary(groups), /9007199254740993\.000/);
});

test("shortage summary never compares magnitudes across distinct units", () => {
  const groups = chart.buildShortageGroups([
    row({ unit: "片", shortage_qty: "2.00" }),
    row({ unit: "米", shortage_qty: "100.00" }),
  ], "shortage");
  const summary = chart.shortageSummary(groups);
  assert.match(summary, /片.*本单位最大缺口 2\.00/);
  assert.match(summary, /米.*本单位最大缺口 100\.00/);
});

test("failed responses and nonproduction results cannot display old demand charts", () => {
  const rows = [row()];
  for (const kind of ["bom", "catalog", "inventory", "maximum", "purchase"]) {
    assert.deepEqual(chart.buildGapGroups(rows, kind), []);
    assert.deepEqual(chart.buildShortageGroups(rows, kind), []);
  }
  for (const status of ["query_failed", "invalid_query", "needs_input", "forbidden"]) {
    const raw = { ...payload(rows), status };
    assert.deepEqual(chart.buildGapGroups(rows, "production", raw), []);
    assert.deepEqual(chart.buildShortageGroups(rows, "production", raw), []);
    assert.deepEqual(chart.buildInventoryTrends({ ...history([
      point("2026-10-01", "2"), point("2026-10-02", "1")]), status }), []);
  }
});

test("history wrapper and reused_result unwrap without scanning fact references", () => {
  const snapshots = [point("2026-10-01", "9.00"), point("2026-10-02", "8.00")];
  const response = history(snapshots);
  assert.equal(chart.buildInventoryTrends({ response, fact_references: [] }).length, 1);
  assert.equal(chart.buildInventoryTrends({ structured_data: { reused_result: { data: { inventory_history: snapshots } } } }).length, 1);
  assert.deepEqual(chart.buildInventoryTrends({ response: payload([]), fact_references: [{ inventory_history: snapshots }] }), []);
  assert.deepEqual(chart.buildInventoryTrends({ response: payload([]), inventory_history: snapshots }), []);
});

test("historical Decimal text and timezone's original calendar date are retained", () => {
  const values = [point("2026-10-01T23:30:00-08:00", "10.000"),
    point("2026-10-02T23:30:00-08:00", "9.500")];
  const series = chart.buildInventoryTrends(history(values));
  assert.deepEqual(series[0].points.map(p => p.text), ["10.000", "9.500"]);
  assert.equal(chart.trendDateText(values[0].observed_at), "2026-10-01");
});

test("Python microsecond timestamps retain original text and never fake submillisecond order", () => {
  const values = [point("2026-10-01T01:00:00.123456+08:00", "10.000"),
    point("2026-10-02T01:00:00.654321+08:00", "9.000")];
  const series = chart.buildInventoryTrends(history(values));
  assert.equal(series[0].points[0].at, values[0].observed_at);
  assert.deepEqual(chart.buildInventoryTrends(history([
    point("2026-10-01T01:00:00.000001Z", "10"),
    point("2026-10-01T01:00:00.000002Z", "9"),
    point("2026-10-02T01:00:00Z", "8"),
  ])), []);
});

test("trend quantities never mix available/on-hand, products, sites or units", () => {
  const snapshots = [];
  for (const product of ["P1", "P2"]) for (const unit of ["片", "米"]) {
    snapshots.push(point("2026-10-01", "4", { product, unit }));
    snapshots.push(point("2026-10-02", "3", { product, unit }));
    snapshots.push({ material_code: "TEST-MAT", product, unit, observed_at: "2026-10-01", on_hand_qty: "20.00" });
    snapshots.push({ material_code: "TEST-MAT", product, unit, observed_at: "2026-10-02", on_hand_qty: "19.00" });
  }
  const series = chart.buildInventoryTrends(history(snapshots));
  assert.equal(series.length, 8);
  assert.equal(new Set(series.map(s => s.key)).size, 8);
  assert.deepEqual(new Set(series.map(s => s.basis)), new Set(["可用库存", "在库数量"]));
  const bySite = [point("2026-10-01", "4", { site: "A" }), point("2026-10-02", "3", { site: "A" }),
    point("2026-10-01", "40", { site: "B" }), point("2026-10-02", "30", { site: "B" })];
  assert.equal(chart.buildInventoryTrends(history(bySite)).length, 2);
});

test("mixed quantity fields with only one point each never become a fake trend", () => {
  const snapshots = [point("2026-10-01", "5"),
    { material_code: "TEST-MAT", unit: "片", observed_at: "2026-10-02", on_hand_qty: "9" }];
  assert.deepEqual(chart.buildInventoryTrends(history(snapshots)), []);
});

test("a middle observation reported in another basis cannot be bridged", () => {
  assert.deepEqual(chart.buildInventoryTrends(history([
    point("2026-10-01", "10"),
    { material_code: "TEST-MAT", unit: "片", observed_at: "2026-10-02", on_hand_qty: "20" },
    point("2026-10-03", "8"),
  ])), []);
});

test("conflicting duplicate instant suppresses the sequence instead of choosing first", () => {
  const values = [point("2026-10-01", "10"), point("2026-10-02", "9"), point("2026-10-02", "8"),
    point("2026-10-03", "7")];
  assert.deepEqual(chart.buildInventoryTrends(history(values)), []);
  values[2].available_qty = "9.000";
  assert.equal(chart.buildInventoryTrends(history(values))[0].points.length, 3);
});

test("missing intermediate point cannot be bridged into a continuous line", () => {
  for (const broken of [
    point("2026-10-02", null), point("2026-10-02", "未提供"),
    point("not-a-date", "9"), point("2026-10-02", "9", { unit: null }),
  ]) {
    assert.deepEqual(chart.buildInventoryTrends(history([
      point("2026-10-01", "10"), broken, point("2026-10-03", "8"),
    ])), []);
  }
});

test("ambiguous dates, invalid calendar dates and generic quantities are not inventory history", () => {
  for (const at of ["2026-02-30", "2026-10-01T01:00:00", "10/01/2026",
    "2026-10-01T24:00:00Z", "2026-10-01T00:00:00+15:00"]) {
    assert.deepEqual(chart.buildInventoryTrends(history([point(at, "10"), point("2026-10-03", "8")])), []);
  }
  assert.deepEqual(chart.buildInventoryTrends(history([
    { material_code: "TEST-MAT", unit: "片", at: "2026-10-01", quantity: "10" },
    { material_code: "TEST-MAT", unit: "片", at: "2026-10-02", quantity: "8" },
  ])), []);
});

test("single current snapshot, conversation timestamps, text answers and array-like history stay empty", () => {
  assert.deepEqual(chart.buildInventoryTrends({ answer: "库存从10变为8", structured_data: { data: {
    materials: [row()], snapshot_at: "2026-10-01", created_at: "2026-10-02",
  } } }), []);
  assert.deepEqual(chart.inventoryHistoryEntries({ structured_data: { inventory_history: { 0: point("2026-10-01", "10"), length: 1 } } }), []);
});

test("chart projection is pure and cannot mutate current or historical results", () => {
  const raw = payload([row({ inventory_unit: "片", comparison_status: "comparable" })]);
  raw.structured_data.data.inventory_history = [point("2026-10-01", "2"), point("2026-10-02", "1")];
  const before = JSON.stringify(raw), view = adaptBackendResponse(raw), table = JSON.stringify(view.backend.rows);
  chart.buildGapGroups(view.backend.rows, view.backend.resultType, view.backend.raw);
  chart.buildShortageGroups(view.backend.rows, view.backend.resultType, view.backend.raw);
  chart.buildInventoryTrends({ response: raw, fact_references: [] });
  assert.equal(JSON.stringify(raw), before);
  assert.equal(JSON.stringify(view.backend.rows), table);
});

function component(relative) {
  const { parse, compileScript } = require("@vue/compiler-sfc");
  const filename = fileURLToPath(new URL(relative, import.meta.url));
  const { descriptor } = parse(readFileSync(filename, "utf8"), { filename });
  const compiled = compileScript(descriptor, { id: "chart-test", inlineTemplate: true });
  const output = ts.transpileModule(compiled.content, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  }}).outputText;
  const module = { exports: {} };
  new Function("require", "module", "exports", output)((name) =>
    name.includes("analysisChart") ? chart : require(name), module, module.exports);
  return module.exports.default;
}
test("actual SVG components preserve exact labels, distinguish zero, and render no fabricated trend", async () => {
  const { h } = require("vue"), { renderToString } = require("@vue/server-renderer");
  const Gap = component("../src/components/InventoryGapChart.vue");
  const Shortage = component("../src/components/ShortageBarChart.vue");
  const Trend = component("../src/components/InventoryTrendChart.vue");
  const groups = chart.buildGapGroups([row({ recorded_stock_qty: "0.000" }), row({
    material_code: "MISSING", recorded_stock_qty: "未提供",
  })], "production");
  const gap = await renderToString(h(Gap, { groups }));
  assert.match(gap, /0\.000/);
  assert.match(gap, /gap-slot/);
  const shortages = chart.buildShortageGroups([row({ shortage_qty: "2.000", suggested_purchase_qty: "3.000" })], "shortage");
  const shortage = await renderToString(h(Shortage, { groups: shortages }));
  assert.match(shortage, /2\.000/);
  assert.match(shortage, /3\.000/);
  const empty = await renderToString(h(Trend, { series: [] }));
  assert.doesNotMatch(empty, /<svg|<path/);
});

test("trend headings distinguish backend product and warehouse scope", async () => {
  const { h } = require("vue"), { renderToString } = require("@vue/server-renderer");
  const Trend = component("../src/components/InventoryTrendChart.vue");
  const series = chart.buildInventoryTrends({ structured_data: { data: {
    inventory_history: ["2026-10-01", "2026-10-02"].map(observed_at => ({
      product_code: "TEST-PRODUCT", material_code: "TEST-MATERIAL", unit: "片",
      warehouse: "TEST-WAREHOUSE", observed_at, on_hand_qty: "10.000",
    })),
  } } });
  assert.equal(series.length, 1);
  const html = await renderToString(h(Trend, { series }));
  assert.match(html, /产品：TEST-PRODUCT/);
  assert.match(html, /范围：warehouse=TEST-WAREHOUSE/);
  assert.match(html, /10\.000/);
});

// Source-version reasonable projection cases are retained below, followed by
// R19 contract/precision/safety regressions. No Vite server or filesystem cache.
test("缺失库存是未提供，绝不解析成 0", () => {
  assert.equal(chart.parseDecimal("未提供"), null);
  assert.equal(chart.parseDecimal("未查询到"), null);
  assert.equal(chart.parseDecimal(""), null);
  assert.equal(chart.parseDecimal("—"), null);
  assert.equal(chart.parseDecimal("abc"), null);
  assert.equal(chart.parseDecimal("0"), 0);
  assert.equal(chart.parseDecimal("12.5"), 12.5);
});

test("生产/缺料结果生成分组；库存按可用→核验→账面→在库取首个已提供值", () => {
  const groups = chart.buildGapGroups(
    [
      {
        material_code: "MAT-1",
        name: "主板",
        unit: "片",
        required_qty: "10",
        available_qty: "未提供",
        actual_stock_qty: "8",
        recorded_stock_qty: "9",
      },
      {
        material_code: "MAT-2",
        name: "螺丝",
        unit: "片",
        required_qty: "100",
        recorded_stock_qty: "120",
      },
      {
        material_code: "MAT-3",
        name: "外壳",
        unit: "片",
        required_qty: "2",
        available_qty: "未查询到",
        actual_stock_qty: "未提供",
        recorded_stock_qty: "未提供",
        on_hand_qty: "未提供",
      },
      {
        material_code: "MAT-4",
        name: "贴纸",
        unit: "片",
        required_qty: "1",
        recorded_stock_qty: "0",
      },
    ],
    "production",
  );
  assert.equal(groups.length, 1);
  assert.equal(groups[0].unit, "片");
  assert.equal(groups[0].missingCount, 1);
  assert.equal(groups[0].max, 120);
  const [a, b, c, d] = groups[0].items;
  assert.equal(a.stock.basis, "核验库存"); // available 未提供 → 核验
  assert.equal(a.stock.value, 8);
  assert.equal(a.demand.text, "10");
  assert.equal(b.stock.basis, "账面记录");
  assert.equal(c.stock, null); // 未提供 ≠ 0
  assert.equal(d.stock.value, 0); // 真实 0 保留为 0
});

test("需求量未提供的行不进入图表", () => {
  const groups = chart.buildGapGroups(
    [
      {
        material_code: "MAT-1",
        unit: "片",
        required_qty: "未提供",
        recorded_stock_qty: "8",
      },
      {
        material_code: "MAT-2",
        unit: "片",
        required_qty: "3",
        recorded_stock_qty: "5",
      },
    ],
    "production",
  );
  assert.equal(groups[0].items.length, 1);
  assert.equal(groups[0].items[0].code, "MAT-2");
});

test("不同单位分开展示，绝不混合比较", () => {
  const groups = chart.buildGapGroups(
    [
      {
        material_code: "A",
        unit: "片",
        required_qty: "2",
        recorded_stock_qty: "1",
      },
      {
        material_code: "B",
        unit: "套",
        required_qty: "3",
        recorded_stock_qty: "4",
      },
    ],
    "shortage",
  );
  assert.deepEqual(
    groups.map((group) => group.unit),
    ["片", "套"],
  );
  assert.equal(groups[0].items.length, 1);
  assert.equal(groups[1].items.length, 1);
});

test("库存/目录类结果不生成对比图", () => {
  assert.equal(
    chart.buildGapGroups(
      [{ material_code: "A", unit: "片", required_qty: "2" }],
      "inventory",
    ).length,
    0,
  );
  assert.equal(chart.buildGapGroups(undefined, "production").length, 0);
  assert.equal(chart.buildGapGroups([], "production").length, 0);
});

test("纵轴刻度包含 0 并覆盖最大值，步长为 1/2/5 的十次幂", () => {
  for (const max of [10, 54, 1000, 9102, 0.75]) {
    const ticks = chart.niceTicks(max);
    assert.equal(ticks[0], 0);
    assert.ok(ticks.at(-1) >= max, `max ${max} covered`);
    const steps = ticks.slice(1).map((value, index) => value - ticks[index]);
    assert.ok(steps.every((step) => step > 0));
  }
  assert.deepEqual(chart.niceTicks(0), [0]);
  assert.deepEqual(chart.niceTicks(-3), [0]);
});

test("无障碍摘要点名未提供数量，且不与 0 混淆", () => {
  const groups = chart.buildGapGroups(
    [
      {
        material_code: "A",
        unit: "片",
        required_qty: "2",
        recorded_stock_qty: "1",
      },
      {
        material_code: "B",
        unit: "片",
        required_qty: "3",
        recorded_stock_qty: "未提供",
      },
    ],
    "production",
  );
  const summary = chart.gapSummary(groups);
  assert.match(summary, /2 种物料/);
  assert.match(summary, /1 种库存未提供/);
  assert.match(summary, /未提供不等于 0/);
  assert.equal(chart.gapSummary([]), "");
});

test("缺料图只列需要处理的物料，按缺口从大到小并按单位分组", () => {
  const groups = chart.buildShortageGroups(
    [
      {
        material_code: "A",
        name: "物料甲",
        unit: "片",
        shortage_qty: "5",
        suggested_purchase_qty: "8",
        recorded_stock_qty: "1",
      },
      {
        material_code: "B",
        name: "物料乙",
        unit: "片",
        shortage_qty: "12",
        suggested_purchase_qty: "未提供",
        recorded_stock_qty: "0",
      },
      {
        material_code: "C",
        name: "物料丙",
        unit: "套",
        shortage_qty: "3",
        recorded_stock_qty: "2",
      },
    ],
    "production",
  );
  assert.deepEqual(
    groups.map((group) => group.unit),
    ["片", "套"],
  );
  assert.deepEqual(
    groups[0].items.map((item) => item.code),
    ["B", "A"],
  );
  assert.equal(groups[0].max, 12);
  assert.equal(groups[0].items[1].suggested.text, "8");
  assert.equal(groups[0].items[0].suggested, null); // 未提供不推算
  assert.equal(groups[1].max, 3);
});

test("缺口为 0/负数/已满足的物料不画条", () => {
  const groups = chart.buildShortageGroups(
    [
      {
        material_code: "ZERO",
        unit: "片",
        shortage_qty: "0",
        status: "满足本次需求",
        recorded_stock_qty: "9",
      },
      {
        material_code: "NEG",
        unit: "片",
        shortage_qty: "-2",
        status: "满足本次需求",
        recorded_stock_qty: "9",
      },
      {
        material_code: "OK",
        unit: "片",
        shortage_qty: "未提供",
        status: "满足本次需求",
        recorded_stock_qty: "9",
      },
    ],
    "production",
  );
  assert.equal(groups.length, 0);
});

test("库存未提供/缺口未量化的物料只计数并说明，绝不补值", () => {
  const groups = chart.buildShortageGroups(
    [
      {
        material_code: "G1",
        unit: "片",
        shortage_qty: "2",
        status: "不满足需求",
        recorded_stock_qty: "1",
      },
      {
        material_code: "M1",
        unit: "片",
        shortage_qty: "未提供",
        status: "库存未查询到",
        available_qty: "未提供",
        actual_stock_qty: "未提供",
        recorded_stock_qty: "未提供",
      },
      {
        material_code: "U1",
        unit: "片",
        shortage_qty: "未提供",
        status: "不满足需求",
        recorded_stock_qty: "4",
      },
    ],
    "production",
  );
  assert.equal(groups.length, 1);
  assert.deepEqual(
    groups[0].items.map((item) => item.code),
    ["G1"],
  );
  assert.equal(groups[0].missingStockCount, 1);
  assert.equal(groups[0].unquantifiedCount, 1);
  const summary = chart.shortageSummary(groups);
  assert.match(summary, /1 种库存未提供/);
  assert.match(summary, /1 种缺口数量未提供/);
  assert.match(summary, /均未计入缺口/);
});

test("缺料图只对生产/缺料结果生成", () => {
  assert.equal(
    chart.buildShortageGroups(
      [{ material_code: "A", unit: "片", shortage_qty: "2" }],
      "inventory",
    ).length,
    0,
  );
  assert.equal(chart.buildShortageGroups(undefined, "production").length, 0);
  assert.equal(chart.buildShortageGroups([], "shortage").length, 0);
});

test("缺料摘要给出最大缺口与单位，且不把未提供算成缺口", () => {
  const groups = chart.buildShortageGroups(
    [
      {
        material_code: "A",
        unit: "片",
        shortage_qty: "7",
        status: "不满足需求",
        recorded_stock_qty: "1",
      },
      {
        material_code: "B",
        unit: "片",
        shortage_qty: "未提供",
        status: "库存未查询到",
        recorded_stock_qty: "未提供",
      },
    ],
    "shortage",
  );
  const summary = chart.shortageSummary(groups);
  assert.match(summary, /1 种物料需要处理/);
  assert.match(summary, /最大缺口 7/);
  assert.match(summary, /1 种库存未提供/);
  assert.equal(chart.shortageSummary([]), "");
});

test("库存趋势只接受后端真实历史快照，并按时间排序去重", () => {
  const payload = {
    structured_data: {
      data: {
        inventory_history: [
          {
            material_code: "MAT-1",
            name: "主板",
            unit: "片",
            observed_at: "2026-10-03T00:00:00Z",
            available_qty: "80",
          },
          {
            material_code: "MAT-1",
            name: "主板",
            unit: "片",
            observed_at: "2026-10-01T00:00:00Z",
            available_qty: "100",
          },
          {
            material_code: "MAT-1",
            name: "主板",
            unit: "片",
            observed_at: "2026-10-03T00:00:00Z",
            available_qty: "80",
          },
          {
            material_code: "MAT-1",
            name: "主板",
            unit: "片",
            observed_at: "2026-10-02T00:00:00Z",
            available_qty: "90",
          },
        ],
      },
    },
  };
  const series = chart.buildInventoryTrends(payload);
  assert.equal(series.length, 1);
  assert.deepEqual(
    series[0].points.map((point) => point.text),
    ["100", "90", "80"],
  );
  assert.deepEqual(
    series[0].points.map((point) => point.at.slice(0, 10)),
    ["2026-10-01", "2026-10-02", "2026-10-03"],
  );
  const summary = chart.trendSummary(series);
  assert.match(summary, /3 个历史快照/);
  assert.match(summary, /2026-10-01 的 100/);
  assert.match(summary, /2026-10-03 的 80/);
});

test("单点、重复时间、缺时间或缺数量都不构成趋势", () => {
  assert.deepEqual(
    chart.buildInventoryTrends({
      structured_data: {
        data: {
          inventory_history: [
            {
              material_code: "A",
              unit: "片",
              observed_at: "2026-10-01T00:00:00Z",
              available_qty: "10",
            },
          ],
        },
      },
    }),
    [],
  );
  assert.deepEqual(
    chart.buildInventoryTrends({
      structured_data: {
        inventory_history: [
          {
            material_code: "A",
            unit: "片",
            observed_at: "2026-10-01T00:00:00Z",
            available_qty: "10",
          },
          {
            material_code: "A",
            unit: "片",
            observed_at: "2026-10-01T00:00:00Z",
            available_qty: "9",
          },
        ],
      },
    }),
    [],
  );
  assert.deepEqual(
    chart.buildInventoryTrends({
      structured_data: {
        inventory_history: [
          {
            material_code: "A",
            unit: "片",
            observed_at: "not-a-date",
            available_qty: "10",
          },
          {
            material_code: "A",
            unit: "片",
            observed_at: "2026-10-02T00:00:00Z",
            available_qty: "未提供",
          },
          {
            material_code: "A",
            unit: "片",
            observed_at: "2026-10-03T00:00:00Z",
            available_qty: "8",
          },
        ],
      },
    }),
    [],
  );
});

test("不同物料/单位各自成线，绝不跨物料连线", () => {
  const series = chart.buildInventoryTrends({
    structured_data: {
      inventory_history: [
        {
          material_code: "A",
          unit: "片",
          snapshot_at: "2026-10-01",
          recorded_stock_qty: "10",
        },
        {
          material_code: "A",
          unit: "片",
          snapshot_at: "2026-10-02",
          recorded_stock_qty: "8",
        },
        {
          material_code: "B",
          unit: "片",
          snapshot_at: "2026-10-01",
          recorded_stock_qty: "5",
        },
        {
          material_code: "B",
          unit: "片",
          snapshot_at: "2026-10-02",
          recorded_stock_qty: "6",
        },
      ],
    },
  });
  assert.equal(series.length, 2);
  assert.deepEqual(series.map((item) => item.code).sort(), ["A", "B"]);
  assert.ok(series.every((item) => item.points.length === 2));
});

test("没有真实历史数组时不生成折线，也不从单次查询推断", () => {
  assert.deepEqual(chart.buildInventoryTrends(undefined), []);
  assert.deepEqual(
    chart.buildInventoryTrends({
      answer: "ok",
      structured_data: { data: { materials: [{ material_code: "A" }] } },
    }),
    [],
  );
  assert.deepEqual(
    chart.buildInventoryTrends({
      structured_data: { inventory_history: { not: "an array" } },
    }),
    [],
  );
  assert.equal(chart.trendSummary([]), "");
});

test("趋势数组可从 structured_data/data/根级读取，但只认白名单字段", () => {
  const rows = [
    {
      material_code: "A",
      unit: "片",
      observed_at: "2026-10-01",
      available_qty: "4",
    },
    {
      material_code: "A",
      unit: "片",
      observed_at: "2026-10-02",
      available_qty: "3",
    },
  ];
  assert.equal(
    chart.buildInventoryTrends({ inventory_history: rows }).length,
    1,
  );
  assert.equal(
    chart.buildInventoryTrends({ structured_data: { inventory_history: rows } })
      .length,
    1,
  );
  assert.equal(
    chart.buildInventoryTrends({
      structured_data: { data: { stock_history: rows } },
    }).length,
    1,
  );
  assert.equal(
    chart.buildInventoryTrends({
      structured_data: { data: { guessed_trend: rows } },
    }).length,
    0,
  );
});
