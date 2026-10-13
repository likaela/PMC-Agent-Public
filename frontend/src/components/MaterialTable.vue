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
const showStockDetails = computed(() =>
  !props.result?.bookObservation && ["inventory", "purchase_planning"].includes(props.result?.queryType ?? ""),
);
const showPurchase = computed(
  () => !props.result?.bookObservation && props.result?.queryType === "purchase_planning",
);
function inventoryQuantity(value: string | null | undefined): string {
  return value == null ? "—" : formatNumber(value);
}
// Intent selects eligible columns; evidence selects populated columns. A known
// zero is valid, while an entirely null column has no business display value.
const columns = computed(() => {
  const r = props.result;
  const rows = r?.materials ?? [];
  const bom = r?.queryType === "bom";
  const maximum = (r?.backend?.structured_data?.presentation as {show_maximum?: boolean} | undefined)?.show_maximum === true ||
    (r?.backend?.structured_data?.data as {capacity_query?: boolean} | undefined)?.capacity_query === true;
  const any = (field: keyof typeof rows[number]) => rows.some(row => row[field] != null && row[field] !== "");
  return {per: (bom || maximum || r?.bookObservation) && any("qtyPer"),
    required: !bom && !maximum && any("required"), available: !bom && any("available"),
    recorded: !bom && r?.bookObservation && any("recordedStock"),
    records: !bom && r?.bookObservation && !r?.slimReview && any("recordCount"),
    onHand: !bom && showStockDetails.value && any("onHand"),
    reserved: !bom && showStockDetails.value && any("reserved"),
    shortage: !bom && !maximum && any("shortage"), purchase: showPurchase.value && any("purchaseQty")};
});
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
        `${row.name} ${row.code ?? ""} ${row.spec}`
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
        <p>
          {{
            result?.queryType === "bom"
              ? "单位用量不等于生产总需求"
              : result?.quantityReview ? "已确认实际库存与BOM需求逐项核对；无记录保持未知" : result?.bookObservation ? "主库存原始记录与BOM需求；账面数量不等于可用库存" : "需求、可用库存与缺口，放在一起核对"
          }}
        </p>
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
            <option value="unknown">{{ result?.quantityReview ? "库存无记录或待核验" : "可用库存未知" }}</option>
          </select></label
        >
      </div>
    </div>
    <div v-if="pending" class="table-loading" aria-label="正在加载物料">
      <div v-for="n in 3" :key="n" class="skeleton"></div>
    </div>
    <template v-else-if="result?.materials.length"
      ><p class="table-scroll-hint">
        {{ result?.bookObservation ? "左右滑动查看需求、库存记录与来源" : "左右滑动查看库存、缺口与来源" }} <Icon name="arrow" :size="13" />
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
              <th v-if="columns.per" class="numeric">{{ result?.queryType === 'bom' ? '单位用量' : 'BOM单耗' }}</th>
              <th v-if="columns.required" class="numeric">需求数量</th>
              <th v-if="columns.available" class="numeric">可用库存</th>
              <th v-if="columns.recorded" class="numeric">{{ result?.quantityReview ? "实际库存数量" : "Excel账面数量" }}</th>
              <th v-if="columns.records" class="numeric">记录数</th>
              <th v-if="columns.onHand" class="numeric">在库数量</th>
              <th v-if="columns.reserved" class="numeric">预留数量</th>
              <th v-if="columns.shortage" class="numeric">缺口数量</th>
              <th v-if="columns.purchase" class="numeric">建议采购量</th>
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
                    ><small>{{ row.code }} {{ row.spec }}</small>
                  </div>
                </div>
              </td>
              <td v-if="columns.per" class="numeric">{{ inventoryQuantity(row.qtyPer) }} {{ row.qtyPer == null ? '' : row.unit }}</td>
              <td
                v-if="columns.required"
                class="numeric"
                :class="{ 'missing-value': row.required == null }"
              >
                {{
                  inventoryQuantity(
                    result?.queryType === "bom" ? row.qtyPer : row.required,
                  )
                }}<small
                  v-if="
                    (result?.queryType === 'bom' ? row.qtyPer : row.required) !=
                    null
                  "
                  >{{ " " + row.unit }}</small
                >
              </td>
              <td
                v-if="columns.available"
                class="numeric"
                :class="{ 'missing-value': row.available == null }"
              >
                {{
                  row.state === "no-facts"
                    ? "暂无已发布数据"
                    : inventoryQuantity(row.available)
                }}<small v-if="row.available != null">{{
                  " " + row.unit
                }}</small>
              </td>
              <td v-if="columns.recorded" class="numeric">
                {{ row.recordFound === false ? "无记录" : inventoryQuantity(row.recordedStock) }}
                <small v-if="row.recordedStock != null">{{ row.inventoryUnit ?? '' }}</small>
              </td>
              <td v-if="columns.records" class="numeric">{{ row.recordCount ?? "—" }}</td>
              <td v-if="columns.onHand" class="numeric">
                {{ inventoryQuantity(row.onHand)
                }}<small v-if="row.onHand != null">{{ " " + row.unit }}</small>
              </td>
              <td v-if="columns.reserved" class="numeric">
                {{ inventoryQuantity(row.reserved)
                }}<small v-if="row.reserved != null">{{
                  " " + row.unit
                }}</small>
              </td>
              <td
                v-if="columns.shortage"
                class="numeric"
                :class="{
                  'shortage-number': row.state === 'shortage',
                  'missing-value': row.shortage == null,
                }"
              >
                {{ inventoryQuantity(row.shortage)
                }}<small v-if="row.shortage != null">{{
                  " " + row.unit
                }}</small>
              </td>
              <td
                v-if="columns.purchase"
                class="numeric"
                :class="{ 'missing-value': row.purchaseQty == null }"
              >
                {{
                  row.purchaseQty == null
                    ? "—"
                    : formatNumber(row.purchaseQty)
                }}<small v-if="row.purchaseQty != null">{{
                  " " + row.unit
                }}</small>
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
                  >{{ result?.quantityReview && ['shortage', 'sufficient'].includes(row.state) ? materialText[row.state] : result?.bookObservation ? (row.recordFound === false ? "无记录" : row.recordedStock == null ? "合计待核验" : "已有账面记录") : materialText[row.state] }}</StatusBadge
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
              <td
                :colspan="
                  3 + Object.values(columns).filter(Boolean).length
                "
                class="filter-empty"
              >
                没有匹配的物料，请调整搜索或筛选。
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="table-footnote">
        <span
          ><Icon name="info" :size="14" />{{ result?.bookObservation ? "按本次核验记录展示数量" : "数值来自本次分析结果" }}</span
        ><span>共 {{ rows.length }} 项物料</span>
      </div></template
    >
    <div v-else class="table-empty">
      <Icon name="layers" :size="24" />
      <p>
        {{
          result
            ? "本次结果没有物料明细"
            : "发起一次查询后，物料明细会显示在这里"
        }}
      </p>
      <small>仅展示明确提供的数据，不从自然语言中猜测数值</small>
    </div>
  </section>
</template>
