<script setup lang="ts">
import { ref, watch, nextTick } from "vue";
import Icon from "./Icon.vue";
import StatusBadge from "./StatusBadge.vue";
import type { AgentResult } from "../data/model";
import { sourceLabel } from "../services/productLanguage";
const props = defineProps<{ result?: AgentResult; mode: string }>();
const root = ref<HTMLElement>();
const open = ref(false);
const highlighted = ref<string[]>([]);
watch(
  () => props.result,
  () => {
    open.value = false;
    highlighted.value = [];
  },
);
async function reveal(ids: string[] = []) {
  open.value = true;
  highlighted.value = ids;
  await nextTick();
  root.value?.scrollIntoView({
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "instant"
      : "smooth",
    block: "start",
  });
  root.value?.focus({ preventScroll: true });
}
defineExpose({ reveal });
</script>
<template>
  <section
    ref="root"
    class="card sources-card"
    tabindex="-1"
    aria-label="数据来源与适用条件"
  >
    <button
      class="source-toggle"
      :aria-expanded="open"
      aria-controls="source-details"
      @click="open = !open"
    >
      <span class="source-title"
        ><span class="source-icon"><Icon name="shield" :size="21" /></span
        ><span
          ><strong>数据来源与适用条件</strong
          ><small>{{
            result
            ? `${result.sources.length} 项分析来源 · ${result.slimReview ? "当前分析范围" : result.backend?.scenario_id ? "演示产品" : "已发布记录"}`
              : "结论的依据，与结论一样重要"
          }}</small></span
        ></span
      ><span class="source-toggle-label"
        >{{ open ? "收起详情" : "展开详情"
        }}<Icon name="down" :size="17" :class="{ rotated: open }"
      /></span>
    </button>
    <div v-if="open" id="source-details" class="source-details">
      <p class="source-disclaimer">
        <Icon name="info" :size="16" />{{
          result?.slimReview ? "仅适用于当前分析范围。采用演示库存时会单独标注，不用于完整产品生产放行。" : result?.backend?.scenario_id
            ? "演示产品数据，仅用于体验分析流程，不用于业务放行。"
            : "仅展示后端返回的来源，数据时点不等于请求时间。"
        }}
      </p>
      <div v-if="!result?.sources.length" class="source-no-data">
        本次尚无来源可展示。
      </div>
      <article
        v-for="source in result?.sources"
        :key="source.id"
        class="source-record"
        :class="{ highlighted: highlighted.includes(source.id) }"
      >
        <div class="source-record-title">
          <Icon name="file" :size="18" /><strong>{{ source.title }}</strong
          ><StatusBadge
            :tone="source.kind === '项目资料示例' ? 'blue' : 'orange'"
            >{{ sourceLabel(source.kind) }}</StatusBadge
          >
        </div>
        <dl>
          <div>
            <dt>引用范围</dt>
            <dd>
              {{ source.scope
              }}
            </dd>
          </div>
          <div>
            <dt>来源文件 / 记录</dt>
            <dd>{{ source.reference }}</dd>
          </div>
          <div v-if="source.version">
            <dt>数据版本</dt>
            <dd>{{ source.version }}</dd>
          </div>
          <div v-if="source.publishedAt">
            <dt>来源发布时间</dt>
            <dd>{{ source.publishedAt }}</dd>
          </div>
          <div v-if="source.snapshotAt">
            <dt>库存快照时点</dt>
            <dd>{{ source.snapshotAt }}</dd>
          </div>
          <div v-if="source.observedAt">
            <dt>数据观察时间</dt>
            <dd>{{ source.observedAt }}</dd>
          </div>
          <div v-if="source.effectiveFrom">
            <dt>生效日期</dt>
            <dd>{{ source.effectiveFrom }}</dd>
          </div>
          <div v-if="source.importBatch">
            <dt>导入批次参考</dt>
            <dd>{{ source.importBatch.slice(-8) }}</dd>
          </div>
          <div v-if="source.sheet">
            <dt>工作表</dt>
            <dd>{{ source.sheet }}</dd>
          </div>
          <div v-if="source.row">
            <dt>来源行号</dt>
            <dd>{{ source.row }}</dd>
          </div>
          <div v-if="source.sha256">
            <dt>文件校验摘要</dt>
            <dd>{{ source.sha256.slice(0, 12) }}</dd>
          </div>
          <div class="full-row">
            <dt>适用条件</dt>
            <dd>{{ source.conditions }}</dd>
          </div>
        </dl>
      </article>
    </div>
  </section>
</template>
