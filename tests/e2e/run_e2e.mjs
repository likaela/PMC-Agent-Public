// 自然语言前端 E2E 执行器（Playwright + 本机 Chrome，127.0.0.1 回环）。
//
// 每条用例都在真实页面里输入自然语言、按 Enter 发送、等待真实后端返回，
// 再从「聊天气泡 + 分析结论区 + 浏览器网络响应」三处取证。
//
// 用法：
//   node tests/e2e/run_e2e.mjs --cases tests/e2e/natural_language_cases.json \
//        --out <临时目录> [--only NL-001,NL-002] [--limit 10] [--env public|simulation]
//
// 退出码：0 全部通过；1 存在失败；2 执行器自身错误。
import { createRequire } from "node:module";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const PROJECT = resolve(HERE, "../..");
const require = createRequire(join(PROJECT, "frontend/package.json"));
const { chromium } = require("playwright");

function arg(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index > -1 ? process.argv[index + 1] : fallback;
}
const CASES_FILE = resolve(arg("cases", join(HERE, "natural_language_cases.json")));
const OUT = resolve(arg("out", join(process.env.TEMP || "/tmp", "pmc-e2e")));
const ONLY = (arg("only", "") || "").split(",").map((s) => s.trim()).filter(Boolean);
const LIMIT = Number(arg("limit", "0")) || 0;
const ENV_FILTER = arg("env", "");
const BASES = {
  public: arg("public", "http://127.0.0.1:18801"),
  simulation: arg("simulation", "http://127.0.0.1:18802"),
};
const CONCURRENCY = Number(arg("concurrency", "1")) || 1;
const WAIT_MS = Number(arg("timeout", "45000"));

const library = JSON.parse(readFileSync(CASES_FILE, "utf8"));
let cases = library.cases;
if (ONLY.length) cases = cases.filter((c) => ONLY.includes(c.id));
if (ENV_FILTER) cases = cases.filter((c) => c.env === ENV_FILTER);
if (LIMIT) cases = cases.slice(0, LIMIT);

mkdirSync(OUT, { recursive: true });
mkdirSync(join(OUT, "screenshots"), { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function textOf(step) {
  if (step.say_repeat) return step.say_repeat.char.repeat(step.say_repeat.times);
  return step.say ?? "";
}

/* ---------------------------------------------------------------- 断言求值 */
function pick(root, path) {
  if (!path) return root;
  return path.split(".").reduce((value, key) => {
    if (value === null || value === undefined) return undefined;
    if (key === "length" && Array.isArray(value)) return value.length;
    return value[key];
  }, root);
}

function asText(value) {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : JSON.stringify(value);
}

function evaluate(check, ctx) {
  const { answer, chat, panel, query, resolveData, ledger, responses } = ctx;
  switch (check.kind) {
    case "answer": {
      const text = answer;
      const missing = (check.contains || []).filter((item) => !text.includes(item));
      const leaked = (check.not_contains || []).filter((item) => text.includes(item));
      const regexOk = !check.regex || new RegExp(check.regex).test(text);
      const ok = missing.length === 0 && leaked.length === 0 && regexOk;
      return { ok, detail: { missing, leaked, regex: check.regex || null, text: text.slice(0, 160) } };
    }
    case "chat": {
      const missing = (check.contains || []).filter((item) => !chat.includes(item));
      return { ok: missing.length === 0, detail: { missing, text: chat.slice(0, 160) } };
    }
    case "panel": {
      const missing = (check.contains || []).filter((item) => !panel.includes(item));
      return { ok: missing.length === 0, detail: { missing, text: panel.slice(0, 160) } };
    }
    case "status": {
      const actual = (query && query.status) || null;
      return { ok: actual === check.value, detail: { expected: check.value, actual } };
    }
    case "intent": {
      const actual = (query && query.intent) || null;
      return { ok: actual === check.value, detail: { expected: check.value, actual } };
    }
    case "query_type": {
      const actual = query && query.structured_data ? query.structured_data.query_type : null;
      return { ok: actual === check.value, detail: { expected: check.value, actual } };
    }
    case "resolve": {
      const actual = resolveData ? resolveData.status : null;
      const product = resolveData ? resolveData.product_code : null;
      const okProduct = check.product === undefined || product === check.product;
      return { ok: actual === check.value && okProduct, detail: { expected: check.value, actual, product } };
    }
    case "field": {
      const root = check.scope === "query" || !check.scope ? query : query?.structured_data?.data;
      const actual = pick(root, check.path);
      let ok = false;
      switch (check.op || "eq") {
        case "eq": ok = actual === check.value; break;
        case "ne": ok = actual !== check.value; break;
        case "gt": ok = Number(actual) > Number(check.value); break;
        case "gte": ok = Number(actual) >= Number(check.value); break;
        case "lt": ok = Number(actual) < Number(check.value); break;
        case "lte": ok = Number(actual) <= Number(check.value); break;
        case "truthy": ok = Boolean(actual); break;
        case "falsy": ok = !actual; break;
        case "contains": ok = asText(actual).includes(String(check.value)); break;
        case "len_eq": ok = Array.isArray(actual) && actual.length === check.value; break;
        case "len_gte": ok = Array.isArray(actual) && actual.length >= check.value; break;
        case "in": ok = (check.value || []).includes(actual); break;
        case "not_in": ok = !(check.value || []).includes(actual); break;
        case "nonempty": ok = Array.isArray(actual) ? actual.length > 0 : Boolean(actual); break;
        default: throw new Error(`unknown op ${check.op}`);
      }
      return { ok, detail: { path: check.path, op: check.op || "eq", expected: check.value, actual } };
    }
    case "ledger": {
      if (!ledger) return { ok: false, detail: { error: "simulation ledger state unavailable" } };
      // 断言按「本条用例新增」口径，避免同一合成账本被前序用例污染。
      const base = ctx.ledgerBase || { executions: [], movements: [] };
      const executions = (ledger.executions || []).length - (base.executions || []).length;
      const movements = (ledger.movements || []).length - (base.movements || []).length;
      if (check.executions !== undefined && executions !== check.executions) {
        return { ok: false, detail: { expected: check.executions, actual: executions } };
      }
      if (check.no_execution && executions !== 0) {
        return { ok: false, detail: { expected: 0, actual: executions } };
      }
      if (check.movements !== undefined && movements !== check.movements) {
        return { ok: false, detail: { expected: check.movements, actual: movements } };
      }
      if (check.deducted_gt !== undefined) {
        const before = (base.movements || []).length;
        const total = (ledger.movements || []).slice(before)
          .reduce((sum, m) => sum + Number(m.deducted_qty || 0), 0);
        if (!(total > Number(check.deducted_gt))) return { ok: false, detail: { expected: `>${check.deducted_gt}`, actual: total } };
      }
      return { ok: true, detail: { executions, movements } };
    }
    case "http": {
      const hit = responses.filter((r) => check.path && r.url.includes(check.path)).pop();
      const statusOk = check.status === undefined || (hit && hit.status === check.status);
      const absentOk = check.absent ? !hit : true;
      return { ok: Boolean(statusOk && absentOk), detail: { path: check.path, status: hit ? hit.status : null, absent: !hit } };
    }
    case "notice": {
      const text = ctx.notice || "";
      const ok = check.contains === undefined || text.includes(check.contains);
      return { ok, detail: { expected: check.contains || null, actual: text } };
    }
    case "answer_len": {
      const length = (ctx.answer || "").length;
      const ok = check.gte === undefined || length >= check.gte;
      return { ok, detail: { gte: check.gte, actual: length } };
    }
    default:
      throw new Error(`unknown check kind ${check.kind}`);
  }
}

/* ------------------------------------------------------------------ 页面操作 */
async function openWorkspace(page, base) {
  await page.goto(`${base}/#/workspace`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#query-input", { timeout: WAIT_MS });
  // 等首轮 catalog 拉取结束（连接提示消失或出现上下文行）
  await page.waitForFunction(() => !document.querySelector(".backend-note"), null, { timeout: WAIT_MS }).catch(() => {});
  await sleep(250);
}

async function submitUtterance(page, text) {
  const input = page.locator("#query-input");
  await input.click();
  await input.fill(text);
  await page.waitForFunction(() => {
    const el = document.querySelector("#query-input");
    return el && el.value.trim().length > 0;
  });
  const started = Date.now();
  await input.press("Enter");
  return started;
}

async function waitForSettled(page, question) {
  // 按「最后一条问题文本」等待，而不是按消息条数：切换产品时前端会清空旧消息。
  await page.waitForFunction(
    (text) => {
      const pairs = document.querySelectorAll(".message-pair");
      if (!pairs.length) return false;
      const last = pairs[pairs.length - 1];
      const asked = last.querySelector(".user-message div");
      if (!asked || asked.innerText.trim() !== text.trim()) return false;
      if (last.querySelector(".loading-label")) return false;
      const panel = document.querySelector("#analysis-result");
      if (!panel || panel.getAttribute("aria-busy") === "true") return false;
      return Boolean(panel.querySelector(".result-kicker"));
    },
    question,
    { timeout: WAIT_MS },
  );
  await sleep(150);
}

async function snapshotUi(page) {
  return page.evaluate(() => {
    const pairs = [...document.querySelectorAll(".message-pair")];
    const last = pairs[pairs.length - 1];
    const panel = document.querySelector("#analysis-result");
    const pick = (root, selector) => (root && root.querySelector(selector) ? root.querySelector(selector).innerText.trim() : "");
    const noticers = [...document.querySelectorAll(".inline-notice, .conversation-context")].map((n) => n.innerText.trim());
    return {
      messageCount: pairs.length,
      question: last ? pick(last, ".user-message div") : "",
      chat: last ? pick(last, ".assistant-message p") : "",
      panel: panel ? panel.innerText.trim() : "",
      badge: panel ? pick(panel, ".result-kicker") : "",
      notice: noticers.join(" | "),
      error: document.querySelector(".message-pair:last-child .assistant-message p")?.innerText.includes("请求失败") || false,
    };
  });
}

function readBody(response) {
  return response.text().then((text) => {
    try {
      return JSON.parse(text);
    } catch {
      return { _raw: text.slice(0, 500) };
    }
  }).catch(() => null);
}

async function fetchLedger(base) {
  try {
    const response = await fetch(`${base}/api/v1/simulation/state`, { headers: { Accept: "application/json" } });
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

/* -------------------------------------------------------------------- 单条执行 */
async function runCase(browser, entry, baseline) {
  const base = BASES[entry.env === "simulation" ? "simulation" : "public"];
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: "zh-CN" });
  const page = await context.newPage();
  const network = [];
  page.on("response", (response) => {
    const url = response.url();
    if (!/\/api\/v1\/(agent|inventory|simulation)\//.test(url)) return;
    network.push({ url, status: response.status(), response });
  });
  const result = { id: entry.id, title: entry.title, category: entry.category, env: entry.env || "public",
    role: entry.role, steps: [], status: "blocked", failures: [], startedAt: new Date().toISOString() };
  try {
    await openWorkspace(page, base);
    let messageCount = 0;
    let lastPreview = null;
    let lastSession = null;
    let ledgerBase = (entry.env || "public") === "simulation" ? await fetchLedger(base) : null;
    for (const [index, step] of entry.steps.entries()) {
      if (step.action === "reload") {
        await page.reload({ waitUntil: "domcontentloaded" });
        await page.waitForSelector("#query-input", { timeout: WAIT_MS });
        await page.waitForFunction(() => document.querySelectorAll(".message-pair").length > 0, null, { timeout: WAIT_MS }).catch(() => {});
        await sleep(600);
        const ui = await snapshotUi(page);
        result.steps.push({ index, action: "reload", ok: true, ui: { chat: ui.chat, panel: ui.panel.slice(0, 400) }, uiFull: ui });
        continue;
      }
      if (step.action === "new_conversation") {
        const button = page.getByRole("button", { name: /新建对话/ }).first();
        if (await button.count()) await button.click();
        await sleep(600);
        messageCount = 0;
        result.steps.push({ index, action: "new_conversation", ok: true });
        continue;
      }
      if (step.action === "reset") {
        const response = await fetch(`${base}/api/v1/simulation/reset`, { method: "POST",
          headers: { "Content-Type": "application/json" }, body: "{}" });
        await page.reload({ waitUntil: "domcontentloaded" });
        await page.waitForSelector("#query-input", { timeout: WAIT_MS });
        await sleep(500);
        messageCount = 0;
        lastPreview = null;
        lastSession = null;
        ledgerBase = (entry.env || "public") === "simulation" ? await fetchLedger(base) : null;
        result.steps.push({ index, action: "reset", ok: response.ok, http: response.status });
        continue;
      }
      if (step.action === "duplicate_execute" || step.action === "http_check") {
        const path = step.action === "duplicate_execute" ? "/api/v1/inventory/execute" : step.path;
        const payload = step.action === "duplicate_execute"
          ? { session_id: lastSession, preview_id: lastPreview?.previewId, confirmed: true }
          : step.body || {};
        const response = await fetch(`${base}${path}`, { method: step.method || "POST",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        let body = null;
        try { body = await response.json(); } catch { body = null; }
        const ledger = (entry.env || "public") === "simulation" ? await fetchLedger(base) : null;
        const ctx = { answer: "", chat: "", panel: "", query: null, resolveData: null, ledger, ledgerBase, responses: [], notice: "" };
        const evaluations = [];
        for (const check of step.expect?.checks || []) {
          const custom = { ...check };
          if (custom.kind === "http") { custom.status = custom.status; }
          evaluations.push({ check, ...evaluate(custom, { ...ctx, responses: [{ url: path, status: response.status }] }) });
        }
        const failed = evaluations.filter((item) => !item.ok);
        result.steps.push({ index, action: step.action, http: `${response.status} ${path}`,
          body: JSON.stringify(body).slice(0, 400), checks: evaluations.map((i) => ({ check: i.check, ok: i.ok, detail: i.detail })),
          ok: failed.length === 0, ledger: ledger ? { executions: ledger.executions.length, movements: ledger.movements.length } : null });
        for (const item of failed) result.failures.push({ step: index, ...item.detail, check: item.check });
        continue;
      }
      if (step.action === "ui_check") {
        if (step.say !== undefined || step.say_repeat) {
          await page.locator("#query-input").fill(textOf(step));
          await sleep(120);
        }
        const ui = await snapshotUi(page);
        const extra = await page.evaluate(() => {
          const button = document.querySelector(".send-button");
          const area = document.querySelector("#query-input");
          return { sendDisabled: button ? button.disabled : null, draftLength: area ? area.value.length : null,
            messages: document.querySelectorAll(".message-pair").length };
        });
        const ctx = { answer: ui.panel, chat: ui.chat, panel: ui.panel, query: null, resolveData: null,
          ledger: null, responses: [], notice: ui.notice };
        const evaluations = [];
        for (const check of step.expect?.checks || []) {
          if (check.kind === "send_disabled") evaluations.push({ check, ok: extra.sendDisabled === check.value, detail: extra });
          else if (check.kind === "draft_length_lte") evaluations.push({ check, ok: extra.draftLength <= check.value, detail: extra });
          else if (check.kind === "messages_eq") evaluations.push({ check, ok: extra.messages === check.value, detail: extra });
          else evaluations.push({ check, ...evaluate(check, ctx) });
        }
        const failed = evaluations.filter((item) => !item.ok);
        result.steps.push({ index, action: "ui_check", ui: extra, checks: evaluations.map((i) => ({ check: i.check, ok: i.ok, detail: i.detail })), ok: failed.length === 0 });
        for (const item of failed) result.failures.push({ step: index, ...item.detail, check: item.check });
        continue;
      }
      if (step.action === "confirm_execution") {
        const form = page.locator(".inventory-confirmation-form");
        await form.waitFor({ state: "visible", timeout: WAIT_MS });
        await form.locator('input[type="checkbox"]').check();
        await form.locator('input[type="password"]').fill(step.credential || "local-simulation");
        const before = network.length;
        await page.getByRole("button", { name: /确认执行库存扣减/ }).click();
        await page.waitForFunction(() => {
          const body = document.body.innerText;
          return /库存扣减已确认|执行失败|未获通过|没有已确认成功/.test(body);
        }, null, { timeout: WAIT_MS });
        await sleep(800);
        const ui = await snapshotUi(page);
        const calls = [];
        for (const item of network.slice(before)) {
          if (item.url.includes("/inventory/execute")) calls.push(await readBody(item.response));
        }
        const stepRecord = { index, action: "confirm_execution", ok: true, ui: { chat: ui.chat, panel: ui.panel.slice(-600) },
          executed: /库存扣减已确认|已执行/.test(ui.panel), http: calls.map((c) => c && c.status) };
        result.steps.push(stepRecord);
        const checks = step.expect?.checks || [];
        for (const check of checks) {
          const evaluated = evaluate(check, { ...ui, answer: ui.panel, query: null, ledger: await fetchLedger(base), ledgerBase });
          if (!evaluated.ok) result.failures.push({ step: index, check, ...evaluated.detail });
        }
        continue;
      }

      const startedAt = Date.now();
      const before = network.length;
      const utterance = textOf(step).trim();
      await submitUtterance(page, utterance);
      messageCount += 1;
      await waitForSettled(page, utterance);
      const elapsed = Date.now() - startedAt;
      const ui = await snapshotUi(page);

      let query = null, resolveData = null;
      const http = [];
      for (const item of network.slice(before)) {
        const body = await readBody(item.response);
        http.push({ url: item.url.split("?")[0].replace(base, ""), status: item.status });
        if (item.url.includes("/agent/resolve")) resolveData = body;
        if (item.url.includes("/agent/query")) query = body;
        if (body && typeof body === "object" && body.session_id && item.url.includes("/agent/sessions")) {
          lastSession = body.session_id;
        }
      }
      const previewData = query?.structured_data?.data;
      if (previewData && previewData.preview_id) {
        lastPreview = { previewId: previewData.preview_id, executed: previewData.executed === true };
      }
      let ledger = null;
      if ((entry.env || "public") === "simulation") ledger = await fetchLedger(base);

      const ctx = { answer: ui.panel || ui.chat, chat: ui.chat, panel: ui.panel, query, resolveData, ledger, ledgerBase,
        responses: http, notice: ui.notice };
      const checks = [...(step.expect?.checks || [])];
      if (step.expect?.answer_contains || step.expect?.answer_not_contains || step.expect?.answer_regex) {
        checks.push({ kind: "answer", contains: step.expect.answer_contains,
          not_contains: step.expect.answer_not_contains, regex: step.expect.answer_regex });
      }
      const evaluations = [];
      for (const check of checks) evaluations.push({ check, ...evaluate(check, ctx) });
      const failed = evaluations.filter((item) => !item.ok);
      const stepRecord = {
        index, say: textOf(step).slice(0, 200), elapsedMs: elapsed, http: http.map((h) => `${h.status} ${h.url}`),
        intent: query ? query.intent : null, queryStatus: query ? query.status : null,
        queryType: query ? query.structured_data?.query_type : null,
        resolve: resolveData ? { status: resolveData.status, product: resolveData.product_code } : null,
        answer: (query && query.answer) || ui.panel,
        chat: ui.chat, panel: ui.panel.slice(0, 1500), notice: ui.notice,
        structured: summarize(query), ledger: ledger ? { executions: ledger.executions.length, movements: ledger.movements.length } : null,
        checks: evaluations.map((item) => ({ check: item.check, ok: item.ok, detail: item.detail })),
        ok: failed.length === 0,
      };
      result.steps.push(stepRecord);
      for (const item of failed) result.failures.push({ step: index, ...item.detail, check: item.check });
    }
    result.status = result.failures.length === 0 ? "passed" : "failed";
    if (result.status === "failed") {
      const shot = join(OUT, "screenshots", `${entry.id}.png`);
      await page.screenshot({ path: shot, fullPage: false }).catch(() => {});
      result.screenshot = shot;
    }
  } catch (error) {
    result.status = "blocked";
    result.error = String(error).slice(0, 600);
    await page.screenshot({ path: join(OUT, "screenshots", `${entry.id}-error.png`) }).catch(() => {});
  } finally {
    await context.close().catch(() => {});
  }
  return result;
}

function summarize(query) {
  if (!query) return null;
  const data = query.structured_data?.data || null;
  if (!data) return { status: query.status, intent: query.intent, query_type: query.structured_data?.query_type || null };
  const keys = ["product", "requested_quantity", "requested_unit", "requested_unit_source", "product_unit",
    "total_material_count", "comparable_material_count", "uncomputable_material_count",
    "missing_stock_material_count", "purchase_material_count", "inventory_sufficient_for_requested",
    "max_producible_quantity", "quantity_check_status", "unit_conflict_materials",
    "duplicate_unverified_materials", "missing_stock_materials", "synthetic", "scenario"];
  const picked = {};
  for (const key of keys) if (key in data) picked[key] = Array.isArray(data[key]) ? data[key].slice(0, 8) : data[key];
  return { status: query.status, intent: query.intent, query_type: query.structured_data?.query_type || null,
    keys: Object.keys(data).length, sample: picked,
    counts: { materials: Array.isArray(data.materials) ? data.materials.length : undefined,
      shortages: Array.isArray(data.book_shortages) ? data.book_shortages.length : undefined,
      recommendations: Array.isArray(data.recommendations) ? data.recommendations.length : undefined } };
}

/* ---------------------------------------------------------------------- 主流程 */
async function main() {
  const health = {};
  for (const [mode, base] of Object.entries(BASES)) {
    const response = await fetch(`${base}/health`).catch(() => null);
    health[mode] = response ? response.status : "down";
  }
  if (health.public !== 200) throw new Error(`public server unavailable: ${JSON.stringify(health)}`);
  const baselineFile = join(HERE, "baseline.json");
  const baseline = JSON.parse(readFileSync(baselineFile, "utf8"));
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const started = Date.now();
  const results = [];
  let cursor = 0;
  async function worker() {
    while (cursor < cases.length) {
      const entry = cases[cursor++];
      const outcome = await runCase(browser, entry, baseline);
      results.push(outcome);
      console.log(`${outcome.status === "passed" ? "PASS" : outcome.status.toUpperCase()} ${outcome.id} ${entry.title}`);
      writeFileSync(join(OUT, "results.partial.json"), JSON.stringify({ results, health }, null, 1));
    }
  }
  try {
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, cases.length) }, worker));
  } finally {
    await browser.close();
  }
  results.sort((a, b) => a.id.localeCompare(b.id));
  const summary = {
    total: results.length,
    passed: results.filter((r) => r.status === "passed").length,
    failed: results.filter((r) => r.status === "failed").length,
    blocked: results.filter((r) => r.status === "blocked").length,
    skipped: results.filter((r) => r.status === "skipped").length,
    durationSeconds: Math.round((Date.now() - started) / 1000),
    health,
    env: { chrome: browser.version ? browser.version() : null, node: process.version, bases: BASES },
  };
  writeFileSync(join(OUT, "results.json"), JSON.stringify({ summary, results, baseline }, null, 1));
  console.log(`\n${JSON.stringify(summary)}`);
  process.exit(summary.failed || summary.blocked ? 1 : 0);
}

main().catch((error) => {
  console.error("runner error", error);
  process.exit(2);
});
