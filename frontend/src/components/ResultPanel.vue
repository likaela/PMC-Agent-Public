<script setup lang="ts">
import { computed, ref, watch, onMounted, onBeforeUnmount } from "vue";
import Icon from "./Icon.vue";
import StatusBadge from "./StatusBadge.vue";
import WarningList from "./WarningList.vue";
import AnalysisState from "./AnalysisState.vue";
import ThoughtLine from "./ThoughtLine.vue";
import InventoryGapChart from "./InventoryGapChart.vue";
import ShortageBarChart from "./ShortageBarChart.vue";
import InventoryTrendChart from "./InventoryTrendChart.vue";
import { buildGapGroups, buildShortageGroups, buildInventoryTrends } from "../services/analysisChart";
import { useAgent } from "../composables/useAgent";
import { statusText } from "../data/model";
import { isTraceComplete, settledSteps } from "../services/analysisTrace";
defineEmits<{ source: []; details: [] }>();
const { result, pending, selected, retry, canSend, session, messages, executeInventory } = useAgent();
const confirmed = ref(false), credential = ref(""), now = ref(Date.now());
let expiryTimer: ReturnType<typeof setInterval> | undefined;
onMounted(() => { expiryTimer = setInterval(() => { now.value = Date.now(); }, 1000); });
onBeforeUnmount(() => { clearInterval(expiryTimer); credential.value = ""; });
watch(() => [selected.value?.id, session.value?.session_id], () => {
  confirmed.value = false;
  credential.value = "";
});
const inventory = computed(() => result.value?.backend?.inventoryOperation);
const expiration = computed(() => inventory.value?.expiresAt ? Date.parse(inventory.value.expiresAt) : NaN);
const expired = computed(() => !Number.isFinite(expiration.value) || expiration.value <= now.value);
const executable = computed(() => inventory.value?.kind === "preview" &&
  inventory.value.canExecute && inventory.value.executionAvailable && !expired.value &&
  !!inventory.value.previewId && canSend.value &&
  selected.value?.id === messages.value.at(-1)?.id &&
  inventory.value.boundSessionId === session.value?.session_id &&
  result.value?.backend?.sessionId === session.value?.session_id &&
  result.value?.product === session.value?.product_code);
function confirmExecution() {
  const allowed = confirmed.value && executable.value && !!credential.value;
  const oneTimeCredential = credential.value;
  credential.value = "";
  confirmed.value = false;
  if (allowed) void executeInventory(oneTimeCredential, true);
}
// Backend-reported tool trace for the selected answer, including restored history.
const traceSteps = computed(() => settledSteps(result.value?.backend?.steps));
const traceComplete = computed(() =>
  isTraceComplete(result.value?.backend?.steps),
);
// Pure presentation of the selected result, never a new query or calculation.
const chartResult = computed(() =>
  !pending.value && result.value?.backend?.status === "ok"
    ? result.value.backend : undefined,
);
const chartGroups = computed(() =>
  buildGapGroups(chartResult.value?.rows, chartResult.value?.resultType, chartResult.value?.raw),
);
const shortageGroups = computed(() =>
  buildShortageGroups(chartResult.value?.rows, chartResult.value?.resultType, chartResult.value?.raw),
);
const trendSeries = computed(() =>
  ["inventory", "production", "shortage", "maximum"].includes(chartResult.value?.resultType ?? "")
    ? buildInventoryTrends(chartResult.value?.raw) : [],
);
</script>
<template>
  <section
    id="analysis-result"
    class="card result-panel"
    aria-labelledby="result-title"
    aria-live="polite"
    :aria-busy="pending"
  >
    <div class="card-heading">
      <h2 id="result-title"><span class="report-step">01</span>分析结论</h2>
      <span class="subtle-label">{{
        pending ? "等待响应" : result ? "与所选回答同步" : "本次查询"
      }}</span>
    </div>
    <Transition name="report-swap" mode="out-in">
      <AnalysisState
        v-if="pending || !result"
        :key="pending ? 'pending' : selected?.state"
      />
      <div v-else :key="selected?.id" class="result-body">
        <ThoughtLine
          v-if="traceSteps.length"
          :working="false"
          :done-label="traceComplete ? '分析完成' : '分析结束'"
          :settled-ms="selected?.elapsedMs"
          :steps="traceSteps"
          :default-open="selected?.elapsedMs != null"
          collapsible
        />
        <div class="result-kicker">
          <StatusBadge
            :tone="
              result.status === 'ready'
                ? 'green'
                : ['partial', 'zero', 'conflict', 'uncalculable'].includes(
                      result.status,
                    )
                  ? 'orange'
                  : 'blue'
            "
            >{{
              result.backend
                ? result.backend.status === "ok"
                  ? "查询完成"
                  : result.backend.status === "needs_input"
                    ? "请补充条件"
                    : result.backend.status === "invalid_query"
                      ? "输入无效"
                      : "业务数据未完整返回"
                : statusText[result.status]
            }}</StatusBadge
          ><span>{{ result.sources[0]?.kind }}</span>
        </div>
        <h3>{{ result.title }}</h3>
        <p class="result-explanation">{{ result.answer }}</p>
        <details v-if="chartGroups.length || shortageGroups.length || trendSeries.length"
          class="result-charts" open>
          <summary>数据图表 <span>与本次物料明细一致</span></summary>
          <InventoryGapChart v-if="chartGroups.length" :groups="chartGroups" />
          <ShortageBarChart v-if="shortageGroups.length" :groups="shortageGroups" />
          <InventoryTrendChart v-if="trendSeries.length" :series="trendSeries" />
        </details>
        <section v-if="inventory" class="inventory-confirmation" aria-label="库存预览与执行回执">
          <template v-if="inventory.kind === 'preview'">
            <p>仅生成领料预览，尚未扣减库存。请核对物料需求与批次分配。</p>
            <p v-if="!inventory.executionAvailable">当前环境未开放库存执行。</p>
            <p v-else-if="expired">预览已失效或有效期无法核实，请重新生成预览。</p>
            <p v-else-if="!executable && inventory.canExecute">请在原会话的最新有效预览中操作。</p>
          </template>
          <p v-else-if="inventory.executed">库存扣减已确认，以下为服务端保存的执行回执。</p>
          <p v-else>当前没有已确认成功的库存扣减回执。</p>
          <ul v-if="inventory.blockers.length">
            <li v-for="blocker in inventory.blockers" :key="blocker">{{ blocker }}</li>
          </ul>
          <div v-if="inventory.allocations.length" class="table-scroll" tabindex="0" aria-label="批次分配与库存回执明细">
            <table>
              <thead><tr><th>物料</th><th>批次</th><th>批次日期</th><th>扣减数量</th><th>扣减前</th><th>扣减后</th></tr></thead>
              <tbody><tr v-for="(allocation, index) in inventory.allocations" :key="index">
                <td>{{ allocation.materialCode ?? '—' }}</td><td>{{ allocation.batch ?? '—' }}</td>
                <td>{{ allocation.receivedAt ?? '—' }}</td><td>{{ allocation.quantity ?? '—' }}</td>
                <td>{{ allocation.before ?? '—' }}</td><td>{{ allocation.after ?? '—' }}</td>
              </tr></tbody>
            </table>
          </div>
          <form v-if="executable" class="inventory-confirmation-form" @submit.prevent="confirmExecution">
            <label><input v-model="confirmed" type="checkbox" />我已核对本次预览，明确确认库存扣减</label>
            <label>库存执行凭据<input v-model="credential" type="password" autocomplete="off" aria-label="库存执行凭据" placeholder="仅用于本次请求" /></label>
            <button type="submit" class="primary-button" :disabled="!confirmed || !credential || pending">确认执行库存扣减</button>
          </form>
        </section>
        <WarningList :warnings="result.warnings" />
        <button
          v-if="
            result.backend &&
            !['ok', 'needs_input', 'invalid_query'].includes(
              result.backend.status,
            )
          "
          class="secondary-button"
          :disabled="!canSend"
          @click="retry()"
        >
          重试原问题
        </button>
        <div class="result-links">
          <button
            v-if="result.backend?.rows.length"
            class="result-detail-button"
            @click="$emit('details')"
          >
            <Icon name="layers" :size="16" />核对物料明细<Icon
              name="down"
              :size="15"
            /></button
          ><button
            v-if="result.sources.length || result.backend"
            class="source-inline"
            @click="$emit('source')"
          >
            <Icon name="link" :size="15" /><span
              >查看本次分析来源与适用条件</span
            ><Icon name="arrow" :size="15" />
          </button>
        </div>
      </div>
    </Transition>
  </section>
</template>
<style src="../chart.css"></style>
<style scoped>
.inventory-confirmation { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--border, #e7ebef); }
.inventory-confirmation-form { display: grid; gap: 12px; margin-top: 14px; }
.inventory-confirmation-form label { display: block; font-size: 13px; }
.inventory-confirmation-form input[type="password"] { display: block; width: 100%; margin-top: 6px; padding: 8px; }
</style>
