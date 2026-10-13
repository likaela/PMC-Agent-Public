<script setup lang="ts">
import { computed } from "vue";
import Icon from "./Icon.vue";
import StatusBadge from "./StatusBadge.vue";
import { useAgent } from "../composables/useAgent";
import { formatNumber, statusText } from "../data/model";
import { sourceLabel } from "../services/productLanguage";
const { result, pending, selected, scenario } = useAgent();
const stateLabel = computed(() =>
  pending.value
    ? "正在分析"
    : result.value
      ? statusText[result.value.status]
      : selected.value?.state === "error"
        ? "请求未完成"
        : selected.value?.state === "cancelled"
          ? "已取消"
          : "等待提问",
);
</script>
<template>
  <section class="summary-grid" aria-label="任务摘要">
    <article v-if="result?.product && !pending" class="summary-card card">
      <div class="summary-label">查询对象<Icon name="box" :size="18" /></div>
      <strong :class="{ 'summary-empty': !result?.product }">{{
        result.product
      }}</strong
      ><small>本次查询的产品或物料编码</small>
    </article>
    <article
      v-if="result?.quantity != null && !pending"
      class="summary-card card"
    >
      <div class="summary-label">目标数量<Icon name="layers" :size="18" /></div>
      <strong :class="{ 'summary-empty': result?.quantity == null }"
        >{{ formatNumber(result?.quantity)
        }}<span v-if="result?.quantity != null">{{
          result.quantityUnit
        }}</span></strong
      ><small>用户指定的生产目标</small>
    </article>
    <article class="summary-card card">
      <div class="summary-label">
        分析状态<Icon name="sparkles" :size="18" />
      </div>
      <div class="summary-status">
        <StatusBadge
          :tone="
            result?.status === 'partial' || selected?.state === 'error'
              ? 'orange'
              : result?.status === 'ready'
                ? 'green'
                : 'blue'
          "
          >{{ stateLabel }}</StatusBadge
        >
      </div>
      <small>{{
        selected ? `请求时间 ${selected.requestedAt}` : "等待发起本次查询"
      }}</small>
    </article>
    <article v-if="result?.sources.length && !pending" class="summary-card card">
      <div class="summary-label">
        数据来源<Icon name="database" :size="18" />
      </div>
      <strong class="summary-mode">{{
        (result ? result.backend?.scenario_id : scenario) ===
        "kq_z112_review_slim"
          ? "标准分析范围"
          : (result ? result.backend?.scenario_id : scenario) ===
              "kq_z112_review_slim_synthetic"
            ? "数量模拟范围"
            : (result ? result.backend?.scenario_id : scenario)
              ? "演示产品"
              : "业务记录"
      }}</strong
      ><small>{{
        sourceLabel(result?.sources[0]?.kind) || "等待查询结果"
      }}</small>
    </article>
  </section>
</template>
