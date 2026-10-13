<script setup lang="ts">
import { ref } from "vue";
import { useAgent } from "./useTrialAgent";
import MessageList from "../src/components/MessageList.vue";
import QueryComposer from "../src/components/QueryComposer.vue";
import ResultPanel from "../src/components/ResultPanel.vue";
import MaterialTable from "../src/components/MaterialTable.vue";
import SourceDetails from "../src/components/SourceDetails.vue";
import SmartQueries from "../src/components/SmartQueries.vue";
const {
  result,
  pending,
  notice,
  send,
  reset,
  restoreSession,
  sessionId,
  selectedId,
  activeProduct,
} = useAgent();
const sources = ref<InstanceType<typeof SourceDetails>>();
const questions = [
  ...[50, 100].map((n) => ({
    label: `${n}台分析`,
    prompt: `生产${n}台DEMO-PMC-01`,
  })),
  { label: "最大生产数量", prompt: "最多能生产多少台？" },
  { label: "采购建议", prompt: "帮我生成采购建议" },
];
</script>
<template>
  <div class="trial-shell">
    <header class="card trial-header">
      <div>
        <strong>智造 PMC Agent</strong>
        <p>制造业 PMC 智能计划助手 · 演示产品体验</p>
      </div>
      <div>
        <label for="data-space">数据空间</label>
        <select id="data-space" disabled>
          <option value="demo_scenario_50_vs_100">
            DEMO-PMC-01（演示产品）
          </option>
        </select>
        <button class="secondary-button" :disabled="pending" @click="reset">
          新建分析
        </button>
        <button
          class="secondary-button"
          :disabled="pending"
          @click="restoreSession"
        >
          恢复会话
        </button>
      </div>
    </header>
    <p role="note" class="mode-banner">
      仅支持
      DEMO-PMC-01（演示产品），数据用于体验分析流程，不用于业务放行。请勿输入企业秘密或个人敏感信息。
    </p>
    <main class="trial-workspace">
      <section class="card chat-card">
        <div class="card-heading">
          <h2>自然语言生产分析</h2>
          <span>后端确定性计算</span>
        </div>
        <SmartQueries
          :questions="questions"
          :pending="pending"
          :context-key="`${sessionId ?? ''}:${selectedId ?? ''}`"
          @choose="send"
        />
        <p v-if="notice" class="mode-banner" aria-live="polite">{{ notice }}</p>
        <MessageList /><QueryComposer :active-product="activeProduct" />
      </section>
      <div class="trial-analysis">
        <ResultPanel
          :active-product="activeProduct"
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
.trial-shell {
  max-width: 1500px;
  margin: auto;
  padding: 24px;
}
.trial-header {
  padding: 20px;
  display: flex;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.trial-header strong {
  font-size: 24px;
}
.trial-header p {
  color: #64748b;
}
.trial-header select {
  margin: 8px;
  max-width: 100%;
}
.trial-workspace {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 20px;
}
.trial-analysis {
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
  overflow-y: auto;
}
@media (max-width: 900px) {
  .trial-workspace {
    grid-template-columns: minmax(0, 1fr);
  }
  .trial-shell {
    padding: 12px;
  }
}
</style>
