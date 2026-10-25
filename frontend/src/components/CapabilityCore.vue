<script setup lang="ts">
import { computed, ref, watch, onMounted, onBeforeUnmount } from "vue";
import { useAnimationFrame } from "motion-v";
import Float from "./motion/Float.vue";
import Icon from "./Icon.vue";
import { useMotionPreferences } from "../composables/useMotionPreferences";
const { reduced, finePointer, visible } = useMotionPreferences();
const props = withDefaults(defineProps<{ active?: boolean }>(), {
  active: true,
});
const figure = ref<HTMLElement>();
const inViewport = ref(true);
let visibilityObserver: IntersectionObserver | undefined;
onMounted(() => {
  visibilityObserver = new IntersectionObserver((entries) => {
    inViewport.value = entries[0]?.isIntersecting ?? false;
  });
  if (figure.value) visibilityObserver.observe(figure.value);
});
onBeforeUnmount(() => visibilityObserver?.disconnect());
const paused = ref(false);
const pointer = ref({ x: 0, y: 0 });
const time = ref(0);
const nodes = [
  { name: "BOM 物料", icon: "layers", position: "bom", state: "查询" },
  { name: "库存事实", icon: "database", position: "inventory", state: "查询" },
  { name: "缺料分析", icon: "alert", position: "shortage", state: "查询" },
  { name: "采购建议", icon: "cart", position: "purchase", state: "规划" },
  { name: "数据来源", icon: "link", position: "sources", state: "可追溯" },
];
useAnimationFrame((_elapsed, delta) => {
  if (
    !reduced.value &&
    !paused.value &&
    props.active &&
    inViewport.value &&
    visible.value
  )
    time.value += Math.min(delta, 64) / 1000;
});
const points = computed(() =>
  [-26, 26, 90].map((angle, index) => {
    const t = time.value * (index === 2 ? -0.22 : 0.24) + index * 2.2;
    const rx = index === 2 ? 203 : 248;
    const ry = index === 2 ? 90 : 94;
    const a = (angle * Math.PI) / 180;
    const x = rx * Math.cos(t),
      y = ry * Math.sin(t);
    return {
      x: 310 + x * Math.cos(a) - y * Math.sin(a),
      y: 250 + x * Math.sin(a) + y * Math.cos(a),
    };
  }),
);
function move(event: PointerEvent) {
  if (
    !finePointer.value ||
    reduced.value ||
    paused.value ||
    !props.active ||
    event.pointerType === "touch"
  )
    return;
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
  pointer.value = {
    x: (event.clientX - rect.left) / rect.width - 0.5,
    y: (event.clientY - rect.top) / rect.height - 0.5,
  };
}
function resetPointer() {
  pointer.value = { x: 0, y: 0 };
}
watch([reduced, paused, () => props.active], () => {
  resetPointer();
  if (reduced.value) time.value = 0;
});
const parallax = computed(() => ({
  transform: `translate3d(${pointer.value.x * 12}px, ${pointer.value.y * 8}px, 0) rotateX(${-pointer.value.y * 5}deg) rotateY(${pointer.value.x * 7}deg)`,
}));
</script>
<template>
  <figure
    ref="figure"
    class="capability-map"
    :class="{ 'is-paused': paused, 'is-reduced': reduced }"
    aria-label="PMC-Agent 能力关系示意"
  >
    <div class="map-topline">
      <span>PMC / CAPABILITY MAP</span><span>01 — 05</span>
    </div>
    <div class="map-stage" @pointermove="move" @pointerleave="resetPointer">
      <div class="core-aura"></div>
      <svg class="orbit-drawing" viewBox="0 0 620 500" aria-hidden="true">
        <defs>
          <linearGradient id="orbit-line" x1="0" y1="0" x2="1" y2="1">
            <stop
              stop-color="var(--orbit-start)"
              stop-opacity="var(--orbit-start-opacity)"
            />
            <stop
              offset=".48"
              stop-color="var(--orbit-middle)"
              stop-opacity="var(--orbit-middle-opacity)"
            />
            <stop
              offset="1"
              stop-color="var(--orbit-end)"
              stop-opacity="var(--orbit-end-opacity)"
            />
          </linearGradient>
          <filter id="orbital-glow"><feGaussianBlur stdDeviation="3" /></filter>
        </defs>
        <g
          class="map-guides"
          fill="none"
          stroke="url(#orbit-line)"
          stroke-width=".9"
        >
          <path
            d="M310 24v452M34 250h552"
            stroke-dasharray="2 10"
            opacity=".25"
          />
          <circle
            cx="310"
            cy="250"
            r="173"
            opacity=".3"
            stroke-dasharray="2 10"
          />
          <ellipse
            cx="310"
            cy="250"
            rx="248"
            ry="94"
            transform="rotate(-26 310 250)"
          />
          <ellipse
            cx="310"
            cy="250"
            rx="248"
            ry="94"
            transform="rotate(26 310 250)"
          />
          <ellipse cx="310" cy="250" rx="90" ry="203" />
          <path d="M60 42h16M68 34v16M544 450h16M552 442v16" opacity=".7" />
        </g>
        <g class="orbit-lights">
          <g v-for="(point, index) in points" :key="index">
            <circle
              :cx="point.x"
              :cy="point.y"
              r="7"
              fill="var(--orbit-glow)"
              opacity=".5"
              filter="url(#orbital-glow)"
            />
            <circle
              class="orbit-dot"
              :cx="point.x"
              :cy="point.y"
              r="2.7"
              fill="var(--orbit-dot)"
            />
          </g>
        </g>
      </svg>
      <div class="core-parallax" :style="parallax">
        <Float
          :paused="paused || !props.active || !inViewport"
          :amplitude="finePointer ? 9 : 4"
        >
          <svg class="core-drawing" viewBox="0 0 620 500" aria-hidden="true">
            <defs>
              <linearGradient id="glass-top" x1="0" y1="0" x2=".85" y2="1">
                <stop stop-color="var(--chip-top-highlight)" />
                <stop
                  offset=".26"
                  stop-color="var(--chip-top-light)"
                  stop-opacity="var(--chip-top-light-opacity)"
                />
                <stop
                  offset=".72"
                  stop-color="var(--chip-top-blue)"
                  stop-opacity="var(--chip-top-blue-opacity)"
                />
                <stop offset="1" stop-color="var(--chip-top-deep)" />
              </linearGradient>
              <linearGradient id="glass-mid" x1="0" y1="0" x2="1" y2="1">
                <stop
                  stop-color="var(--chip-middle-light)"
                  stop-opacity="var(--chip-middle-light-opacity)"
                />
                <stop
                  offset=".5"
                  stop-color="var(--chip-middle-blue)"
                  stop-opacity="var(--chip-middle-blue-opacity)"
                />
                <stop
                  offset="1"
                  stop-color="var(--chip-middle-deep)"
                  stop-opacity="var(--chip-middle-deep-opacity)"
                />
              </linearGradient>
              <linearGradient id="glass-side" x1="0" y1="0" x2="0" y2="1">
                <stop
                  stop-color="var(--chip-side-light)"
                  stop-opacity="var(--chip-side-opacity)"
                />
                <stop offset="1" stop-color="var(--chip-side-deep)" />
              </linearGradient>
              <radialGradient id="floor-glow">
                <stop stop-color="var(--chip-middle-blue)" stop-opacity=".4" />
                <stop
                  offset="1"
                  stop-color="var(--chip-middle-blue)"
                  stop-opacity="0"
                />
              </radialGradient>
              <filter
                id="core-soft-shadow"
                x="-.5"
                y="-.5"
                width="2"
                height="2"
              >
                <feGaussianBlur stdDeviation="10" />
              </filter>
            </defs>
            <ellipse
              cx="310"
              cy="370"
              rx="100"
              ry="22"
              fill="url(#floor-glow)"
              filter="url(#core-soft-shadow)"
            />
            <g class="core-planes">
              <path
                d="m192 297 118-68 118 68v12l-118 68-118-68Z"
                fill="url(#glass-side)"
                stroke="var(--chip-base-edge)"
              />
              <path
                d="m192 297 118-68 118 68-118 68Z"
                fill="var(--chip-base-fill)"
                stroke="var(--chip-base-highlight)"
                stroke-opacity=".55"
              />
              <path
                d="m192 271 118-68 118 68-118 68Z"
                fill="url(#glass-mid)"
                stroke="var(--chip-layer-edge)"
                stroke-opacity=".68"
              />
              <path
                d="m192 245 118-68 118 68-118 68Z"
                fill="url(#glass-mid)"
                stroke="var(--chip-layer-edge-bright)"
                stroke-opacity=".82"
              />
              <path
                d="m192 215 118-68 118 68v5l-118 69-118-69Z"
                fill="var(--chip-rim-fill)"
                stroke="var(--chip-rim-edge)"
              />
              <path
                d="m192 215 118-68 118 68-118 68Z"
                fill="url(#glass-top)"
                stroke="var(--chip-top-edge)"
                stroke-width="1.2"
              />
              <path
                d="m214 215 96-55 96 55-96 56Z"
                fill="none"
                stroke="var(--chip-inner-edge)"
                stroke-opacity=".4"
              />
              <path
                d="m192 215 118-68 68 39"
                fill="none"
                stroke="var(--chip-specular)"
                stroke-width="1.5"
              />
              <path
                d="M310 147v-23M192 215h-20M428 215h20M310 377v18"
                stroke="var(--chip-guide)"
                stroke-opacity=".75"
              />
              <text
                x="310"
                y="217"
                text-anchor="middle"
                fill="var(--chip-label)"
                font-size="22"
                font-weight="650"
                letter-spacing="-.6"
              >
                PMC-Agent
              </text>
              <text
                x="310"
                y="240"
                text-anchor="middle"
                fill="var(--chip-caption)"
                font-size="8"
                letter-spacing="2.3"
              >
                PLANNING CORE
              </text>
            </g>
          </svg>
        </Float>
      </div>
      <div
        v-for="node in nodes"
        :key="node.name"
        class="capability-node"
        :class="`node-${node.position}`"
      >
        <span class="node-icon"><Icon :name="node.icon" :size="16" /></span
        ><span>{{ node.name }}</span
        ><small>{{ node.state }}</small>
      </div>
      <div class="capability-node node-planned">
        <span class="node-icon"><Icon name="clock" :size="16" /></span
        ><span>产能与排产</span><small>规划</small>
      </div>
    </div>
    <figcaption>
      <span><i></i>能力关系示意 · 非实时执行流程</span>
      <span v-if="reduced" class="motion-preference"
        >已遵循系统减少动态效果</span
      >
      <button
        v-else
        class="motion-toggle"
        :aria-pressed="paused"
        @click="paused = !paused"
      >
        {{ paused ? "开启动效" : "暂停动效" }}
      </button>
    </figcaption>
  </figure>
</template>
