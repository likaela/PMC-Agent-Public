<script setup lang="ts">
import { ref } from "vue";
import { useAgent } from "../composables/useAgent";
import Icon from "./Icon.vue";
const { draft, pending, send, cancel } = useAgent();
defineProps<{
  activeProduct?: string | null;
  products?: { product_code: string }[];
}>();
defineEmits<{ switch: [code: string] }>();
const composing = ref(false);
function onKey(event: KeyboardEvent) {
  if (
    event.key === "Enter" &&
    !event.shiftKey &&
    !event.isComposing &&
    !composing.value &&
    event.keyCode !== 229
  ) {
    event.preventDefault();
    void send();
  }
}
</script>
<template>
  <form class="composer" @submit.prevent="send()">
    <div class="active-product" aria-label="当前活动产品">
      <label for="active-product-select">当前产品</label>
      <select
        v-if="products?.length"
        id="active-product-select"
        :value="activeProduct ?? ''"
        @change="$emit('switch', ($event.target as HTMLSelectElement).value)"
      >
        <option value="" disabled>未选择产品</option>
        <option
          v-for="p in products"
          :key="p.product_code"
          :value="p.product_code"
        >
          {{ p.product_code }}
        </option>
      </select>
      <span v-else>{{ activeProduct ?? "未选择产品" }}</span>
    </div>
    <label for="query-input" class="sr-only">输入分析问题</label>
    <div class="composer-input">
      <textarea
        id="query-input"
        v-model="draft"
        :disabled="pending"
        maxlength="2000"
        rows="2"
        placeholder="请输入产品编码和生产数量，或物料编码与查询问题…"
        @keydown="onKey"
        @compositionstart="composing = true"
        @compositionend="composing = false"
      ></textarea>
      <div class="composer-actions">
        <span
          ><kbd>Enter</kbd> 发送
          <span class="desktop-only">· Shift + Enter 换行</span></span
        ><button
          v-if="pending"
          class="cancel-button"
          type="button"
          @click="cancel()"
        >
          <Icon name="stop" :size="14" />取消</button
        ><button
          v-else
          class="send-button"
          type="submit"
          :disabled="!draft.trim()"
          aria-label="发送问题"
        >
          <Icon name="send" :size="19" />
        </button>
      </div>
    </div>
    <p class="composer-hint">
      可输入产品编码或别称；后续问题沿用当前产品，会话自动保存。
    </p>
  </form>
</template>
<style scoped>
.active-product {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #526581;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.active-product select {
  max-width: 100%;
  border: 1px solid #dbe5f3;
  border-radius: 6px;
  background: #f2f7ff;
  color: #27528c;
  padding: 3px 8px;
  font-size: 12px;
}
</style>
