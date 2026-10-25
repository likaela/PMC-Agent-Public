<script setup lang="ts">
/**
 * Analysis thought line.
 *
 * Interaction reference: Vue Bits "Thought Line" (MIT + Commons Clause).
 * Re-implemented with this project's own markup, theme variables and styles;
 * no dependency was added. Steps are real request phases or backend-reported
 * operations, never fabricated reasoning.
 */
import { computed, onUnmounted, ref, watch } from "vue";
import Icon from "./Icon.vue";
import { formatElapsed, type ThoughtStep } from "../services/analysisTrace";

const props = withDefaults(
  defineProps<{
    working?: boolean;
    label?: string;
    doneLabel?: string;
    startedAt?: number;
    settledMs?: number;
    steps?: ThoughtStep[];
    collapsible?: boolean;
    /** Settled traces can start expanded so the backend steps are visible at once. */
    defaultOpen?: boolean;
  }>(),
  {
    working: false,
    label: "正在分析",
    doneLabel: "分析完成",
    startedAt: 0,
    steps: () => [],
    collapsible: false,
    defaultOpen: false,
  },
);

let lineId = 0;
const traceId = `thought-trace-${++lineId}`;
const liveMs = ref(0);
let timer: number | undefined;

function stopTimer() {
  if (timer !== undefined) {
    window.clearInterval(timer);
    timer = undefined;
  }
}
function startTimer() {
  stopTimer();
  const paint = () => {
    liveMs.value = Math.max(0, Date.now() - props.startedAt);
  };
  paint();
  timer = window.setInterval(paint, 100);
}
watch(
  () => [props.working, props.startedAt] as const,
  () => {
    if (props.working) startTimer();
    else stopTimer();
  },
  { immediate: true },
);
onUnmounted(stopTimer);

const showTime = computed(() => props.working || props.settledMs != null);
const timeText = computed(() =>
  showTime.value
    ? formatElapsed(props.working ? liveMs.value : (props.settledMs ?? 0))
    : "",
);
const interactive = computed(() => props.collapsible && props.steps.length > 0);
const open = ref(props.working || props.defaultOpen);
watch(
  () => props.working,
  (working) => {
    open.value = working
      ? props.steps.length > 0
      : props.defaultOpen && props.steps.length > 0;
  },
);
const activeStep = computed(() =>
  props.steps.find((step) => step.state === "active"),
);
const announcement = computed(() => {
  if (props.working)
    return activeStep.value
      ? `${props.label}，${activeStep.value.label}`
      : props.label;
  return timeText.value
    ? `${props.doneLabel}，用时 ${timeText.value}`
    : props.doneLabel;
});
const headLabel = computed(
  () =>
    `${props.doneLabel}${timeText.value ? `，用时 ${timeText.value}` : ""}，${props.steps.length} 个步骤，${open.value ? "收起" : "展开"}分析过程`,
);
function stepStyle(index: number) {
  return { animationDelay: `${index * 55}ms` };
}
</script>
<template>
  <div
    class="thought-line"
    :data-working="working ? '' : undefined"
    :class="{ 'is-settled': !working }"
  >
    <component
      :is="interactive ? 'button' : 'div'"
      class="thought-head"
      :class="{ 'is-interactive': interactive }"
      :type="interactive ? 'button' : undefined"
      :aria-expanded="interactive ? open : undefined"
      :aria-controls="interactive ? traceId : undefined"
      :aria-label="interactive ? headLabel : undefined"
      @click="interactive && (open = !open)"
    >
      <span class="thought-glyph" aria-hidden="true"
        ><Icon name="sparkles" :size="15"
      /></span>
      <span class="thought-label" aria-hidden="true">{{
        working ? label : doneLabel
      }}</span>
      <span v-if="timeText" class="thought-timer" aria-hidden="true">{{
        timeText
      }}</span>
      <Icon
        v-if="interactive"
        name="down"
        :size="13"
        class="thought-chevron"
        :class="{ 'is-open': open }"
        aria-hidden="true"
      />
    </component>
    <div
      v-if="steps.length"
      :id="traceId"
      class="thought-trace"
      :data-open="open ? '' : undefined"
      :aria-hidden="open ? undefined : 'true'"
    >
      <div class="thought-trace-inner">
        <ol class="thought-steps">
          <li
            v-for="(step, index) in steps"
            :key="step.key"
            class="thought-step"
            :data-state="step.state"
            :title="step.operation"
            :style="stepStyle(index)"
          >
            <span class="thought-step-icon" aria-hidden="true">
              <Icon v-if="step.state === 'done'" name="check" :size="12" /><Icon
                v-else-if="step.state === 'failed'"
                name="alert"
                :size="12"
              /><span
                v-else-if="step.state === 'active'"
                class="thought-dot"
              ></span
              ><span v-else class="thought-ring"></span>
            </span>
            <span class="thought-step-label">{{ step.label }}</span>
            <span v-if="step.detail" class="thought-step-detail">{{
              step.detail
            }}</span>
          </li>
        </ol>
      </div>
    </div>
    <span class="sr-only" role="status">{{ announcement }}</span>
  </div>
</template>
