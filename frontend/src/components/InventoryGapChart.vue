<script setup lang="ts">
/**
 * 需求 / 库存分组柱状图（2026-10-26）。
 *
 * 交互参考 Unovis GroupedBar（https://unovis.dev/gallery/），但为避免给
 * Streamlit 云端包引入图表运行时依赖，这里用少量 SVG 实现同类的分组对比：
 * 需求量与库存并排，缺库存画虚线槽并标注“未提供”（不画成 0），真实 0 与
 * “未提供”分开，不同单位分开展示。值只用于缩放，提示框展示后端原始文本。
 */
import { computed } from "vue";
import {
  gapSummary,
  niceTicks,
  type GapBar,
  type GapUnitGroup,
} from "../services/analysisChart";
const props = defineProps<{ groups: GapUnitGroup[] }>();
const summary = computed(() => gapSummary(props.groups));

const BAR_W = 11,
  BAR_GAP = 3,
  GROUP_W = BAR_W * 2 + BAR_GAP + 17,
  // 纵轴刻度文字占用的宽度；柱状区必须从它右侧开始，否则首组柱会压在刻度上。
  Y_W = 54,
  // 刻度与第一组柱之间的留白。
  AXIS_GAP = 10,
  PLOT_LEFT = Y_W + AXIS_GAP,
  PAD_RIGHT = 10,
  PLOT_H = 150,
  PAD_TOP = 8,
  X_LABEL_H = 46;

function chartWidth(items: number): number {
  return Math.max(PLOT_LEFT + items * GROUP_W + PAD_RIGHT, 320);
}
function chartHeight(): number {
  return PAD_TOP + PLOT_H + X_LABEL_H;
}
function barY(value: number, max: number): number {
  const scale = Math.max(max, ...niceTicks(max));
  return PAD_TOP + PLOT_H - (scale > 0 ? (value / scale) * PLOT_H : 0);
}
function barHeight(value: number, max: number): number {
  const scale = Math.max(max, ...niceTicks(max));
  if (scale <= 0) return 0;
  return Math.max((value / scale) * PLOT_H, value > 0 ? 2 : 0);
}
function formatTick(value: number): string {
  if (value !== 0 && (Math.abs(value) >= 1e6 || Math.abs(value) < 0.01))
    return value.toExponential(1);
  return Number.isInteger(value)
    ? new Intl.NumberFormat("zh-CN").format(value)
    : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(
        value,
      );
}
function xLabel(code: string): string {
  return code.length > 9 ? `${code.slice(0, 9)}…` : code;
}
function speak(item: GapBar, unit: string): string {
  const demand = item.demand
    ? `需求 ${item.demand.text} ${unit}`
    : "需求量未提供";
  const stock = item.stock
    ? `库存 ${item.stock.text} ${unit}（${item.stock.basis}）`
    : "库存未提供";
  return `${item.code}${item.name && item.name !== item.code ? `（${item.name}）` : ""}：${demand}；${stock}。`;
}
</script>
<template>
  <div v-if="groups.length" class="gap-chart">
    <div class="gap-chart-head">
      <span class="gap-chart-title">需求 / 库存对比</span>
      <span class="gap-chart-unit">
        {{ groups.length > 1 ? "按单位分开展示" : `单位：${groups[0].unit}` }}
      </span>
    </div>
    <div class="gap-chart-legend">
      <span><i class="gap-legend-dot demand" aria-hidden="true"></i>需求量</span
      ><span><i class="gap-legend-dot stock" aria-hidden="true"></i>库存</span
      ><span
        ><i class="gap-legend-dot slot" aria-hidden="true"></i>未提供 ≠ 0</span
      ><span class="gap-legend-note"
      >仅对比单位一致的库存；优先使用后端已换算的核对库存</span
      >
    </div>
    <section
      v-for="group in groups"
      :key="group.unit"
      class="gap-chart-panel"
      :aria-label="`${summary}（本组单位：${group.unit}）`"
    >
      <div class="gap-chart-scroll" tabindex="0">
        <svg
          :width="chartWidth(group.items.length)"
          :height="chartHeight()"
          :viewBox="`0 0 ${chartWidth(group.items.length)} ${chartHeight()}`"
          class="gap-svg"
          role="img"
          :aria-label="summary"
          preserveAspectRatio="xMinYMin meet"
        >
          <g v-for="tick in niceTicks(group.max)" :key="`tick-${tick}`">
            <line
              :x1="Y_W"
              :x2="chartWidth(group.items.length) - PAD_RIGHT"
              :y1="barY(tick, group.max)"
              :y2="barY(tick, group.max)"
              class="gap-grid"
            />
            <text
              :x="Y_W - 6"
              :y="barY(tick, group.max) + 3.5"
              text-anchor="end"
              class="gap-tick"
            >
              {{ formatTick(tick) }}
            </text>
          </g>
          <g
            v-for="(item, index) in group.items"
            :key="item.key"
            class="gap-group"
            :transform="`translate(${PLOT_LEFT + index * GROUP_W}, 0)`"
          >
            <rect
              v-if="item.demand"
              class="gap-bar demand"
              :x="0"
              :y="barY(item.demand.value, group.max)"
              :width="BAR_W"
              :height="barHeight(item.demand.value, group.max)"
            >
              <title>需求 {{ item.demand.text }} {{ group.unit }}</title>
            </rect>
            <template v-if="item.stock">
              <rect
                v-if="item.stock.value > 0"
                class="gap-bar stock"
                :x="BAR_W + BAR_GAP"
                :y="barY(item.stock.value, group.max)"
                :width="BAR_W"
                :height="barHeight(item.stock.value, group.max)"
              >
                <title>
                  库存 {{ item.stock.text }} {{ group.unit }}（{{
                    item.stock.basis
                  }}）
                </title>
              </rect>
              <rect
                v-else
                class="gap-bar stock zero"
                :x="BAR_W + BAR_GAP"
                :y="PAD_TOP + PLOT_H - 1"
                :width="BAR_W"
                :height="1"
              >
                <title>库存 {{ item.stock.text }} {{ group.unit }}（{{ item.stock.basis }}）</title>
              </rect>
            </template>
            <template v-else>
              <rect
                class="gap-slot"
                :x="BAR_W + BAR_GAP"
                :y="PAD_TOP"
                :width="BAR_W"
                :height="PLOT_H"
              />
            </template>
            <text
              class="gap-x-label"
              :x="(BAR_W * 2 + BAR_GAP) / 2"
              :y="PAD_TOP + PLOT_H + 16"
              text-anchor="middle"
            >
              {{ xLabel(item.code) }}
              <title>{{ item.code }} {{ item.name }}</title>
            </text>
            <text
              v-if="item.stock === null"
              class="gap-missing-line"
              :x="(BAR_W * 2 + BAR_GAP) / 2"
              :y="PAD_TOP + PLOT_H + 30"
              text-anchor="middle"
            >
              未提供
            </text>
          </g>
        </svg>
      </div>
      <span v-if="group.items.length >= 12" class="gap-scroll-hint">
        物料较多，可横向滚动查看全部
      </span>
      <ul class="sr-only">
        <li v-for="item in group.items" :key="item.key">
          {{ speak(item, group.unit) }}
        </li>
      </ul>
    </section>
  </div>
</template>
