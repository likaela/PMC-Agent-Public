<script setup lang="ts">
import type { ChartAiView } from "../services/backendTypes";
import Icon from "./Icon.vue";
defineProps<{ analysis?: ChartAiView }>();
</script>
<template>
  <section v-if="analysis && analysis.status !== 'not_applicable'" class="chart-ai" aria-label="AI 分析解读" :aria-busy="analysis.status === 'loading'">
    <h3><Icon name="sparkles" :size="16" />AI 分析解读</h3>
    <p v-if="analysis.status === 'loading'" role="status">正在生成分析解读…</p>
    <p v-else-if="analysis.status !== 'ok' || !analysis.sections" role="status">分析解读暂未完成，请重试本次查询。</p>
    <div v-else class="chart-ai-sections">
      <div><h4>核心结论</h4><p>{{ analysis.sections.conclusion }}</p></div>
      <div v-if="analysis.sections.findings.length"><h4>关键物料发现</h4><ul><li v-for="(finding, i) in analysis.sections.findings" :key="i">{{ finding }}</li></ul></div>
      <div><h4>生产计划影响</h4><p>{{ analysis.sections.impact }}</p></div>
      <div v-if="analysis.sections.actions.length"><h4>后续核实建议</h4><ul><li v-for="(action, i) in analysis.sections.actions" :key="i">{{ action }}</li></ul></div>
    </div>
  </section>
</template>
<style scoped>
.chart-ai { min-width: 0; margin: 12px 0 18px; padding: 14px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface-raised); color: var(--text); }
.chart-ai h3 { display: flex; align-items: center; gap: 7px; margin: 0 0 12px; font-size: 13px; }
.chart-ai h4 { margin: 0 0 5px; font-size: 12px; color: var(--muted-strong); }
.chart-ai p, .chart-ai li { margin: 0; font-size: 12px; line-height: 1.8; overflow-wrap: anywhere; }
.chart-ai ul { margin: 0; padding-left: 18px; }
.chart-ai-sections { display: grid; gap: 12px; }
</style>
