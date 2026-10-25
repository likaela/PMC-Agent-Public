import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

// Compile only into memory: no build, cache, temporary module or backend calls.
const frontend = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dependencies = process.env.PMC_FRONTEND_DEPENDENCY_ROOT || frontend;
const dependencyRequire = createRequire(join(dependencies, "package.json"));
const ts = dependencyRequire("typescript");
const vue = dependencyRequire("vue");
const { parse, compileScript } = dependencyRequire("@vue/compiler-sfc");
let memoryStorage;
function resetBrowser() {
  memoryStorage = new Map();
  globalThis.localStorage = {
    getItem: (key) => memoryStorage.get(key) ?? null,
    setItem: (key, value) => memoryStorage.set(key, String(value)),
    removeItem: (key) => memoryStorage.delete(key),
  };
  globalThis.fetch = async () => { throw new Error("Unexpected network transport"); };
}
function modules(overrides = {}) {
  const cache = new Map();
  function evaluate(source, filename) {
    const output = ts.transpileModule(source.replaceAll("import.meta.env", '({ VITE_STREAMLIT: "false" })'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    const module = { exports: {} };
    new Function("require", "module", "exports", output)((name) => {
      if (name in overrides) return overrides[name];
      if (name === "vue") return vue;
      if (name.endsWith(".vue")) return {};
      if (name === "./streamlitBridge") return { streamlitRequest() { throw new Error("Bridge disabled in this test"); } };
      if (name.startsWith(".")) return load(resolve(dirname(filename), name) + ".ts");
      throw new Error(`Unexpected module ${name}`);
    }, module, module.exports);
    return module.exports;
  }
  function load(filename) {
    filename = resolve(filename);
    if (cache.has(filename)) return cache.get(filename);
    const sourceFile = existsSync(filename) ? filename : resolve(dependencies, filename.slice(frontend.length + 1));
    const exported = evaluate(readFileSync(sourceFile, "utf8"), filename);
    cache.set(filename, exported);
    return exported;
  }
  return { load: (name) => load(resolve(frontend, name)), evaluate };
}
const binding = { session_id: "session-test", conversation_id: "conversation-test", product_code: "TEST-PRODUCT", mode: "current", scenario_id: null };
function payload(kind = "preview", changes = {}) {
  return {
    intent: "production_capacity", status: "ok", answer: kind === "preview" ? "仅预览，尚未执行。" : "库存执行回执。",
    session_id: binding.session_id, conversation_id: binding.conversation_id, turn_id: `turn-${kind}`,
    structured_data: { operation: `inventory_${kind}`, data: {
      product: binding.product_code, requested_quantity: 2, product_unit: "片", session_id: binding.session_id,
      preview_id: "preview-test", expires_at: "2099-01-01T00:00:00+00:00", can_execute: true, execution_available: true,
      executed: kind === "execution", status: kind === "execution" ? "executed" : "preview",
      materials: [{ material_code: "TEST-MAT", name: "Test material", required_qty: "2.00", available_qty: "10.00", shortage_qty: "0.00", unit: "片" }],
      allocations: [{ material_code: "TEST-MAT", batch_no: "LOT-A", received_at: "2026-01-01", allocated_qty: "2.00", available_before: "10.00", available_after: "8.00" }],
      ...changes,
    } }, provenance: { source_file: "synthetic-inventory.json" }, warnings: [],
  };
}
async function prepared(options = {}) {
  resetBrowser();
  const runtime = modules();
  const adapt = runtime.load("src/services/responseAdapter.ts").adaptBackendResponse;
  const { createAgentStore } = runtime.load("src/composables/agentStore.ts");
  const calls = { query: 0, execute: [], history: 0, receipt: 0 };
  let saved = [];
  const service = {
    catalog: async () => [{ product_code: binding.product_code, product_name: "Synthetic fixture", synthetic: false, default_mode: "current", modes: [{ mode: "current", scenario_id: null }] }],
    resolve: async () => ({ status: "ok", product_code: binding.product_code, catalog_query: false }),
    createSession: async () => ({ session_id: binding.session_id, conversation_id: binding.conversation_id }),
    query: async () => { calls.query++; return adapt(options.preview || payload()); },
    executeInventory: async (input, credential, signal) => {
      calls.execute.push({ input, credential, signal });
      if (options.execute) return options.execute(input, credential, signal, runtime);
      return adapt(payload("execution"));
    },
    history: async () => { calls.history++; return { session_id: binding.session_id, conversation_id: binding.conversation_id, turns: saved }; },
    getInventoryReceipt: async (input, signal) => {
      calls.receipt++;
      if (options.receipt) return options.receipt(input, signal, runtime);
      const missing = payload("execution", { executed: false, status: "not_executed" });
      missing.status = "data_unavailable";
      return adapt(missing);
    },
  };
  const store = createAgentStore(service, { minimumPendingMs: 0 });
  await store.initialize();
  await store.send("生成领料预览");
  return { runtime, adapt, store, calls, setHistory: (turns) => { saved = turns; } };
}

test("preview preserves exact allocations and never claims an execution", () => {
  resetBrowser();
  const adapt = modules().load("src/services/responseAdapter.ts").adaptBackendResponse;
  const result = adapt(payload());
  const operation = result.backend.inventoryOperation;
  assert.equal(operation.kind, "preview");
  assert.equal(operation.executed, false);
  assert.equal(operation.allocations[0].quantity, "2.00");
  assert.equal(operation.allocations[0].before, "10.00");
  assert.equal(operation.allocations[0].after, "8.00");
  assert.equal(result.backend.rows[0].required_qty, "2.00");
  assert.ok(result.backend.raw.provenance);
});
test("execution is available only when the backend explicitly enables it", () => {
  resetBrowser();
  const adapt = modules().load("src/services/responseAdapter.ts").adaptBackendResponse;
  const result = payload();
  delete result.structured_data.data.execution_available;
  assert.equal(adapt(result).backend.inventoryOperation.executionAvailable, false);
  result.structured_data.operation = "public_inventory_preview";
  assert.equal(adapt(result).backend.inventoryOperation.kind, "preview");
  result.status = "data_unavailable";
  assert.equal(adapt(result).backend.inventoryOperation.canExecute, false);
});
test("a failure or an executed-looking preview cannot become a successful receipt", () => {
  resetBrowser();
  const adapt = modules().load("src/services/responseAdapter.ts").adaptBackendResponse;
  assert.equal(adapt(payload("preview", { executed: true, status: "executed" })).backend.inventoryOperation.executed, false);
  const denied = payload("execution");
  denied.status = "forbidden";
  assert.equal(adapt(denied).backend.inventoryOperation.executed, false);
});
test("persisted movements are displayed without recomputing their balances", () => {
  resetBrowser();
  const adapt = modules().load("src/services/responseAdapter.ts").adaptBackendResponse;
  const receipt = adapt(payload("execution", { movements: [{ material_code: "TEST-MAT", batch_no: "LOT-B", quantity: "2.00", before_qty: "30.50", after_qty: "28.50" }] }));
  assert.equal(receipt.backend.inventoryOperation.executed, true);
  assert.equal(receipt.backend.inventoryOperation.allocations[0].batch, "LOT-B");
  assert.equal(receipt.backend.inventoryOperation.allocations[0].after, "28.50");
});
test("query sends only the query endpoint, even for an execution-looking question", async () => {
  resetBrowser();
  const requests = [];
  globalThis.fetch = async (url, init) => { requests.push({ url, init }); return new Response(JSON.stringify(payload()), { status: 200 }); };
  const service = modules().load("src/services/apiClient.ts").createLiveService();
  await service.query({ text: "确认执行库存扣减", session: binding }, new AbortController().signal);
  assert.deepEqual(requests.map((request) => request.url), ["/api/v1/agent/query"]);
  assert.equal(requests[0].init.headers["X-Inventory-Write-Token"], undefined);
});
test("execute client requires confirmed input and transmits the credential only in the header", async () => {
  resetBrowser();
  const requests = [];
  globalThis.fetch = async (url, init) => { requests.push({ url, init }); return new Response(JSON.stringify(payload("execution")), { status: 200 }); };
  const service = modules().load("src/services/apiClient.ts").createLiveService();
  const input = { session_id: binding.session_id, preview_id: "preview-test", confirmed: true };
  await assert.rejects(service.executeInventory({ ...input, confirmed: false }, "test-secret", new AbortController().signal));
  assert.equal(requests.length, 0);
  await service.executeInventory(input, "test-secret", new AbortController().signal);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/v1/inventory/execute");
  assert.equal(requests[0].init.headers["X-Inventory-Write-Token"], "test-secret");
  assert.deepEqual(JSON.parse(requests[0].init.body), input);
  assert.ok(!requests[0].init.body.includes("test-secret"));
  assert.ok(!JSON.stringify([...memoryStorage]).includes("test-secret"));
});
test("an HTTP execution failure is never retried by the client", async () => {
  resetBrowser();
  let requests = 0;
  globalThis.fetch = async () => { requests++; return new Response(JSON.stringify({ code: "WRITE_DISABLED", user_message: "未开放库存执行" }), { status: 403 }); };
  const service = modules().load("src/services/apiClient.ts").createLiveService();
  await assert.rejects(service.executeInventory({ session_id: binding.session_id, preview_id: "preview-test", confirmed: true }, "test-secret", new AbortController().signal), (error) => error.httpStatus === 403);
  assert.equal(requests, 1);
});
test("an already cancelled execution never sends HTTP", async () => {
  resetBrowser();
  let requests = 0;
  globalThis.fetch = async () => { requests++; throw new Error("Unexpected request"); };
  const service = modules().load("src/services/apiClient.ts").createLiveService();
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(service.executeInventory({ session_id: binding.session_id, preview_id: "preview-test", confirmed: true }, "test-secret", controller.signal), (error) => error.name === "AbortError");
  assert.equal(requests, 0);
});
test("durable receipt recovery is a GET without an execution credential or request body", async () => {
  resetBrowser();
  const requests = [];
  globalThis.fetch = async (url, init) => { requests.push({ url, init }); return new Response(JSON.stringify(payload("execution")), { status: 200 }); };
  const service = modules().load("src/services/apiClient.ts").createLiveService();
  const result = await service.getInventoryReceipt({ session_id: "session/test", preview_id: "preview/test" }, new AbortController().signal);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/v1/inventory/receipts/preview%2Ftest?session_id=session%2Ftest");
  assert.equal(requests[0].init.method, "GET");
  assert.equal(requests[0].init.body, undefined);
  assert.equal(requests[0].init.headers["X-Inventory-Write-Token"], undefined);
  assert.equal(result.backend.inventoryOperation.executed, true);
});
test("store query creates a preview only; execution additionally requires explicit confirmation", async () => {
  const { store, calls } = await prepared();
  assert.equal(calls.query, 1);
  assert.equal(calls.execute.length, 0);
  await store.executeInventory("test-secret");
  assert.equal(calls.execute.length, 0);
  await store.executeInventory("test-secret", true);
  assert.equal(calls.execute.length, 1);
  assert.equal(store.result.value.backend.inventoryOperation.executed, true);
  assert.equal(calls.execute[0].input.preview_id, "preview-test");
  assert.ok(!JSON.stringify(store.messages.value).includes("test-secret"));
});
for (const [name, changes] of [
  ["expired", { expires_at: "2000-01-01T00:00:00Z" }],
  ["invalid expiry", { expires_at: "not-a-date" }],
  ["missing expiry", { expires_at: null }],
  ["blocked preview", { can_execute: false }],
  ["write-disabled environment", { execution_available: false }],
  ["other session", { session_id: "other-session" }],
  ["other product", { product: "OTHER-PRODUCT" }],
]) test(`${name} cannot send a store execution request`, async () => {
  const { store, calls } = await prepared({ preview: payload("preview", changes) });
  await store.executeInventory("test-secret", true);
  assert.equal(calls.execute.length, 0);
});
test("an older selected preview is not executable after a newer query", async () => {
  const { store, calls } = await prepared();
  const previous = store.selectedId.value;
  await store.send("重新生成预览");
  store.select(previous);
  await store.executeInventory("test-secret", true);
  assert.equal(calls.execute.length, 0);
});
test("uncertain execution recovers history, never retries or resumes queries without a receipt", async () => {
  const { store, calls, setHistory } = await prepared({ execute: async () => { throw new Error("Response lost"); } });
  await store.executeInventory("test-secret", true);
  assert.equal(calls.execute.length, 1);
  assert.equal(store.needsSync.value, true);
  await store.retry();
  assert.equal(calls.execute.length, 1);
  assert.equal(store.needsSync.value, true);
  assert.equal(calls.query, 1);
  setHistory([{ turn_id: "saved-receipt", sequence_no: 2, user_query: "确认执行库存扣减", created_at: "2026-01-01", response: payload("execution"), fact_references: [] }]);
  await store.retry();
  assert.equal(calls.execute.length, 1);
  assert.equal(store.result.value.backend.inventoryOperation.executed, true);
  assert.equal(store.needsSync.value, false);
});
test("a committed durable receipt recovers a missing history turn without a second write", async () => {
  const { store, calls } = await prepared({
    execute: async () => { throw new Error("History synchronization failed after commit"); },
    receipt: async (_input, _signal, runtime) => runtime.load("src/services/responseAdapter.ts").adaptBackendResponse(payload("execution")),
  });
  await store.executeInventory("test-secret", true);
  assert.equal(store.needsSync.value, true);
  await store.retry();
  assert.equal(calls.history, 1);
  assert.equal(calls.receipt, 1);
  assert.equal(calls.execute.length, 1);
  assert.equal(store.result.value.backend.inventoryOperation.executed, true);
  assert.equal(store.needsSync.value, false);
});
test("restoring a saved preview checks its durable receipt before offering execution again", async () => {
  const { store, calls, setHistory } = await prepared({ receipt: async (_input, _signal, runtime) =>
    runtime.load("src/services/responseAdapter.ts").adaptBackendResponse(payload("execution")) });
  setHistory([{ turn_id: "saved-preview", sequence_no: 1, user_query: "生成领料预览", created_at: "2026-01-01", response: payload(), fact_references: [] }]);
  await store.loadHistory();
  assert.equal(calls.receipt, 1);
  assert.equal(calls.execute.length, 0);
  assert.equal(store.result.value.backend.inventoryOperation.kind, "execution");
  assert.equal(store.result.value.backend.inventoryOperation.executed, true);
});
test("a mismatched durable receipt leaves the execution unresolved", async () => {
  const { store, calls } = await prepared({ execute: async () => { throw new Error("Response lost"); },
    receipt: async (_input, _signal, runtime) => runtime.load("src/services/responseAdapter.ts").adaptBackendResponse(payload("execution", { preview_id: "other-preview" })) });
  await store.executeInventory("test-secret", true);
  await store.retry();
  assert.equal(calls.execute.length, 1);
  assert.equal(store.needsSync.value, true);
  assert.equal(store.connectionError.value.backendCode, "EXECUTION_BINDING_MISMATCH");
  assert.equal(store.result.value, undefined);
});
for (const status of ["preview_expired", "stale_preview", "data_unavailable", "confirmation_required", "task_conflict", "invalid_query"])
  test(`an explicit ${status} execution rejection is not an uncertain transaction`, async () => {
    const { store, calls } = await prepared({ execute: async (_input, _credential, _signal, runtime) => {
      const rejected = payload("execution", { executed: false, status: "not_executed" });
      rejected.status = status;
      return runtime.load("src/services/responseAdapter.ts").adaptBackendResponse(rejected);
    } });
    await store.executeInventory("test-secret", true);
    assert.equal(calls.execute.length, 1);
    assert.equal(store.selected.value.state, "done");
    assert.equal(store.result.value.backend.status, status);
    assert.equal(store.result.value.backend.inventoryOperation.executed, false);
    assert.equal(store.needsSync.value, false);
  });
test("a legacy explicit expired response with no data is still a safe rejection", async () => {
  const { store, calls } = await prepared({ execute: async (_input, _credential, _signal, runtime) => {
    const rejected = payload("execution");
    rejected.status = "preview_expired";
    rejected.structured_data.data = null;
    return runtime.load("src/services/responseAdapter.ts").adaptBackendResponse(rejected);
  } });
  await store.executeInventory("test-secret", true);
  assert.equal(calls.execute.length, 1);
  assert.equal(store.selected.value.state, "done");
  assert.equal(store.result.value.backend.status, "preview_expired");
  assert.equal(store.needsSync.value, false);
});
test("a receipt with a different preview or session is not accepted as success", async () => {
  const { store, calls } = await prepared({ execute: async (_input, _credential, _signal, runtime) => {
    const wrong = payload("execution", { preview_id: "other-preview" });
    wrong.session_id = "other-session";
    return runtime.load("src/services/responseAdapter.ts").adaptBackendResponse(wrong);
  } });
  await store.executeInventory("test-secret", true);
  assert.equal(calls.execute.length, 1);
  assert.equal(store.selected.value.state, "error");
  assert.equal(store.result.value, undefined);
  assert.equal(store.needsSync.value, true);
});
test("an explicit authorization refusal remains a refusal and has no automatic write retry", async () => {
  const { store, calls } = await prepared({ execute: async (_input, _credential, _signal, runtime) => {
    const { QueryError } = runtime.load("src/data/model.ts");
    throw new QueryError("network", "当前环境未开放库存执行。", "WRITE_DISABLED", 403);
  } });
  await store.executeInventory("test-secret", true);
  assert.equal(store.selected.value.state, "error");
  assert.equal(store.result.value, undefined);
  await store.retry();
  assert.equal(calls.execute.length, 1);
});
test("cancelling an execution ignores a late response and requires receipt recovery", async () => {
  let deliver;
  const { store, calls, adapt } = await prepared({ execute: () => new Promise((resolve) => { deliver = resolve; }) });
  const executing = store.executeInventory("test-secret", true);
  store.cancel();
  deliver(adapt(payload("execution")));
  await executing;
  assert.equal(calls.execute.length, 1);
  assert.equal(store.selected.value.state, "cancelled");
  assert.equal(store.result.value, undefined);
  assert.equal(store.needsSync.value, true);
});
test("the real confirmation component clears its credential before its single explicit action", async () => {
  resetBrowser();
  const cleanups = [], sent = [];
  const base = modules();
  const adapted = base.load("src/services/responseAdapter.ts").adaptBackendResponse(payload());
  const agent = { result: vue.ref(adapted), pending: vue.ref(false), selected: vue.ref({ id: 1 }),
    retry() {}, canSend: vue.ref(true), session: vue.ref(binding), messages: vue.ref([{ id: 1 }]),
    executeInventory: (...args) => { sent.push(args); } };
  const runtime = modules({ vue: { ...vue, onMounted() {}, onBeforeUnmount(callback) { cleanups.push(callback); } },
    "../composables/useAgent": { useAgent: () => agent } });
  const filename = resolve(frontend, "src/components/ResultPanel.vue");
  const descriptor = parse(readFileSync(filename, "utf8")).descriptor;
  const compiled = compileScript(descriptor, { id: "inventory-test" });
  const component = runtime.evaluate(compiled.content, filename).default;
  const view = component.setup({}, { expose() {}, emit() {} });
  view.credential.value = "test-secret";
  view.confirmExecution();
  assert.equal(sent.length, 0);
  assert.equal(view.credential.value, "");
  view.confirmed.value = true;
  view.credential.value = "test-secret";
  view.confirmExecution();
  assert.deepEqual(sent, [["test-secret", true]]);
  assert.equal(view.credential.value, "");
  assert.equal(view.confirmed.value, false);
  view.confirmExecution();
  assert.equal(sent.length, 1);
  view.credential.value = "second-secret";
  cleanups.forEach((cleanup) => cleanup());
  assert.equal(view.credential.value, "");
});
