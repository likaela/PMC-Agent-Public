<script setup lang="ts">
import { computed, ref } from "vue";
import { useAgent } from "./useReviewAgent";
import MessageList from "../src/components/MessageList.vue";
import QueryComposer from "../src/components/QueryComposer.vue";
import ResultPanel from "../src/components/ResultPanel.vue";
import MaterialTable from "../src/components/MaterialTable.vue";
import SourceDetails from "../src/components/SourceDetails.vue";
import SmartQueries from "../src/components/SmartQueries.vue";
const {
  catalog,
  productCode,
  currentProduct,
  currentScope,
  analysisMode,
  materials,
  chooseProduct,
  result,
  pending,
  notice,
  send,
  reset,
  restoreSession,
  sessionId,
  selectedId,
} = useAgent();
const sources = ref<InstanceType<typeof SourceDetails>>();
const showCatalog = ref(false),
  showHelp = ref(false);
const questions = computed(() =>
  !currentProduct.value
    ? [{ label: "查看产品目录", prompt: "查询已发布BOM的产品。" }]
    : [
        { label: "查看BOM", prompt: `查询${productCode.value}的BOM` },
        { label: "查看库存", prompt: `查询${productCode.value}的库存` },
        ...[1, 50, 100].map((n) => ({
          label: `${n}${currentProduct.value?.unit ?? ""}分析`,
          prompt: `生产${n}${currentProduct.value?.unit ?? ""}${productCode.value}可以吗？`,
        })),
        { label: "限制物料", prompt: "哪些物料限制生产？" },
      ],
);
</script>
<template>
  <div class="review-shell">
    <header class="card review-header">
      <div>
        <strong>智造 PMC Agent</strong>
        <p>制造业 PMC 智能计划助手</p>
      </div>
      <div>
        <label for="review-product">选择产品</label>
        <select
          id="review-product"
          :value="productCode ?? ''"
          @change="chooseProduct(($event.target as HTMLSelectElement).value)"
        >
          <option value="" disabled>请选择产品</option>
          <option
            v-for="p in catalog"
            :key="p.product_code"
            :value="p.product_code"
          >
            {{ p.product_code
            }}{{
              p.synthetic
                ? "（演示产品）"
                : p.product_name
                  ? " · " + p.product_name
                  : ""
            }}
          </option>
        </select>
        <template v-if="currentProduct && currentProduct.modes.length > 1"
          ><label for="review-scope">分析范围</label>
          <select
            id="review-scope"
            :value="analysisMode"
            :disabled="pending"
            @change="
              chooseProduct(
                productCode!,
                ($event.target as HTMLSelectElement).value,
              )
            "
          >
            <option
              v-for="s in currentProduct.modes"
              :key="s.mode"
              :value="s.mode"
            >
              {{ s.label }} · {{ s.line_count }}项
            </option>
          </select>
        </template>
        <button class="secondary-button" :disabled="pending" @click="reset()">
          新建分析
        </button>
        <button
          class="secondary-button"
          :disabled="pending"
          @click="restoreSession"
        >
          恢复会话
        </button>
        <button class="secondary-button" @click="showHelp = !showHelp">
          使用说明
        </button>
      </div>
    </header>
    <section v-if="showHelp" class="card review-help">
      <h2>快速开始</h2>
      <p>
        先选择产品，或直接输入产品编码。产品与物料目录来自当前账号可访问的数据。
      </p>
      <p>
        可以查看BOM、现有库存、生产数量需求与已核实不足；请输入您决定的正整数数量。无库存记录保持未知，不代表库存为零。
      </p>
      <p>
        结果按当前范围核对库存记录，不代表正式工单批准。未知数量不估算。演示产品可体验齐套及采购流程。
      </p>
      <p>
        “为什么不能生产？”“哪些物料限制生产？”“这些数量来自哪里？”可继续追问。点击历史回答查看对应结果；刷新或恢复只读取当前账号的本次对话历史。
      </p>
      <p>
        切换产品会进入独立会话，旧数量和证据不会继承。请勿输入无关企业秘密；如需反馈，请向发放账号的项目负责人提供问题和时间，不上传企业原件。
      </p>
    </section>
    <p role="note" class="mode-banner" v-if="currentProduct">
      {{ currentProduct.product_code }}
      {{ currentProduct.product_name ?? "" }} / {{ currentProduct.revision }} ·
      {{ currentScope?.line_count }}项BOM · {{ currentScope?.label }} ·
      {{
        analysisMode === "slim" || analysisMode === "current"
          ? "结果仅适用于当前分析范围，不代表完整产品生产放行"
          : currentProduct.synthetic
            ? "演示产品，仅用于体验分析流程"
            : "来源可追溯，只读查询"
      }}
      · 单位{{ currentProduct.unit ?? "未知" }}
      <span v-if="currentScope?.dataset_version">
        · 数据版本{{ currentScope.dataset_version }}</span
      >
    </p>
    <section v-if="currentProduct" class="card review-help">
      <p v-if="analysisMode === 'slim' || analysisMode === 'current'">
        物料组成 · 库存记录 · 需求核对 · 最大满足数量 · 来源与连续追问
      </p>
      <template v-else
        ><p>{{ currentProduct.capabilities.join(" · ") }}</p>
        <p>{{ currentProduct.limitations.join("；") }}</p></template
      >
      <button class="secondary-button" @click="showCatalog = !showCatalog">
        产品物料目录
      </button>
      <div v-if="showCatalog" class="catalog-scroll">
        <table>
          <thead>
            <tr>
              <th>物料编码</th>
              <th>名称</th>
              <th>BOM单耗</th>
              <th>库存数量</th>
              <th>单位</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(m, i) in materials" :key="i">
              <td>{{ m.material_code }}</td>
              <td>{{ m.name ?? "未提供" }}</td>
              <td>{{ m.qty_per }}</td>
              <td>{{ m.stock_qty ?? "未知" }}</td>
              <td>{{ m.unit ?? "未知" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <main class="review-workspace">
      <section class="card chat-card">
        <div class="card-heading">
          <h2>自然语言生产分析</h2>
          <span>需求 · 库存 · 结果</span>
        </div>
        <SmartQueries
          :questions="questions"
          :pending="pending"
          :context-key="`${productCode}:${analysisMode}:${sessionId ?? ''}:${selectedId ?? ''}`"
          @choose="send"
        />
        <p v-if="notice" class="mode-banner" aria-live="polite">{{ notice }}</p>
        <MessageList /><QueryComposer
          :active-product="productCode"
          :products="catalog"
          @switch="chooseProduct"
        />
      </section>
      <div class="review-analysis">
        <ResultPanel
          :active-product="productCode"
          @continue="chooseProduct"
          @source="sources?.reveal()"
        />
        <MaterialTable
          :result="result ?? undefined"
          :pending="pending"
          @source="(ids) => sources?.reveal(ids)"
        />
        <SourceDetails
          ref="sources"
          :result="result ?? undefined"
          mode="live"
        />
      </div>
    </main>
  </div>
</template>
<style scoped>
.review-shell {
  max-width: 1500px;
  margin: auto;
  padding: 24px;
}
.review-header {
  padding: 20px;
  display: flex;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.review-header strong {
  font-size: 24px;
}
.review-header select {
  margin: 8px;
  max-width: 100%;
}
.review-help {
  padding: 16px;
  margin: 12px 0;
}
.review-workspace {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}
.review-analysis {
  min-width: 0;
  display: grid;
  gap: 20px;
  align-content: start;
}
.chat-card {
  min-width: 0;
  padding: 20px;
}
.message-list {
  min-height: 250px;
  max-height: 65vh;
  overflow: auto;
}
.catalog-scroll {
  max-height: 360px;
  overflow: auto;
}
td,
th {
  padding: 8px;
  text-align: left;
}
@media (max-width: 900px) {
  .review-workspace {
    grid-template-columns: minmax(0, 1fr);
  }
  .review-shell {
    padding: 12px;
  }
}
</style>
