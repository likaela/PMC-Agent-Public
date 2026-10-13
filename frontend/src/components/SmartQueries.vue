<script setup lang="ts">
import { ref, watch, useId } from "vue";
import Icon from "./Icon.vue";
const props = defineProps<{
  questions: { label: string; prompt: string }[];
  contextKey?: string;
  pending?: boolean;
}>();
const emit = defineEmits<{ choose: [prompt: string] }>();
const open = ref(false);
const id = useId();
watch(() => props.contextKey, () => { open.value = false; });
function choose(prompt: string) {
  open.value = false;
  emit("choose", prompt);
}
</script>
<template>
  <section class="smart-queries" aria-label="智能查询">
    <div class="smart-queries-heading">
      <div><strong>智能查询</strong><small v-if="!open">点击展开常用问题</small></div>
      <button type="button" class="text-button" :aria-expanded="open" :aria-controls="id" @click="open = !open">
        {{ open ? "收起" : "展开推荐问题" }}<Icon name="down" :size="14" :class="{ rotated: open }"/>
      </button>
    </div>
    <div v-if="open" :id="id" class="quick-questions">
      <button v-for="q in questions" :key="q.prompt" type="button" :disabled="pending" @click="choose(q.prompt)">{{ q.label }}</button>
    </div>
  </section>
</template>
<style scoped>
.smart-queries { border-bottom:1px solid var(--border, #e7ebef); padding:10px 0; flex-shrink:0; }
.smart-queries-heading { display:flex; align-items:center; justify-content:space-between; gap:8px; }
.smart-queries-heading strong { font-size:13px; }
.smart-queries-heading small { display:block; font-size:12px; color:var(--text-muted, #687888); margin-top:3px; }
.smart-queries-heading button { flex-shrink:0; font-size:12px; }
.quick-questions { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; padding:0; }
.quick-questions button { max-width:100%; white-space:normal; }
@media(max-width:450px) { .smart-queries-heading { align-items:flex-start; } }
</style>
