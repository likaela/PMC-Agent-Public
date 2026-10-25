<script setup lang="ts">
import { computed, ref, nextTick } from "vue";
import { useRouter } from "vue-router";
import LiveSessionControls from "../components/LiveSessionControls.vue";
import BackendDetails from "../components/BackendDetails.vue";
import WelcomePrompt from "../components/WelcomePrompt.vue";
import Icon from "../components/Icon.vue";
import QueryComposer from "../components/QueryComposer.vue";
import MessageList from "../components/MessageList.vue";
import ResultPanel from "../components/ResultPanel.vue";
import SourceDetails from "../components/SourceDetails.vue";
import { useAgent } from "../composables/useAgent";
const router = useRouter();
const { pending, result, messages, notice, reset } = useAgent();
const hasQuery = computed(() => messages.value.length > 0);
const sources = ref<InstanceType<typeof SourceDetails>>();
function showMaterials() {
  document
    .getElementById("business-details")
    ?.scrollIntoView({ block: "start", behavior: "instant" });
  document
    .querySelector<HTMLElement>(".table-scroll")
    ?.focus({ preventScroll: true });
}
function newAnalysis() {
  reset();
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
  <div class="workspace-page" :class="{ 'is-welcome': !hasQuery }">
    <div v-if="notice && !hasQuery" class="inline-notice" role="status">
      {{ notice }}
    </div>
    <LiveSessionControls />
    <WelcomePrompt v-if="!hasQuery" key="welcome" />
    <div v-else key="analysis" class="analysis-session">
      <div class="page-heading">
        <div>
          <div class="eyebrow">ANALYSIS WORKSPACE</div>
          <h1>分析工作台<span class="heading-dot"></span></h1>
          <p>提出一个问题，让物料、结论与依据清晰呈现。</p>
        </div>
        <button
          class="secondary-button"
          :disabled="pending || !messages.length"
          @click="newAnalysis"
        >
          <Icon name="plus" :size="17" />新建对话
        </button>
      </div>
      <div v-if="notice" class="inline-notice" role="status">
        <Icon name="info" :size="16" />{{ notice }}
      </div>
      <div class="analysis-grid">
        <section class="card chat-panel" aria-labelledby="chat-title">
          <div class="card-heading">
            <h2 id="chat-title"><Icon name="chat" :size="19" />智能查询</h2>
            <span class="assistant-status"><span></span>PMC-Agent</span>
          </div>
          <MessageList />
          <QueryComposer /><button
            v-if="result"
            class="mobile-result-link"
            @click="focusResult"
          >
            查看本次分析结果<Icon name="down" :size="16" />
          </button>
        </section>
        <ResultPanel @source="sources?.reveal()" @details="showMaterials" />
      </div>
      <BackendDetails
        v-if="
          result?.backend &&
          (result.backend.rows.length || result.backend.metrics.length)
        "
        :result="result"
      />
      <SourceDetails
        v-if="result && (result.sources.length || result.backend)"
        ref="sources"
        :result="result"
      />
    </div>
  </div>
</template>
