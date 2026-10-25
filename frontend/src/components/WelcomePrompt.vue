<script setup lang="ts">
import Icon from "./Icon.vue";
import BrandMark from "./BrandMark.vue";
import QueryComposer from "./QueryComposer.vue";
import BlurReveal from "./motion/BlurReveal.vue";
import { useAgent } from "../composables/useAgent";
const { draft } = useAgent();
function fill(question: string) {
  draft.value = question;
  document.getElementById("query-input")?.focus();
}
</script>
<template>
  <section class="welcome-workspace" aria-labelledby="welcome-heading">
    <BlurReveal :y-offset="10" :duration="0.45">
      <div class="welcome-brand"><BrandMark :size="29" /></div>
      <p class="welcome-eyebrow">PMC-AGENT / 智能计划助手</p>
      <h1 id="welcome-heading">把生产问题，直接交给我。</h1>
      <p class="welcome-description">
        说出产品与生产目标，一起核对物料、库存和缺口。
      </p>
    </BlurReveal>
    <BlurReveal class="welcome-input-area" :delay="0.08" :y-offset="8">
      <QueryComposer class="welcome-composer" />
      <div class="conversation-starter">
        <span>不知道从哪里开始？</span
        ><button @click="fill('有哪些产品可以查询？')">
          问问有哪些产品<Icon name="arrow" :size="14" />
        </button>
      </div>
    </BlurReveal>
    <p class="welcome-footnote">
      <Icon name="shield" :size="14" />连接业务数据 · 结论有来源 · 对话可追溯
    </p>
  </section>
</template>
