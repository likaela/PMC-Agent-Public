<script setup lang="ts">
import { computed, ref, watch } from "vue";
import Icon from "./Icon.vue";
import StatusBadge from "./StatusBadge.vue";
import { formatNumber, materialText, type AgentResult } from "../data/model";
const props = defineProps<{ result?: AgentResult; pending: boolean }>();
defineEmits<{ source: [ids: string[]] }>();
const filter = ref("all");
const search = ref("");
const ascending = ref<boolean | null>(null);
watch(
  () => props.result,
  () => {
    filter.value = "all";
    search.value = "";
    ascending.value = null;
  },
);
const rows = computed(() =>
  (props.result?.materials ?? [])
    .filter(
      (row) =>
        (filter.value === "all" || row.state === filter.value) &&
        `${row.name} ${row.spec}`
          .toLowerCase()
          .includes(search.value.toLowerCase()),
    )
    .sort((a, b) =>
      ascending.value === null
        ? 0
        : ascending.value
          ? a.name.localeCompare(b.name, "zh-CN")
          : b.name.localeCompare(a.name, "zh-CN"),
    ),
);
</script>
<template>
  <section class="card materials-card" aria-labelledby="materials-title">
    <div class="card-heading">
      <div class="title-with-subtitle">
        <h2 id="materials-title">
          <Icon name="layers" :size="19" />物料明细<span class="count-chip">{{
            result?.materials.length ?? 0
          }}</span>
        </h2>
        <p>需求、库存与缺口，放在一起核对</p>
      </div>
      <div class="table-controls">
        <label class="table-search"
          ><Icon name="search" :size="16" /><input
            v-model="search"
            aria-label="搜索物料"
            placeholder="搜索物料"
            :disabled="!result?.materials.length" /></label
        ><label class="table-filter"
          ><Icon name="filter" :size="15" /><select
            v-model="filter"
            aria-label="筛选物料状态"
            :disabled="!result?.materials.length"
          >
            <option value="all">全部物料</option>
            <option value="shortage">仅看缺料</option>
            <option value="unknown">库存待补充</option>
          </select></label
        >
      </div>
    </div>
    <div v-if="pending" class="table-loading" aria-label="正在加载物料">
      <div v-for="n in 3" :key="n" class="skeleton"></div>
    </div>
    <template v-else-if="result?.materials.length"
      ><p class="table-scroll-hint">
        左右滑动查看库存、缺口与来源 <Icon name="arrow" :size="13" />
      </p>
      <div
        class="table-scroll"
        tabindex="0"
        role="region"
        aria-label="物料明细表，可横向滚动"
      >
        <table>
          <thead>
            <tr>
              <th
                :aria-sort="
                  ascending === null
                    ? 'none'
                    : ascending
                      ? 'ascending'
                      : 'descending'
                "
              >
                <button class="sort-button" @click="ascending = !ascending">
                  物料名称<Icon
                    :name="ascending ? 'down' : 'chevron'"
                    :size="13"
                  />
                </button>
              </th>
              <th class="numeric">需求数量</th>
              <th class="numeric">库存数量</th>
              <th class="numeric">缺口数量</th>
              <th>物料状态</th>
              <th>数据来源</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in rows"
              :key="row.id"
              :class="{ 'shortage-row': row.state === 'shortage' }"
            >
              <td>
                <div class="material-name">
                  <span class="material-icon"
                    ><Icon name="box" :size="18"
                  /></span>
                  <div>
                    <strong>{{ row.name }}</strong
                    ><small>{{ row.spec }}</small>
                  </div>
                </div>
              </td>
              <td
                class="numeric"
                :class="{ 'missing-value': row.required == null }"
              >
                {{ formatNumber(row.required)
                }}<small v-if="row.required != null"> {{ row.unit }}</small>
              </td>
              <td
                class="numeric"
                :class="{ 'missing-value': row.available == null }"
              >
                {{
                  row.state === "no-facts"
                    ? "暂无已发布数据"
                    : formatNumber(row.available)
                }}<small v-if="row.available != null"> {{ row.unit }}</small>
              </td>
              <td
                class="numeric"
                :class="{
                  'shortage-number': row.state === 'shortage',
                  'missing-value': row.shortage == null,
                }"
              >
                {{ formatNumber(row.shortage)
                }}<small v-if="row.shortage != null"> {{ row.unit }}</small>
              </td>
              <td>
                <StatusBadge
                  :tone="
                    row.state === 'shortage'
                      ? 'orange'
                      : row.state === 'sufficient'
                        ? 'green'
                        : row.state === 'published'
                          ? 'blue'
                          : 'neutral'
                  "
                  >{{ materialText[row.state] }}</StatusBadge
                >
              </td>
              <td>
                <button
                  class="text-button"
                  :aria-label="`查看${row.name}来源`"
                  @click="$emit('source', row.sourceIds)"
                >
                  查看来源<Icon name="chevron" :size="13" />
                </button>
              </td>
            </tr>
            <tr v-if="!rows.length">
              <td colspan="6" class="filter-empty">
                没有匹配的物料，请调整搜索或筛选。
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="table-footnote">
        <span
          ><Icon name="info" :size="14" />未提供 ≠
          0；缺口来自固定样例或后端结果</span
        ><span>共 {{ rows.length }} 项物料</span>
      </div></template
    >
    <div v-else class="table-empty">
      <Icon name="layers" :size="24" />
      <p>
        {{
          result
            ? "本次结果未提供可展示的物料明细"
            : "发起一次查询后，物料明细会显示在这里"
        }}
      </p>
      <small>仅展示明确提供的数据，不从自然语言中猜测数值</small>
    </div>
  </section>
</template>
