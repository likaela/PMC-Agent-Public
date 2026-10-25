<script setup lang="ts">
import { computed } from "vue";
import ThoughtLine from "./ThoughtLine.vue";
import { useAgent } from "../composables/useAgent";
import { pendingSteps } from "../services/analysisTrace";
const { pendingStage, pendingStartedAt } = useAgent();
const steps = computed(() => pendingSteps(pendingStage.value));
</script>
<template>
  <div class="analysis-skeleton">
    <ThoughtLine
      :working="true"
      label="正在分析"
      :started-at="pendingStartedAt"
      :steps="steps"
    />
    <p>步骤来自当前实际执行的请求阶段；结论与数据会在响应返回后一起显示。</p>
    <div class="skeleton-report" aria-hidden="true">
      <div class="skeleton skeleton-tag"></div>
      <div class="skeleton skeleton-title"></div>
      <div class="skeleton"></div>
      <div class="skeleton medium"></div>
      <div class="skeleton-box">
        <div class="skeleton"></div>
        <div class="skeleton medium"></div>
      </div>
    </div>
  </div>
</template>
