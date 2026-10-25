<script setup lang="ts">
import { computed, ref, watch, nextTick } from "vue";
import { useAgent } from "../composables/useAgent";
import { pendingStageLabel } from "../services/analysisTrace";
import Icon from "./Icon.vue";
const { messages, selectedId, pending, pendingStage, select } = useAgent();
const stageText = computed(() => pendingStageLabel(pendingStage.value));
const list = ref<HTMLElement>();
watch(
  () => [messages.value.length, pending.value],
  async () => {
    await nextTick();
    list.value?.scrollTo({ top: list.value.scrollHeight, behavior: "instant" });
  },
);
</script>
<template>
  <div ref="list" class="message-list" aria-label="对话记录">
    <div class="welcome-message">
      <span class="assistant-avatar"><Icon name="sparkles" :size="18" /></span>
      <div>
        <strong>你好，我是 PMC-Agent</strong>
        <p>
          告诉我你想分析的生产目标或物料问题，我会把结论和依据整理在分析结论区。
        </p>
        <span class="welcome-label">直接提问，支持连续追问</span>
      </div>
    </div>
    <div v-for="message in messages" :key="message.id" class="message-pair">
      <div class="user-message">
        <div>{{ message.question }}</div>
        <span>你 · {{ message.requestedAt }}</span>
      </div>
      <button
        class="assistant-message"
        :class="{ 'is-selected': selectedId === message.id }"
        :disabled="pending"
        :aria-pressed="selectedId === message.id"
        @click="select(message.id)"
      >
        <span class="assistant-avatar"
          ><Icon name="sparkles" :size="16"
        /></span>
        <div>
          <div class="message-meta">
            <strong>PMC-Agent</strong
            ><span>{{
              selectedId === message.id ? "当前结果" : "查看结果"
            }}</span>
          </div>
          <p v-if="message.state === 'pending'" class="loading-label">
            {{ stageText }}<span class="loading-dots">···</span>
          </p>
          <p v-else-if="message.state === 'cancelled'">
            已停止等待本次响应；不代表后端计算已停止。
          </p>
          <p v-else-if="message.error">{{ message.error.message }}</p>
          <p v-else>{{ message.result?.answer }}</p>
          <small v-if="message.result?.sources.length"
            ><Icon name="link" :size="12" />来源可查</small
          >
        </div>
      </button>
    </div>
  </div>
</template>
