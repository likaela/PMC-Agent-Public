<script setup lang="ts">
import type { AgentExecutionEvent } from "../services/backendTypes";
import { formatElapsed } from "../services/analysisTrace";
defineProps<{ events?: AgentExecutionEvent[] }>();
const labels: Record<string, string> = { user_input: "用户输入", llm_understanding: "DeepSeek 理解", understanding: "DeepSeek 理解", local_validation: "规则核验", rules: "规则核验", catalog: "产品目录", tool_selection: "工具选择", service: "业务服务", tool: "业务工具", tools: "业务工具", calculation: "业务计算", response: "结果生成", answer: "结果生成", chart_ai: "图表 AI 解读" };
const states: Record<string, string> = { ok: "完成", completed: "完成", failed: "失败", error: "失败", unavailable: "未完成", skipped: "未执行", running: "处理中", checked: "已检查", reused: "沿用已存结果", not_applicable: "本轮无需", needs_input: "待补充", invalid_query: "输入无效", query_failed: "查询失败", data_unavailable: "数据待补齐", model_unavailable: "理解未完成", item_not_found: "未找到物料" };
</script>
<template>
  <details v-if="events?.length" class="agent-execution">
    <summary>Agent 执行过程<span>{{ events.length }} 个阶段</span></summary>
    <ol><li v-for="(event, i) in events" :key="i" :data-status="event.status">
      <div class="execution-stage"><strong>{{ labels[event.stage] ?? event.stage }}</strong><span>{{ states[event.status] ?? event.status }}</span><time v-if="event.durationMs != null" :title="`${event.durationMs} ms`">{{ formatElapsed(event.durationMs) }}</time></div>
      <p>{{ event.summary }}</p>
      <dl v-if="event.details.length"><template v-for="(detail, j) in event.details" :key="j"><dt>{{ detail.label }}</dt><dd>{{ detail.value }}</dd></template></dl>
    </li></ol>
  </details>
</template>
<style scoped>
.agent-execution { margin: 10px 0 16px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 9px; color: var(--text); font-size: 12px; min-width: 0; }
.agent-execution summary { cursor: pointer; font-weight: 600; }
.agent-execution summary > span { margin-left: 10px; font-size: 11px; color: var(--muted); font-weight: 400; }
.agent-execution ol { list-style: none; margin: 12px 0 0; padding: 0; display: grid; gap: 12px; }
.agent-execution li { border-left: 2px solid var(--border); padding-left: 10px; min-width: 0; }
.agent-execution li[data-status="failed"], .agent-execution li[data-status="error"] { border-color: var(--error); }
.execution-stage { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; }
.execution-stage span, .execution-stage time { font-size: 11px; color: var(--muted); }
.agent-execution p { margin: 5px 0; line-height: 1.7; overflow-wrap: anywhere; }
.agent-execution dl { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 3px 10px; margin: 5px 0 0; font-size: 11px; }
.agent-execution dt { color: var(--muted); }.agent-execution dd { margin: 0; overflow-wrap: anywhere; white-space: pre-wrap; }
</style>
