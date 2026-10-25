<script setup lang="ts">
import { ref, nextTick, watch, onMounted } from "vue";
import { useAgent } from "../composables/useAgent";
import Icon from "./Icon.vue";
const { draft, pending, send, cancel, canSend, product } = useAgent();
const composing = ref(false);
const input = ref<HTMLTextAreaElement>();
function resizeInput() {
  const field = input.value;
  if (!field) return;
  field.style.height = "auto";
  field.style.height = `${Math.min(176, Math.max(52, field.scrollHeight))}px`;
  field.style.overflowY = field.scrollHeight > 176 ? "auto" : "hidden";
}
onMounted(resizeInput);
watch(draft, async () => {
  await nextTick();
  resizeInput();
});
watch(pending, async (value, before) => {
  if (before && !value) {
    await nextTick();
    input.value?.focus({ preventScroll: true });
  }
});
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
    <label for="query-input" class="sr-only">输入分析问题</label>
    <div class="composer-input">
      <textarea
        ref="input"
        id="query-input"
        aria-describedby="composer-hint"
        v-model="draft"
        :disabled="pending"
        maxlength="2000"
        rows="2"
        :placeholder="
          product
            ? `继续询问 ${product.product_code}，或输入新的产品问题…`
            : '例如：KQ-Z112 生产 2 片，物料够吗？'
        "
        @input="resizeInput"
        @keydown="onKey"
        @compositionstart="composing = true"
        @compositionend="composing = false"
      ></textarea>
      <div class="composer-actions">
        <span
          ><kbd>Enter</kbd> 发送
          <span class="desktop-only">· Shift + Enter 换行</span></span
        ><Transition name="prompt-action" mode="out-in"
          ><button
            v-if="pending"
            key="stop"
            class="cancel-button"
            aria-label="停止等待本次响应"
            type="button"
            title="停止等待本次响应，不代表后端计算已停止"
            @click="cancel()"
          >
            <Icon name="stop" :size="14" />停止等待</button
          ><button
            v-else
            key="send"
            class="send-button"
            type="submit"
            :disabled="!draft.trim() || !canSend"
            aria-label="发送问题"
          >
            <Icon name="send" :size="19" /></button
        ></Transition>
      </div>
    </div>
    <p id="composer-hint" class="composer-hint">
      {{
        product
          ? "沿用当前产品与对话；提到新产品时将单独建立分析。"
          : "直接输入型号与问题，不必预先选择产品。"
      }}
    </p>
  </form>
</template>
