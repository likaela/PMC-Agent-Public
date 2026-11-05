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
      需求、库存与缺口见下表。
    </p>
    <p v-if="result.backend.resultType === 'purchase'" class="backend-note">
      结合供应商交期及未交付订单核实补料方案。
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
      本次查询暂无物料明细。
    </p>
  </section>
</template>
