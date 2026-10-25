<script setup lang="ts">
import { ref, watch, nextTick, onMounted, onUnmounted } from "vue";
import ThemeToggle from "./ThemeToggle.vue";
import BrandMark from "./BrandMark.vue";
import { useRoute } from "vue-router";
import Icon from "./Icon.vue";
import { useAgent } from "../composables/useAgent";
const route = useRoute();
const {
  pending,
  connecting,
  ready,
  connectionError,
  session,
  recentSessions,
  reset,
  restore,
  initialize,
  clearHistory,
} = useAgent();
const menuOpen = ref(false);
const help = ref<HTMLDialogElement>();
const mobile = ref(false);
const navigation = ref<HTMLElement>();
const toggle = ref<HTMLButtonElement>();
let breakpoint: MediaQueryList | undefined;
const syncWidth = () => {
  mobile.value = breakpoint?.matches ?? false;
  if (!mobile.value) menuOpen.value = false;
};
onMounted(() => {
  void initialize();
  breakpoint = window.matchMedia("(max-width: 760px)");
  syncWidth();
  breakpoint.addEventListener("change", syncWidth);
});
onUnmounted(() => breakpoint?.removeEventListener("change", syncWidth));
function navigationItems() {
  return [
    ...(navigation.value?.querySelectorAll<HTMLElement>(
      "a, button:not(:disabled)",
    ) ?? []),
  ].filter((item) => item.getClientRects().length > 0);
}
watch(menuOpen, async (open) => {
  await nextTick();
  if (open) navigationItems()[0]?.focus();
  else if (mobile.value) toggle.value?.focus();
});
function trapNavigation(event: KeyboardEvent) {
  if (!mobile.value || !menuOpen.value || event.key !== "Tab") return;
  const items = navigationItems();
  const first = items[0];
  const last = items.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last?.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first?.focus();
  }
}
function focusMain() {
  document.getElementById("main-content")?.focus();
}
watch(
  () => route.path,
  () => {
    menuOpen.value = false;
  },
);
</script>
<template>
  <a class="skip-link" href="#main-content" @click.prevent="focusMain"
    >跳转到主要内容</a
  >
  <div class="app-layout" @keydown.esc="menuOpen = false">
    <header class="mobile-header">
      <RouterLink to="/" class="brand"
        ><span class="brand-symbol"><BrandMark :size="26" /></span
        ><strong>PMC-Agent</strong></RouterLink
      >
      <button
        ref="toggle"
        class="icon-button"
        :aria-expanded="menuOpen"
        aria-controls="sidebar"
        :aria-label="menuOpen ? '关闭导航' : '打开导航'"
        @click="menuOpen = !menuOpen"
      >
        <Icon :name="menuOpen ? 'close' : 'menu'" />
      </button>
    </header>
    <button
      v-if="menuOpen"
      class="nav-backdrop"
      aria-label="关闭导航遮罩"
      @click="menuOpen = false"
    ></button>
    <aside
      id="sidebar"
      ref="navigation"
      class="sidebar"
      :class="{ 'is-open': menuOpen }"
      :inert="mobile && !menuOpen"
      @keydown="trapNavigation"
    >
      <RouterLink to="/" class="brand desktop-brand"
        ><span class="brand-symbol"><BrandMark :size="28" /></span
        ><span
          ><strong>PMC-Agent</strong><small>制造业智能计划助手</small></span
        ></RouterLink
      >
      <div class="nav-label">工作空间 <span>WORKSPACE</span></div>
      <nav aria-label="主导航">
        <RouterLink to="/" class="nav-item" exact-active-class="is-active"
          ><Icon name="home" /><span>首页</span></RouterLink
        >
        <RouterLink to="/workspace" class="nav-item" active-class="is-active"
          ><Icon name="sparkles" /><span>分析工作台</span></RouterLink
        >
      </nav>
      <section class="recent-conversations" aria-label="最近对话">
        <div class="recent-heading">
          <span>最近对话</span
          ><button
            class="icon-button"
            aria-label="新建对话"
            :disabled="pending || connecting"
            @click="
              reset();
              menuOpen = false;
            "
          >
            <Icon name="plus" :size="16" />
          </button>
        </div>
        <p v-if="!recentSessions.length" class="recent-empty">
          开始提问后，对话会保存在这里。
        </p>
        <div v-else class="recent-list">
          <button
            v-for="(item, index) in recentSessions"
            :key="item.session_id"
            :class="{ 'is-current': session?.session_id === item.session_id }"
            :aria-current="
              session?.session_id === item.session_id ? 'true' : undefined
            "
            :disabled="pending || connecting"
            @click="
              restore(item);
              menuOpen = false;
            "
          >
            <Icon name="chat" :size="15" /><span
              ><strong>{{ item.product_code }}</strong
              ><small>分析对话 · {{ item.session_id.slice(-6) }}</small></span
            ><span v-if="index === 0" class="recent-new">最近</span>
          </button>
        </div>
        <div class="history-footer">
          <small class="history-scope">仅显示此浏览器记住的对话</small>
          <button
            v-if="recentSessions.length"
            class="text-button"
            :disabled="pending || connecting"
            @click="
              clearHistory();
              menuOpen = false;
            "
          >
            清空历史
          </button>
        </div>
      </section>
      <div class="sidebar-bottom">
        <div class="sidebar-note">
          <span class="small-icon"><Icon name="shield" :size="18" /></span
          ><strong>每个结论，有据可循</strong>
          <p>查看物料明细与来源，<br />让计划更清晰。</p>
        </div>
        <button
          class="nav-item help-button"
          @click="
            help?.showModal();
            menuOpen = false;
          "
        >
          <Icon name="help" /><span>使用说明</span
          ><Icon name="chevron" :size="16" />
        </button>
        <div class="sidebar-footer">
          <span class="status-dot"></span>PMC 工作台 <span>v0.2</span>
        </div>
      </div>
    </aside>
    <div class="main-shell" :inert="mobile && menuOpen">
      <div class="topbar">
        <div class="breadcrumb">
          工作空间 <Icon name="chevron" :size="14" /><span>{{
            route.path === "/" ? "首页" : "分析工作台"
          }}</span>
        </div>
        <div class="topbar-actions">
          <span class="connection-label" role="status"
            ><span
              :class="{
                'status-dot': true,
                'is-unavailable': !!connectionError,
              }"
            ></span
            >{{
              connecting
                ? "连接中"
                : connectionError
                  ? "连接待恢复"
                  : ready
                    ? "业务数据已连接"
                    : "连接业务数据"
            }}</span
          >
          <ThemeToggle />
        </div>
      </div>
      <main id="main-content" tabindex="-1">
        <slot />
      </main>
      <footer class="page-footer">
        <span>PMC-Agent</span><span>清晰的计划，始于可信的数据。</span>
      </footer>
    </div>
    <dialog
      ref="help"
      class="help-dialog"
      aria-labelledby="help-title"
      @click="
        (event) => {
          if (event.target === help) help?.close();
        }
      "
    >
      <div class="card-heading">
        <div>
          <span class="eyebrow">QUICK GUIDE</span>
          <h2 id="help-title">开始你的第一次分析</h2>
        </div>
        <button
          class="icon-button"
          aria-label="关闭使用说明"
          @click="help?.close()"
        >
          <Icon name="close" />
        </button>
      </div>
      <div class="dialog-theme-controls">
        <span>外观主题</span><ThemeToggle />
      </div>
      <ol class="help-steps">
        <li>
          <strong>直接描述你的问题</strong>
          <p>
            输入产品型号、数量与问题，例如“KQ-Z112 生产 2
            片，物料够吗？”；不知道型号时，可以问“有哪些产品可以查询？”。
          </p>
        </li>
        <li>
          <strong>阅读结论与物料明细</strong>
          <p>
            可以连续追问，也可以直接说出另一个产品。点击历史回答，可切换到对应的结果、警告和来源。
          </p>
        </li>
        <li>
          <strong>核对来源和适用条件</strong>
          <p>“未提供”不等于 0。库存满足不代表产能、交期或正式生产放行。</p>
        </li>
      </ol>
      <div class="soft-note">
        <Icon name="info" />
        <p>
          具体能力以已发布业务数据和返回结果为准。切换产品会独立保存对话；最近对话只包含本浏览器记住的会话。
        </p>
      </div>
      <button class="primary-button full-width" @click="help?.close()">
        我知道了<Icon name="check" :size="18" />
      </button>
    </dialog>
  </div>
</template>
