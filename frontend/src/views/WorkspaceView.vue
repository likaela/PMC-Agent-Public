<script setup lang="ts">
import { computed, ref, watch, nextTick } from "vue";
import { useRoute, useRouter } from "vue-router";
import Icon from "../components/Icon.vue";
import SummaryCards from "../components/SummaryCards.vue";
import QueryComposer from "../components/QueryComposer.vue";
import MessageList from "../components/MessageList.vue";
import ResultPanel from "../components/ResultPanel.vue";
import MaterialTable from "../components/MaterialTable.vue";
import SourceDetails from "../components/SourceDetails.vue";
import SmartQueries from "../components/SmartQueries.vue";
import { demoScenarios, DEMO_SCENARIO_ID } from "../data/demoScenarios";
import { useAgent } from "../composables/useAgent";
const route = useRoute();
const router = useRouter();
const {
  mode,
  scenario,
  draft,
  pending,
  result,
  notice,
  startEnterpriseAnalysis,
  send,
  restoreSession,
  selectedProduct,
  sessionId,
  selectedId,
  activeProduct,
  productCatalog,
  chooseProduct,
} = useAgent();
const questions = computed(() =>
  scenario.value === DEMO_SCENARIO_ID
    ? demoScenarios.map((s) => ({ label: s.label, prompt: s.prompt }))
    : [
        {
          label: "查看BOM",
          prompt: selectedProduct.value
            ? `查询${selectedProduct.value}的BOM`
            : "有哪些已发布产品？",
        },
        { label: "查看库存", prompt: "看看库存。" },
        { label: "计算最大数量", prompt: "最多可以生产多少片？" },
        { label: "生产最大数量", prompt: "生产最大数量可以吗？" },
        { label: "限制物料", prompt: "哪些物料限制产量？" },
      ],
);
const sources = ref<InstanceType<typeof SourceDetails>>();
watch(
  () => route.query.scenario,
  (value) => {
    const online = demoScenarios.find((s) => s.id === value);
    if (
      online &&
      mode.value === "live" &&
      scenario.value === DEMO_SCENARIO_ID
    ) {
      draft.value = online.prompt;
      return;
    }
  },
  { immediate: true },
);
watch(scenario, () => {
  if (route.query.scenario) router.replace("/workspace");
});
function newAnalysis() {
  startEnterpriseAnalysis();
  router.replace("/workspace");
}
async function focusResult() {
  await nextTick();
  document.getElementById("analysis-result")?.scrollIntoView({
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "instant"
      : "smooth",
  });
}
</script>
<template>
  <div class="workspace-page">
    <div class="page-heading">
      <div>
        <div class="eyebrow">ANALYSIS WORKSPACE</div>
        <h1>分析工作台<span class="heading-dot"></span></h1>
        <p>
          输入产品编码与生产数量，查询BOM、核对库存记录，查看需求、结果与来源。
        </p>
      </div>
      <button
        v-if="mode === 'live'"
        class="text-button"
        :disabled="pending"
        @click="restoreSession()"
      >
        恢复会话
      </button>
      <button class="secondary-button" :disabled="pending" @click="newAnalysis">
        <Icon name="plus" :size="17" />新建分析
      </button>
    </div>
    <SummaryCards />
    <div v-if="notice" class="inline-notice" role="status">
      <Icon name="info" :size="16" />{{ notice }}
    </div>
    <div class="analysis-grid">
      <section class="card chat-panel" aria-labelledby="chat-title">
        <div class="card-heading">
          <h2 id="chat-title"><Icon name="chat" :size="19" />PMC助手</h2>
          <span class="assistant-status"><span></span>PMC-Agent</span>
        </div>
        <SmartQueries
          :questions="questions"
          :pending="pending"
          :context-key="`${scenario ?? ''}:${sessionId ?? ''}:${selectedId ?? ''}`"
          @choose="send"
        />
        <MessageList />
        <QueryComposer
          :active-product="activeProduct"
          :products="productCatalog"
          @switch="chooseProduct"
        /><button v-if="result" class="mobile-result-link" @click="focusResult">
          查看本次分析结果<Icon name="down" :size="16" />
        </button>
      </section>
      <ResultPanel
        :active-product="activeProduct"
        @continue="chooseProduct"
        @source="sources?.reveal()"
      />
    </div>
    <MaterialTable
      :result="result ?? undefined"
      :pending="pending"
      @source="(ids) => sources?.reveal(ids)"
    /><SourceDetails ref="sources" :result="result ?? undefined" :mode="mode" />
  </div>
</template>
