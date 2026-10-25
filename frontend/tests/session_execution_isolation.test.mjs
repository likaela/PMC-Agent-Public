import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

// 只编译到内存：不写构建产物、不访问后端、不写缓存。
const frontend = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dependencies = process.env.PMC_FRONTEND_DEPENDENCY_ROOT || frontend;
const dependencyRequire = createRequire(join(dependencies, "package.json"));
const ts = dependencyRequire("typescript");
const vue = dependencyRequire("vue");
let memoryStorage;

function resetBrowser() {
  memoryStorage = new Map();
  globalThis.localStorage = {
    getItem: (key) => memoryStorage.get(key) ?? null,
    setItem: (key, value) => memoryStorage.set(key, String(value)),
    removeItem: (key) => memoryStorage.delete(key),
  };
  globalThis.fetch = async () => {
    throw new Error("Unexpected network transport");
  };
}

function modules() {
  const cache = new Map();
  function load(filename) {
    filename = resolve(filename);
    if (cache.has(filename)) return cache.get(filename);
    const source = existsSync(filename)
      ? filename
      : resolve(dependencies, filename.slice(frontend.length + 1));
    const output = ts.transpileModule(
      readFileSync(source, "utf8").replaceAll("import.meta.env", '({ VITE_STREAMLIT: "false" })'),
      { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
    ).outputText;
    const module = { exports: {} };
    new Function("require", "module", "exports", output)((name) => {
      if (name === "vue") return vue;
      if (name.endsWith(".vue")) return {};
      if (name === "./streamlitBridge")
        return { streamlitRequest() { throw new Error("Bridge disabled in this test"); } };
      if (name.startsWith(".")) return load(resolve(dirname(filename), name) + ".ts");
      throw new Error(`Unexpected module ${name}`);
    }, module, module.exports);
    cache.set(filename, module.exports);
    return module.exports;
  }
  return { load };
}

const product = "TEST-PRODUCT";
const sessionA = { session_id: "session-A", conversation_id: "conv-A", product_code: product, mode: "current", scenario_id: null };
const sessionB = { session_id: "session-B", conversation_id: "conv-B", product_code: product, mode: "current", scenario_id: null };

function previewPayload(sessionId, previewId) {
  return {
    intent: "production_capacity", status: "ok", answer: "已生成领料预览，库存尚未扣减。",
    session_id: sessionId, turn_id: `turn-${sessionId}`,
    structured_data: {
      operation: "inventory_preview",
      data: {
        product, requested_quantity: 3, product_unit: "片", session_id: sessionId,
        preview_id: previewId, expires_at: "2099-01-01T00:00:00+00:00",
        can_execute: true, execution_available: true, executed: false,
        materials: [{ material_code: "TEST-A", name: "Synthetic", required_qty: "6", available_qty: "10", shortage_qty: "0", unit: "片" }],
        allocations: [],
      },
    },
    warnings: [],
  };
}

function executionPayload(sessionId, previewId) {
  return {
    intent: "production_capacity", status: "ok", answer: "本次演示库存扣减已完成。",
    session_id: sessionId, turn_id: `execution-${sessionId}`,
    structured_data: {
      operation: "inventory_execution",
      data: {
        product, requested_quantity: 3, product_unit: "片", session_id: sessionId,
        preview_id: previewId, executed: true, task_id: previewId,
        allocations: [{ material_code: "TEST-A", batch_no: "OLD", received_at: "2024-01-01", allocated_qty: "3", available_before: "10", available_after: "7" }],
        materials: [],
      },
    },
    warnings: [],
  };
}

function harness(options = {}) {
  resetBrowser();
  const runtime = modules();
  const adapt = runtime.load("src/services/responseAdapter.ts").adaptBackendResponse;
  const { createAgentStore } = runtime.load("src/composables/agentStore.ts");
  const calls = { query: 0, execute: 0, history: 0, receipt: 0 };
  const state = { nextSession: sessionA };
  const service = {
    catalog: async () => [{
      product_code: product, product_name: "Synthetic fixture", synthetic: false,
      default_mode: "current", modes: [{ mode: "current", scenario_id: null }],
    }],
    resolve: async () => ({ status: "ok", product_code: product, catalog_query: false }),
    createSession: async () => ({ session_id: state.nextSession.session_id, conversation_id: state.nextSession.conversation_id }),
    query: async () => {
      calls.query++;
      return adapt(previewPayload(state.nextSession.session_id, `preview-${state.nextSession.session_id}`));
    },
    executeInventory: async () => {
      calls.execute++;
      if (options.execute) return options.execute();
      return adapt(executionPayload("session-A", "preview-session-A"));
    },
    history: async (binding) => {
      calls.history++;
      return { session_id: binding.session_id, conversation_id: `conv-${binding.session_id}`, turns: [] };
    },
    getInventoryReceipt: async (input) => {
      calls.receipt++;
      if (options.receipt) return options.receipt(input);
      return adapt(executionPayload(input.session_id, input.preview_id));
    },
  };
  return { runtime, adapt, createAgentStore, service, calls, state };
}

test("一个会话的未知执行状态不会阻塞另一个独立会话", async () => {
  const { createAgentStore, service, calls, state } = harness({
    execute: () => { throw new Error("socket hang up after commit"); },
  });
  const store = createAgentStore(service, { minimumPendingMs: 0 });
  await store.initialize();
  state.nextSession = sessionA;
  await store.send("生产3片并领料");
  assert.equal(store.session.value.session_id, "session-A");
  assert.equal(store.result.value.backend.inventoryOperation.kind, "preview");
  assert.equal(store.canSend.value, true);

  await store.executeInventory("test-secret", true);
  assert.equal(calls.execute, 1);
  // 结果未知：当前会话被要求先核实，不能再次扣减。
  assert.equal(store.needsSync.value, true);
  assert.equal(store.canSend.value, false);

  // 切到独立会话 B：不继承 A 的未决状态，可以正常查询。
  await store.restore(sessionB);
  assert.equal(store.session.value.session_id, "session-B");
  assert.equal(store.needsSync.value, false);
  assert.equal(store.canSend.value, true);
  const before = calls.query;
  await store.send("生产2片够不够");
  assert.equal(calls.query, before + 1);
  assert.equal(store.needsSync.value, false);

  // 回到 A：旧会话的未决证据仍然保留（未被盲目清除）。
  await store.restore(sessionA);
  assert.equal(store.needsSync.value, false); // 已通过原预览查到回执
  assert.equal(calls.receipt, 1);
  assert.equal(store.canSend.value, true);
  assert.match(store.notice.value, /已从库存账本核实执行回执/);
});

test("未知执行状态在原会话内保持待核实，且不会自动重试扣减", async () => {
  const { createAgentStore, service, calls, state } = harness({
    execute: () => { throw new Error("timeout"); },
    receipt: () => { throw new Error("receipt unavailable"); },
  });
  const store = createAgentStore(service, { minimumPendingMs: 0 });
  await store.initialize();
  state.nextSession = sessionA;
  await store.send("生产3片并领料");
  await store.executeInventory("test-secret", true);
  assert.equal(store.needsSync.value, true);
  // 同步失败（回执暂不可用）：仍然待核实，且没有第二次扣减请求。
  await store.loadHistory();
  assert.equal(store.needsSync.value, true);
  assert.equal(store.canSend.value, false);
  assert.equal(calls.execute, 1);
});

test("采购清单只包含可核算物料，与后端结构化结果一致", () => {
  resetBrowser();
  const adapt = modules().load("src/services/responseAdapter.ts").adaptBackendResponse;
  const response = {
    intent: "purchase_planning", status: "ok",
    answer: "已自动生成需要采购的BOM表单：共 1 种物料需采购。另有2种物料未生成确定缺口与采购数量。",
    structured_data: {
      query_type: "purchase_planning",
      data: {
        product, revision: "V01", product_unit: "片", requested_quantity: 10,
        inventory_sufficient_for_requested: false,
        quantity_check_status: "insufficient",
        purchase_material_count: 1,
        unverified_purchase_count: 2,
        recommendations: [{
          material: "SYN-A", name: "Synthetic A", unit: "片",
          required_qty: "10", shortage_qty: "8", suggested_purchase_qty: "8",
        }],
        unverified_purchase_materials: [
          { material: "SYN-B", unit: "片", required_qty: "10", shortage_qty: null, suggested_purchase_qty: null, reason: "库存无记录" },
          { material: "SYN-C", unit: "片", required_qty: "10", shortage_qty: null, suggested_purchase_qty: null, reason: "计量单位冲突" },
        ],
        materials: [],
      },
    },
    warnings: [],
  };
  const result = adapt(response);
  const rows = result.backend.rows;
  assert.equal(rows.length, response.structured_data.data.purchase_material_count);
  assert.deepEqual(rows.map((row) => row.material_code), ["SYN-A"]);
  assert.equal(rows[0].suggested_purchase_qty, "8");
  assert.ok(rows.every((row) => row.suggested_purchase_qty !== "未提供"));
  const listed = new Set(rows.map((row) => row.material_code));
  for (const item of response.structured_data.data.unverified_purchase_materials) {
    assert.equal(listed.has(item.material), false, "待核验物料不得出现在采购清单");
  }
  assert.equal(result.backend.metrics.find((m) => m.label === "产品").value, product);
  assert.ok(result.backend.columns.some((column) => column.key === "suggested_purchase_qty"));
});
