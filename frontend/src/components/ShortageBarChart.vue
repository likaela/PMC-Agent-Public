<script setup lang="ts">
/**
 * 缺料横向条形图（阶段二，2026-10-29）。
 *
 * 只列需要处理的物料，按缺口从大到小排序；横向条让长物料名称保持可读，
 * 采购量只在后端给出时随提示展示。不同单位分开展示、各自比例尺；
 * 库存未提供/缺口未量化的物料只给计数提示，绝不补值或画成 0。
 */
import { computed } from "vue";
import {
  shortageSummary,
  type ShortageItem,
  type ShortageUnitGroup,
} from "../services/analysisChart";
const props = defineProps<{ groups: ShortageUnitGroup[] }>();
const summary = computed(() => shortageSummary(props.groups));
const total = computed(() =>
  props.groups.reduce((sum, group) => sum + group.items.length, 0),
);
function percent(item: ShortageItem, max: number): string {
  if (max <= 0) return "0%";
  return `${Math.max((item.shortage.value / max) * 100, 1.5)}%`;
}
function speak(item: ShortageItem, unit: string): string {
  const suggested = item.suggested
    ? `；需要采购 ${item.suggested.text} ${unit}`
    : "";
  return `${item.code}${item.name && item.name !== item.code ? `（${item.name}）` : ""}：缺口 ${item.shortage.text} ${unit}${suggested}。`;
}
</script>
<template>
  <figure v-if="groups.length" class="shortage-chart">
    <figcaption class="shortage-head">
      <span class="shortage-title">缺料清单</span>
      <span class="shortage-caption"
        >按缺口从大到小 · 共 {{ total }} 种需要处理</span
      >
    </figcaption>
    <div class="shortage-legend">
      <span><i class="shortage-legend-bar" aria-hidden="true"></i>缺口数量</span
      ><span class="shortage-legend-note"
        >只列需要处理的物料；未提供/未量化的缺口不计入</span
      >
    </div>
    <section
      v-for="group in groups"
      :key="group.unit"
      class="shortage-unit"
      :aria-label="`${summary}（本组单位：${group.unit}）`"
    >
      <div class="shortage-unit-head">
        <span>单位：{{ group.unit }}</span>
        <span>{{ group.items.length }} 种</span>
      </div>
      <div
        class="shortage-scroll"
        :class="{ 'is-scrollable': group.items.length > 8 }"
        tabindex="0"
      >
        <ol class="shortage-list">
          <li
            v-for="item in group.items"
            :key="item.key"
            class="shortage-row"
            :title="
              item.suggested
                ? `${item.code} ${item.name}：缺口 ${item.shortage.text} ${group.unit}；需要采购 ${item.suggested.text} ${group.unit}`
                : `${item.code} ${item.name}：缺口 ${item.shortage.text} ${group.unit}`
            "
          >
            <span class="shortage-name">
              <strong>{{ item.code }}</strong>
              <small v-if="item.name && item.name !== item.code">{{
                item.name
              }}</small>
            </span>
            <span class="shortage-track" aria-hidden="true">
              <span
                class="shortage-fill"
                :style="{ width: percent(item, group.max) }"
              ></span>
            </span>
            <span class="shortage-value">
              <strong>{{ item.shortage.text }}</strong>
              <small>{{ group.unit }}</small>
            </span>
          </li>
        </ol>
      </div>
      <span v-if="group.items.length > 8" class="shortage-scroll-hint">
        共 {{ group.items.length }} 种缺料，可纵向滚动查看
      </span>
      <p
        v-if="group.missingStockCount || group.unquantifiedCount"
        class="shortage-note"
      >
        <span v-if="group.missingStockCount"
          >另有
          {{
            group.missingStockCount
          }}
          种物料库存未提供，未计入缺口，请先核实库存。</span
        >
        <span v-if="group.unquantifiedCount"
          >另有
          {{
            group.unquantifiedCount
          }}
          种物料标记为不足但缺口数量未提供，未绘制条形。</span
        >
      </p>
      <ul class="sr-only">
        <li v-for="item in group.items" :key="`sr-${item.key}`">
          {{ speak(item, group.unit) }}
        </li>
      </ul>
    </section>
  </figure>
</template>
