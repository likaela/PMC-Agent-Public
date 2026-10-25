<script setup lang="ts">
import AnalysisSkeleton from "./AnalysisSkeleton.vue";
import Icon from "./Icon.vue";
import { useAgent } from "../composables/useAgent";
const { selected, pending, retry, canSend } = useAgent();
const errorTitles = {
  network: "请求失败，请重试",
  mapping: "当前结果暂无法完整展示",
  unconfigured: "请补充产品信息",
  unsupported: "当前问题暂不支持",
};
</script>
<template>
  <AnalysisSkeleton v-if="pending" />
  <div v-else-if="selected?.error" class="result-empty error-state">
    <span class="empty-icon warning"
      ><Icon
        :name="selected.error.code === 'unconfigured' ? 'link' : 'alert'"
        :size="29"
    /></span>
    <h3>
      {{
        selected.error.backendCode === "PRODUCT_SWITCH_REQUIRED"
          ? "请直接提供新的产品型号"
          : errorTitles[selected.error.code]
      }}
    </h3>
    <p>{{ selected.error.message }}</p>
    <span class="diagnostic"
      >{{ selected.error.backendCode ?? selected.error.code.toUpperCase() }}
      {{ selected.error.httpStatus }}</span
    >
    <details v-if="selected.error.diagnostics" class="backend-audit">
      <summary>错误诊断</summary>
      <pre>{{ JSON.stringify(selected.error.diagnostics, null, 2) }}</pre>
    </details>
    <button
      v-if="
        selected.error.code !== 'unsupported' &&
        selected.error.backendCode !== 'PRODUCT_SWITCH_REQUIRED' &&
        canSend
      "
      class="secondary-button"
      @click="retry()"
    >
      <Icon name="retry" :size="16" />重试原问题
    </button>
  </div>
  <div v-else class="result-empty">
    <span class="empty-icon"><Icon name="layers" :size="32" /></span>
    <h3>
      {{
        selected?.state === "cancelled"
          ? "本次分析已取消"
          : "让每个问题都有清晰的落点"
      }}
    </h3>
    <p>
      {{
        selected?.state === "cancelled"
          ? "已停止等待，迟到响应将被忽略；不代表后端计算已停止。同步对话后可继续提问。"
          : "发送问题后，查看分析结论、物料缺口和可追溯的数据来源。"
      }}
    </p>
    <button
      v-if="selected?.state === 'cancelled' && canSend"
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
</template>
