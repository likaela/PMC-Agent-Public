<script setup lang="ts">
/**
 * 库存变化折线图（阶段三，2026-10-29）。
 *
 * 只在后端给出真实历史快照/流水时渲染；每个物料+单位一条线，按真实时间
 * 定位，不做插值、不跨物料连线，也不把一次查询或对话时间当作趋势点。
 * 没有可靠的多时点记录时不渲染，也不从对话中拼接历史。
 */
import { computed } from "vue";
import {
  niceTicks,
  trendDateText,
  trendSummary,
  type TrendPoint,
  type TrendSeries,
} from "../services/analysisChart";
const props = defineProps<{ series: TrendSeries[] }>();
const summary = computed(() => trendSummary(props.series));

const VIEW_W = 560,
  VIEW_H = 196,
  PAD_L = 46,
  PAD_R = 14,
  PAD_T = 12,
  PAD_B = 30;
const PLOT_W = VIEW_W - PAD_L - PAD_R,
  PLOT_H = VIEW_H - PAD_T - PAD_B;

function yScale(value: number, max: number): number {
  const scale = Math.max(max, ...niceTicks(max));
  return PAD_T + PLOT_H - (scale > 0 ? (value / scale) * PLOT_H : 0);
}
function xScale(timestamp: number, first: number, last: number): number {
  if (last <= first) return PAD_L;
  return PAD_L + ((timestamp - first) / (last - first)) * PLOT_W;
}
function formatDate(at: string): string {
  return trendDateText(at);
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
function points(series: TrendSeries): TrendPoint[] {
  return series.points;
}
function linePath(series: TrendSeries): string {
  const first = series.points[0].timestamp;
  const last = series.points.at(-1)!.timestamp;
  return series.points
    .map(
      (point, index) =>
        `${index ? "L" : "M"}${xScale(point.timestamp, first, last).toFixed(1)},${yScale(point.value, maxOf(series)).toFixed(1)}`,
    )
    .join(" ");
}
function maxOf(series: TrendSeries): number {
  return series.points.reduce((top, point) => Math.max(top, point.value), 0);
}
function xLabels(series: TrendSeries): { x: number; text: string }[] {
  const first = series.points[0].timestamp;
  const last = series.points.at(-1)!.timestamp;
  const indices =
    series.points.length >= 3
      ? [
          0,
          Math.floor((series.points.length - 1) / 2),
          series.points.length - 1,
        ]
      : [0, series.points.length - 1];
  return indices.map((index) => ({
    x: xScale(series.points[index].timestamp, first, last),
    text: formatDate(series.points[index].at),
  }));
}
</script>
<template>
  <figure v-if="series.length" class="trend-chart">
    <figcaption class="trend-head">
      <span class="trend-title">库存变化趋势</span>
      <span class="trend-caption">仅展示后端提供的真实历史快照/流水</span>
    </figcaption>
    <section
      v-for="item in series"
      :key="item.key"
      class="trend-series"
      :aria-label="summary"
    >
      <div class="trend-series-head">
        <strong>{{ item.code }}</strong>
        <small v-if="item.name && item.name !== item.code">{{
          item.name
        }}</small>
        <span>单位：{{ item.unit }}</span>
        <span>口径：{{ item.basis }}</span>
        <span v-if="item.product">产品：{{ item.product }}</span>
        <span v-if="item.scope">范围：{{ item.scope }}</span>
        <span>{{ item.points.length }} 个快照</span>
      </div>
      <svg
        class="trend-svg"
        :viewBox="`0 0 ${VIEW_W} ${VIEW_H}`"
        role="img"
        :aria-label="summary"
        preserveAspectRatio="xMidYMid meet"
      >
        <g v-for="tick in niceTicks(maxOf(item))" :key="`y-${tick}`">
          <line
            :x1="PAD_L"
            :x2="VIEW_W - PAD_R"
            :y1="yScale(tick, maxOf(item))"
            :y2="yScale(tick, maxOf(item))"
            class="trend-grid"
          />
          <text
            :x="PAD_L - 6"
            :y="yScale(tick, maxOf(item)) + 3.5"
            text-anchor="end"
            class="trend-tick"
          >
            {{ formatTick(tick) }}
          </text>
        </g>
        <path :d="linePath(item)" class="trend-line" />
        <circle
          v-for="point in points(item)"
          :key="`${point.timestamp}`"
          :cx="
            xScale(
              point.timestamp,
              item.points[0].timestamp,
              item.points.at(-1)!.timestamp,
            )
          "
          :cy="yScale(point.value, maxOf(item))"
          r="3.2"
          class="trend-point"
        >
          <title>
            {{ point.at }} · {{ point.text }} {{ item.unit }}（{{ item.basis }}）
          </title>
        </circle>
        <text
          v-for="(label, index) in xLabels(item)"
          :key="`x-${index}`"
          :x="label.x"
          :y="VIEW_H - 8"
          text-anchor="middle"
          class="trend-x-label"
        >
          {{ label.text }}
        </text>
      </svg>
      <ul class="sr-only">
        <li v-for="point in item.points" :key="`sr-${point.timestamp}`">
          {{ formatDate(point.at) }}：{{ point.text }} {{ item.unit }}
        </li>
      </ul>
    </section>
  </figure>
</template>
