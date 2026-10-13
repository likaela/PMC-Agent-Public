<script setup lang="ts">
import { useRouter } from "vue-router";
import { computed, ref } from "vue";
import Icon from "../components/Icon.vue";
import StatusBadge from "../components/StatusBadge.vue";
import { useAgent } from "../composables/useAgent";
import { demoScenarios, DEMO_SCENARIO_ID } from "../data/demoScenarios";
const { startEnterpriseAnalysis, setScenario } = useAgent();
const router = useRouter();
const showDemo = ref(false);
const enterpriseCards = [
  {
    id: "enterprise-production",
    icon: "layers",
    title: "BOM与生产需求查询",
    description: "查询已发布产品和物料组成，按生产数量计算BOM需求。",
    prompt: "输入产品编码和生产数量",
    tag: "企业已发布事实",
  },
  {
    id: "enterprise-inventory",
    icon: "database",
    title: "Excel库存记录查询",
    description: "查看主库存表的数量、库位、批次及来源；无记录保持未知。",
    prompt: "输入物料编码查询库存",
    tag: "未知数据不作估算",
  },
  {
    id: "enterprise-purchase",
    icon: "cart",
    title: "连续追问与来源查询",
    description: "保持当前产品上下文，查询材料和来源，查看历史回答。",
    prompt: "输入产品编码或先问有哪些产品",
    tag: "历史回答不冒充最新库存",
  },
];
const cards = computed(() =>
  showDemo.value
    ? demoScenarios.map((s, index) => ({
        ...s,
        index: `0${index + 1}`,
        tag: "演示产品",
      }))
    : enterpriseCards.map((s, index) => ({ ...s, index: `0${index + 1}` })),
);
function openScenario(id: string) {
  if (id.startsWith("enterprise-")) {
    startEnterpriseAnalysis();
    router.push("/workspace");
    return;
  }
  setScenario(DEMO_SCENARIO_ID);
  router.push({ path: "/workspace", query: { scenario: id } });
}
</script>
<template>
  <div class="home-page">
    <section class="hero card">
      <div class="hero-content">
        <div class="eyebrow">
          <span class="tiny-line"></span>从问题到计划，让依据更清楚
        </div>
        <h1>复杂的物料问题，<br />从这里<span>理清思路。</span></h1>
        <p>
          你的制造业 PMC 智能计划助手。提出问题，读懂分析结论，<br
            class="desktop-only"
          />在同一个工作台查看物料明细与数据来源。
        </p>
        <RouterLink
          class="primary-button hero-button"
          to="/workspace"
          @click="startEnterpriseAnalysis"
          >开始分析<Icon name="arrow" :size="19"
        /></RouterLink>
        <div class="hero-foot">
          <Icon name="checkcircle" :size="15" />结论与来源一起呈现<span
          ></span>保留每一次分析上下文
        </div>
      </div>
      <div class="hero-visual" aria-hidden="true">
        <div class="visual-grid"></div>
        <div class="visual-orbit orbit-one"></div>
        <div class="visual-orbit orbit-two"></div>
        <div class="floating-tag tag-top">
          <span class="tag-icon"><Icon name="layers" :size="19" /></span>
          <div>
            <small>企业分析入口</small>
            <strong>输入产品编码 <em>描述生产需求</em></strong>
          </div>
        </div>
        <div class="visual-cube"><Icon name="cube" :size="84" /></div>
        <div class="floating-tag tag-bottom">
          <span class="tag-icon orange"><Icon name="alert" :size="19" /></span>
          <div>
            <small>企业事实 · 结果可追溯</small>
            <strong>查询后展示 <em>结果与来源</em></strong>
          </div>
          <span class="mini-line"></span>
        </div>
        <span class="visual-caption">连接问题、结论与依据</span>
      </div>
    </section>
    <div class="section-heading">
      <div>
        <h2>从一个具体问题开始</h2>
        <p>先提供产品或物料编码，再用自然语言描述需求</p>
      </div>
      <span class="subtle-label">{{
        showDemo
          ? "演示产品 · 独立数据空间"
          : "企业分析 · 不预设产品与结果"
      }}</span>
    </div>
    <div class="section-heading">
      <button class="text-button" @click="showDemo = !showDemo">
        {{ showDemo ? "返回产品分析入口" : "体验演示产品" }}
      </button>
    </div>
    <section
      class="scenario-cards"
      :aria-label="!showDemo ? '企业分析入口' : '演示场景'"
    >
      <article v-for="card in cards" :key="card.id" class="scenario-card card">
        <div class="scenario-card-top">
          <span class="scene-icon"><Icon :name="card.icon" :size="24" /></span
          ><span class="scene-index">{{ card.index }}</span>
        </div>
        <h3>{{ card.title }}</h3>
        <p>{{ card.description }}</p>
        <span class="scenario-label">{{ card.tag }}</span
        ><button class="scenario-question" @click="openScenario(card.id)">
          <span>{{ card.prompt }}</span
          ><Icon name="arrow" :size="18" />
        </button>
      </article>
    </section>
    <section class="home-bottom">
      <div class="card workflow-card">
        <div class="card-heading">
          <h2>一条清晰的分析路径</h2>
          <Icon name="sparkles" :size="19" />
        </div>
        <div class="workflow">
          <div>
            <span>1</span><strong>描述目标</strong>
            <p>产品、数量与适用条件</p>
          </div>
          <Icon name="chevron" :size="16" />
          <div>
            <span>2</span><strong>阅读结论</strong>
            <p>先看结论，再看缺口</p>
          </div>
          <Icon name="chevron" :size="16" />
          <div>
            <span>3</span><strong>核对依据</strong>
            <p>来源、时效与边界</p>
          </div>
        </div>
      </div>
      <div class="card boundary-card">
        <div class="card-heading">
          <h2>当前能力边界</h2>
          <StatusBadge tone="blue">事实查询</StatusBadge>
        </div>
        <p>
          查询产品、BOM和库存记录，计算生产需求并核对数量。在标准分析范围内查看最大满足数量与限制物料；
          通过演示产品体验齐套与采购建议。未知数量不估算，结果仅适用于对应分析范围，不代替生产放行。
        </p>
        <div class="boundary-note">
          <Icon name="shield" :size="16" />演示结果不构成真实生产或采购决策
        </div>
      </div>
    </section>
  </div>
</template>
