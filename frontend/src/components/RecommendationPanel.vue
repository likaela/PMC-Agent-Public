<script setup lang="ts">
import Icon from "./Icon.vue";
import { formatNumber, type AgentResult } from "../data/model";
defineProps<{ result?: AgentResult }>();
</script>
<template>
  <section
    v-if="result && (result.conclusion || result.nextAction || result.purchase)"
    class="card recommendation-panel"
    aria-labelledby="recommendation-title"
  >
    <div class="card-heading">
      <h2 id="recommendation-title">
        <span class="report-step">03</span>建议与下一步
      </h2>
      <span class="subtle-label">仅供参考 · 需人工核实</span>
    </div>
    <div class="recommendation-content">
      <div v-if="result.purchase" class="purchase-summary">
        <div class="purchase-grid">
          <div>
            <span>缺料数量</span
            ><strong
              >{{ formatNumber(result.purchase.shortage)
              }}<small>{{ result.purchase.unit }}</small></strong
            >
          </div>
          <div>
            <span>安全库存</span
            ><strong
              >{{ formatNumber(result.purchase.safetyStock)
              }}<small>{{ result.purchase.unit }}</small></strong
            >
          </div>
          <div>
            <span>建议采购</span
            ><strong
              >{{ formatNumber(result.purchase.suggested)
              }}<small>{{ result.purchase.unit }}</small></strong
            >
          </div>
        </div>
        <p class="purchase-note">{{ result.purchase.note }}</p>
      </div>
      <div class="next-action">
        <span class="next-action-icon"><Icon name="arrow" :size="18" /></span>
        <div>
          <strong>{{ result.conclusion }}</strong>
          <p>{{ result.nextAction }}</p>
        </div>
      </div>
    </div>
  </section>
</template>
