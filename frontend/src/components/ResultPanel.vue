<script setup lang="ts">
import { computed } from "vue";
import Icon from "./Icon.vue";
import StatusBadge from "./StatusBadge.vue";
import { useAgent } from "../composables/useAgent";
import { formatNumber, statusText } from "../data/model";
import { sourceLabel } from "../services/productLanguage";
defineProps<{ activeProduct?: string | null }>();
defineEmits<{ source: []; continue: [code: string] }>();
const { result, selected, messages, pending, retry } = useAgent();
const metrics = computed(() => result.value?.metrics?.filter(m => m.value != null && m.value.trim() !== "") ?? []);
const purchaseFields = computed(() => {
  const rows = result.value?.purchases ?? [];
  return {shortage: rows.some(r => r.shortage != null), suggested: rows.some(r => r.suggested != null),
    cost: rows.some(r => r.cost != null), supplier: rows.some(r => r.supplier != null || r.leadTime != null),
    risk: rows.some(r => r.risk != null || r.reasons.length > 0)};
});
const errorTitles = {
  network: "请求失败，请重试",
  mapping: "当前结果暂无法完整展示",
  unconfigured: "接口尚未配置",
  unsupported: "请选择已提供的演示场景",
  timeout: "等待结果超时",
  session: "请恢复或新建会话",
  invalid: "请检查查询条件",
  server: "分析服务暂不可用",
};
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
      <h2 id="result-title"><Icon name="file" :size="19" />分析结论</h2>
      <span class="subtle-label">{{
        pending
          ? "等待响应"
          : selected &&
              (selected.id !== messages.at(-1)?.id ||
                (activeProduct &&
                  result?.product &&
                  activeProduct !== result.product))
            ? "历史回答对应结果（非本次请求）"
            : result
              ? "本次回答对应结果"
              : "本次查询"
      }}</span>
    </div>
    <div v-if="pending" class="result-loading">
      <div class="loading-symbol"><Icon name="sparkles" :size="26" /></div>
      <h3>正在等待分析结果</h3>
      <p>本次请求完成后，将在这里展示结论与依据。</p>
      <div class="skeleton"></div>
      <div class="skeleton short"></div>
      <div class="skeleton"></div>
    </div>
    <div v-else-if="selected?.error" class="result-empty error-state">
      <span class="empty-icon warning"
        ><Icon
          :name="selected.error.code === 'unconfigured' ? 'link' : 'alert'"
          :size="29"
      /></span>
      <h3>{{ errorTitles[selected.error.code] }}</h3>
      <p>{{ selected.error.message }}</p>
      <button
        v-if="selected.error.code !== 'unsupported'"
        class="secondary-button"
        @click="retry()"
      >
        <Icon name="retry" :size="16" />重试原问题
      </button>
    </div>
    <div v-else-if="!result" class="result-empty">
      <span class="empty-icon"><Icon name="layers" :size="32" /></span>
      <h3>
        {{
          selected?.result?.conversation
            ? "本轮为交流回复"
            : selected?.state === "cancelled"
              ? "本次分析已取消"
              : "让每个问题都有清晰的落点"
        }}
      </h3>
      <p>
        {{
          selected?.result?.conversation
            ? "本轮没有执行业务分析。可选择左侧历史回答查看对应结果，或继续输入产品编码发起查询。"
            : selected?.state === "cancelled"
              ? "已停止等待，在线请求请先恢复会话确认服务端结果。"
              : "发送问题后，查看分析结论、物料缺口和可追溯的数据来源。"
        }}
      </p>
      <button
        v-if="selected?.state === 'cancelled'"
        class="secondary-button"
        @click="retry()"
      >
        <Icon name="retry" :size="16" />重试原问题
      </button>
      <div v-else class="empty-preview">
        <span><Icon name="checkcircle" :size="16" />结论摘要</span
        ><span><Icon name="layers" :size="16" />物料明细</span
        ><span><Icon name="link" :size="16" />数据来源</span>
      </div>
    </div>
    <div v-else class="result-body">
      <p
        v-if="
          result.product && activeProduct && result.product !== activeProduct
        "
        class="result-explanation"
        role="note"
      >
        此回答属于{{ result.product }}；当前输入产品为{{ activeProduct }}。
        <button class="text-button" @click="$emit('continue', result.product)">
          继续分析此产品
        </button>
      </p>
      <div class="result-kicker">
        <StatusBadge
          :tone="
            result.status === 'ready'
              ? 'green'
              : [
                    'partial',
                    'blocked',
                    'zero',
                    'conflict',
                    'uncalculable',
                    'no-facts',
                  ].includes(result.status)
                ? 'orange'
                : 'blue'
          "
          >{{ statusText[result.status] }}</StatusBadge
        ><span>{{ sourceLabel(result.sources[0]?.kind) }}</span>
      </div>
      <h3>{{ result.title }}</h3>
      <p v-if="result.slimReview" class="result-explanation">
        本次结果仅适用于当前分析范围，不代表完整产品生产放行。
      </p>
      <p class="result-explanation">{{ result.answer }}</p>
      <div v-if="metrics.length" class="purchase-grid">
        <div v-for="metric in metrics" :key="metric.label">
          <span>{{ metric.label }}</span
          ><strong>{{ metric.value }}</strong>
        </div>
      </div>
      <div
        v-if="result.purchases?.length"
        class="table-scroll"
        aria-label="采购建议表"
      >
        <table>
          <thead>
            <tr>
              <th>物料</th>
              <th v-if="purchaseFields.shortage">缺口</th>
              <th v-if="purchaseFields.suggested">建议采购</th>
              <th v-if="purchaseFields.cost">成本 / 币种</th>
              <th v-if="purchaseFields.supplier">供应商 / 交期</th>
              <th v-if="purchaseFields.risk">风险与依据</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(purchase, index) in result.purchases" :key="index">
              <td>{{ purchase.material }}</td>
              <td v-if="purchaseFields.shortage">{{ purchase.shortage == null ? '—' : formatNumber(purchase.shortage) }} {{ purchase.shortage == null ? '' : purchase.unit }}</td>
              <td v-if="purchaseFields.suggested">
                {{ purchase.suggested == null ? '—' : formatNumber(purchase.suggested) }} {{ purchase.suggested == null ? '' : purchase.unit }}
              </td>
              <td v-if="purchaseFields.cost">
                {{ purchase.cost == null ? '—' : formatNumber(purchase.cost) }}
                {{ purchase.cost == null ? '' : purchase.currency ?? '' }}
              </td>
              <td v-if="purchaseFields.supplier">
                {{ purchase.supplier ?? '' }}
                <span v-if="purchase.leadTime != null"> {{ purchase.leadTime }} 天</span>
              </td>
              <td v-if="purchaseFields.risk">
                {{ purchase.risk ?? '' }}
                <p>{{ purchase.reasons.join("；") }}</p>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <details
        v-if="result.detailTable"
        :key="selected?.id"
        :open="!result.bookObservation"
      >
        <summary v-if="result.bookObservation" class="text-button">展开库存记录明细</summary>
        <div class="table-scroll" aria-label="事实明细表">
        <table>
          <thead>
            <tr>
              <th v-for="column in result.detailTable.columns" :key="column">
                {{ column }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, index) in result.detailTable.rows" :key="index">
              <td v-for="(cell, column) in row" :key="column">
                {{ cell ?? "—" }}
              </td>
            </tr>
            <tr v-if="!result.detailTable.rows.length">
              <td :colspan="result.detailTable.columns.length">
                暂无可展示明细。
              </td>
            </tr>
          </tbody>
        </table>
        </div>
      </details>
      <div v-if="result.purchase" class="purchase-grid">
        <div v-if="result.purchase.shortage != null">
          <span>缺料数量</span
          ><strong
            >{{ formatNumber(result.purchase.shortage)
            }}<small>{{ result.purchase.unit }}</small></strong
          >
        </div>
        <div v-if="result.purchase.safetyStock != null">
          <span>安全库存</span
          ><strong
            >{{ formatNumber(result.purchase.safetyStock)
            }}<small>{{ result.purchase.unit }}</small></strong
          >
        </div>
        <div v-if="result.purchase.suggested != null">
          <span>建议采购</span
          ><strong
            >{{ formatNumber(result.purchase.suggested)
            }}<small>{{ result.purchase.unit }}</small></strong
          >
        </div>
      </div>
      <p v-if="result.purchase" class="purchase-note">
        {{ result.purchase.note }}
      </p>
      <div v-if="result.conclusion || result.nextAction" class="next-action">
        <span class="next-action-icon"><Icon name="arrow" :size="18" /></span>
        <div>
          <strong>{{ result.conclusion }}</strong>
          <p>{{ result.nextAction }}</p>
        </div>
      </div>
      <div v-if="result.warnings.length" class="warning-list">
        <h4><Icon name="alert" :size="16" />使用前请留意</h4>
        <ul>
          <li v-for="warning in result.warnings" :key="warning">
            {{ warning }}
          </li>
        </ul>
      </div>
      <button class="source-inline" @click="$emit('source')">
        <Icon name="link" :size="15" /><span>查看本次分析来源与适用条件</span
        ><Icon name="arrow" :size="15" />
      </button>
    </div>
  </section>
</template>
