<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import ThemeToggle from "../components/ThemeToggle.vue";
import Icon from "../components/Icon.vue";
import BrandMark from "../components/BrandMark.vue";
import CapabilityCore from "../components/CapabilityCore.vue";
import { useAgent } from "../composables/useAgent";
import { useHomePager } from "../composables/useHomePager";
import "../home.css";
const root = ref<HTMLElement>();
const { paged, active, transitioning, duration, go } = useHomePager(root);
const { draft, pending } = useAgent();
const router = useRouter();
const cards = [
  {
    index: "01",
    icon: "layers",
    title: "生产目标，拆解到每项物料。",
    description: "从生产数量出发，核对 BOM 需求、库存与已知缺口。",
    tag: "生产物料分析",
    prompt: "KQ-Z112 生产 2 片，物料够吗？",
  },
  {
    index: "02",
    icon: "database",
    title: "库存数字，也要有据可查。",
    description: "查看已发布库存，区分可用数量与缺失记录。",
    tag: "库存查询",
    prompt: "查询 KQ-BHX3588M 的库存",
  },
  {
    index: "03",
    icon: "link",
    title: "看清结论，也看清依据。",
    description: "沿着分析结果追问，查看物料记录与来源。",
    tag: "来源追溯",
    prompt: "查看 KQ-Z112 的 BOM 和来源",
  },
];
function openQuestion(question: string) {
  if (pending.value) return;
  draft.value = question;
  void router.push("/workspace");
}
function focusMain() {
  document.getElementById("main-content")?.focus();
}
</script>
<template>
  <div
    ref="root"
    class="landing-page"
    :class="{ 'is-paged': paged, 'is-switching': transitioning }"
    :data-page="active + 1"
    :data-layout="paged ? 'paged' : 'natural'"
  >
    <a class="skip-link" href="#main-content" @click.prevent="focusMain"
      >跳转到主要内容</a
    >
    <header class="landing-header">
      <RouterLink to="/" class="landing-brand" aria-label="PMC-Agent 首页"
        ><BrandMark :size="30" /><span
          >PMC-Agent<small>制造业智能计划助手</small></span
        ></RouterLink
      >
      <nav aria-label="首页导航">
        <button class="landing-nav-link" @click="go(1, true)">应用场景</button
        ><RouterLink to="/workspace" class="landing-console"
          >进入工作台<Icon name="arrow" :size="17"
        /></RouterLink>
        <ThemeToggle />
      </nav>
    </header>
    <main id="main-content" class="home-viewport" tabindex="-1">
      <div
        class="home-track"
        :style="{
          transform: paged ? `translate3d(0, -${active * 100}%, 0)` : 'none',
          '--page-duration': `${duration}ms`,
        }"
      >
        <section
          id="home-overview"
          class="home-screen overview-screen"
          :class="{ 'is-current': active === 0 }"
          :inert="paged && active !== 0"
          :aria-hidden="paged && active !== 0 ? true : undefined"
          aria-labelledby="landing-title"
        >
          <div class="screen-content landing-hero">
            <div class="landing-copy">
              <div class="landing-eyebrow">
                <span></span>MANUFACTURING INTELLIGENCE
              </div>
              <h1 id="landing-title">
                让生产计划，<br /><em>每一步都有依据。</em>
              </h1>
              <p class="landing-description">
                从一个问题出发，连接物料、库存与计划。<br />用自然语言查看结论、明细与来源。
              </p>
              <div class="landing-actions">
                <RouterLink to="/workspace" class="landing-primary"
                  >开始分析<Icon name="arrow" :size="19" /></RouterLink
                ><button class="landing-secondary" @click="go(1, true)">
                  了解应用场景<Icon name="down" :size="17" />
                </button>
              </div>
              <p class="landing-mode">
                <span></span>基于已发布业务数据<small
                  >自然语言提问 · 结论可追溯</small
                >
              </p>
            </div>
            <div class="hero-visual-reveal">
              <CapabilityCore :active="!paged || active === 0" />
            </div>
          </div>
          <button class="natural-explore" @click="go(1, true)">
            探索应用场景<Icon name="down" :size="16" />
          </button>
        </section>
        <section
          id="home-scenarios"
          class="home-screen scenarios-screen"
          :class="{ 'is-current': active === 1 }"
          :inert="paged && active !== 1"
          :aria-hidden="paged && active !== 1 ? true : undefined"
          aria-labelledby="scenarios-heading"
        >
          <div class="screen-content scenario-content">
            <div class="landing-section-heading">
              <div>
                <span class="landing-eyebrow">START WITH A QUESTION</span>
                <h2 id="scenarios-heading">从一个具体问题开始</h2>
              </div>
              <p>从提问到依据，连接真实业务数据。</p>
            </div>
            <div class="landing-cards">
              <article
                v-for="card in cards"
                :key="card.index"
                class="landing-card"
              >
                <div class="landing-card-top">
                  <Icon :name="card.icon" :size="23" /><span>{{
                    card.index
                  }}</span>
                </div>
                <span class="landing-card-tag">{{ card.tag }}</span>
                <h3>{{ card.title }}</h3>
                <p>{{ card.description }}</p>
                <button
                  class="scenario-question"
                  :disabled="pending"
                  @click="openQuestion(card.prompt)"
                >
                  <span>{{ card.prompt }}</span
                  ><Icon name="arrow" :size="17" />
                </button>
              </article>
            </div>
            <div class="landing-principles">
              <span><b>01</b>自然语言提问</span
              ><Icon name="arrow" :size="16" /><span
                ><b>02</b>结论与物料明细</span
              ><Icon name="arrow" :size="16" /><span
                ><b>03</b>来源与适用条件</span
              ><small>面向 PMC 计划员 · 生产主管 · 采购人员</small>
            </div>
            <div class="landing-boundary">
              <Icon name="info" :size="16" />
              <p>
                说出产品型号即可开始；点击问题将填入草稿，发送后查询业务数据。缺失的事实会明确提示。
              </p>
            </div>
            <footer class="landing-footer">
              <span>PMC-Agent<small> / 让依据连接计划</small></span
              ><span>查询业务事实，让决策有据可循</span>
            </footer>
          </div>
        </section>
      </div>
    </main>
    <nav class="home-pagination" aria-label="首页分页">
      <button
        v-for="(label, index) in ['产品概览', '应用场景']"
        :key="index"
        :aria-label="`${index + 1 === 1 ? '01' : '02'} ${label}`"
        :aria-current="active === index ? 'step' : undefined"
        :aria-controls="index === 0 ? 'home-overview' : 'home-scenarios'"
        @click="go(index, true)"
      >
        <span>0{{ index + 1 }}</span
        ><span>{{ label }}</span>
      </button>
    </nav>
    <div class="home-dock">
      <span class="page-position">0{{ active + 1 }} <i>/</i> 02</span
      ><button @click="go(active === 0 ? 1 : 0, true)">
        {{ active === 0 ? "探索应用场景" : "返回产品概览"
        }}<Icon
          name="down"
          :size="16"
          :class="{ 'point-up': active === 1 }"
        /></button
      ><span class="dock-hint">滚动探索 · ↑ ↓ 翻页</span>
    </div>
    <p class="sr-only" aria-live="polite">
      {{ paged ? `当前第 ${active + 1} 屏，共 2 屏` : "自然滚动布局" }}
    </p>
  </div>
</template>
