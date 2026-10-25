<script setup lang="ts">
import type { AgentResult } from "../data/model";
defineProps<{ result: AgentResult }>();
</script>
<template>
  <section
    v-if="result.backend"
    id="business-details"
    class="card backend-details"
    aria-label="业务明细"
  >
    <h2>{{ result.title }}</h2>
    <div class="backend-metrics">
      <div v-for="(m, i) in result.backend.metrics" :key="i">
        <small>{{ m.label }}</small
        ><strong>{{ m.value }}</strong>
      </div>
    </div>
    <p
      v-if="
        ['production', 'maximum', 'shortage'].includes(
          result.backend.resultType,
        )
      "
      class="backend-note"
    >
      按所示数据范围核对物料，不代表正式生产放行。未提供与无记录均不等于 0。
    </p>
    <p v-if="result.backend.resultType === 'purchase'" class="backend-note">
      采购建议不代表已下单或已到货；数量、约束、价格与成本均由后端提供。
    </p>
    <div
      v-if="result.backend.rows.length"
      class="table-scroll"
      tabindex="0"
      role="region"
      aria-label="业务明细表"
    >
      <table>
        <thead>
          <tr>
            <th
              v-for="c in result.backend.columns"
              :key="c.key"
              :class="{ numeric: c.numeric }"
            >
              {{ c.label }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(row, i) in result.backend.rows"
            :key="i"
            :class="{ 'backend-shortage': row.status === '不满足需求' }"
          >
            <td
              v-for="c in result.backend.columns"
              :key="c.key"
              :class="{ numeric: c.numeric }"
            >
              {{ row[c.key] }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="backend-note">
      本轮未返回可用的明细表；请阅读回答与来源。未知结构保留在原始证据中。
    </p>
  </section>
</template>
