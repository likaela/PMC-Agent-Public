<script setup lang="ts">
import { ref, watch, nextTick, onBeforeUnmount } from "vue";
import ThemeToggle from "./ThemeToggle.vue";
import Icon from "./Icon.vue";
import StatusBadge from "./StatusBadge.vue";
import type { AgentResult } from "../data/model";
import { embeddedScrollRoot } from "../interactions/pageScroll";
const props = defineProps<{ result?: AgentResult }>();
const dialog = ref<HTMLDialogElement>();
const closeButton = ref<HTMLButtonElement>();
const open = ref(false);
const highlighted = ref<string[]>([]);
let returnFocus: HTMLElement | null = null;
let previousOverflow: string | undefined;
let scrollRoot: HTMLElement | undefined;
function finishClose() {
  dialog.value?.close();
  if (scrollRoot && previousOverflow !== undefined)
    scrollRoot.style.overflow = previousOverflow;
  previousOverflow = undefined;
  scrollRoot = undefined;
  if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
  returnFocus = null;
}
function close() {
  open.value = false;
}
function trapTab(event: KeyboardEvent) {
  const targets = [
    ...(dialog.value?.querySelectorAll<HTMLElement>(
      'button, a[href], summary, [tabindex="0"]',
    ) ?? []),
  ].filter((element) => element.getClientRects().length > 0);
  const first = targets[0],
    last = targets[targets.length - 1];
  if (!first || !last) return;
  const current = document.activeElement;
  if (
    event.shiftKey &&
    (current === first || !dialog.value?.contains(current))
  ) {
    event.preventDefault();
    last.focus();
  } else if (
    !event.shiftKey &&
    (current === last || !dialog.value?.contains(current))
  ) {
    event.preventDefault();
    first.focus();
  }
}
watch(
  () => props.result,
  () => {
    close();
    finishClose();
    highlighted.value = [];
  },
);
onBeforeUnmount(finishClose);
async function reveal(ids: string[] = []) {
  highlighted.value = ids;
  if (open.value) return;
  returnFocus =
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
  scrollRoot = embeddedScrollRoot() ?? document.body;
  previousOverflow = scrollRoot.style.overflow;
  scrollRoot.style.overflow = "hidden";
  dialog.value?.showModal();
  open.value = true;
  await nextTick();
  closeButton.value?.focus({ preventScroll: true });
}
defineExpose({ reveal });
</script>
<template>
  <section
    class="card sources-card"
    tabindex="-1"
    aria-label="数据来源与适用条件"
  >
    <button
      class="source-toggle"
      :aria-expanded="open"
      aria-controls="source-details"
      aria-haspopup="dialog"
      @click="reveal()"
    >
      <span class="source-title"
        ><span class="source-icon"><Icon name="shield" :size="21" /></span
        ><span
          ><strong>数据来源与适用条件</strong
          ><small>{{
            result
              ? `${result.sources.length} 项分析来源 · 业务数据`
              : "结论的依据，与结论一样重要"
          }}</small></span
        ></span
      ><span class="source-toggle-label"
        >{{ open ? "收起详情" : "展开详情"
        }}<Icon name="down" :size="17" :class="{ rotated: open }"
      /></span>
    </button>
    <Teleport to="body">
      <dialog
        ref="dialog"
        class="source-drawer"
        aria-labelledby="drawer-title"
        @cancel.prevent="close"
        @keydown.tab="trapTab"
        @click="$event.target === dialog && close()"
      >
        <Transition name="drawer" @after-leave="finishClose">
          <div v-if="open" class="source-drawer-panel">
            <header class="drawer-header">
              <div>
                <p class="eyebrow">EVIDENCE &amp; CONTEXT</p>
                <h2 id="drawer-title">数据来源与适用条件</h2>
                <p>查看结论依据，核对数据边界。</p>
              </div>
              <button
                ref="closeButton"
                class="icon-button"
                aria-label="关闭来源详情"
                @click="close"
              >
                <Icon name="close" :size="20" />
              </button>
            </header>
            <div
              id="source-details"
              class="source-details"
              tabindex="0"
              role="region"
              aria-label="来源记录"
            >
              <p class="source-disclaimer">
                <Icon
                  name="info"
                  :size="16"
                />以下展示本次回答引用的数据来源；未提供的字段如实标明。
              </p>
              <div v-if="!result?.sources.length" class="source-no-data">
                本次尚无来源可展示。
              </div>
              <article
                v-for="source in result?.sources"
                :key="source.id"
                class="source-record"
                :class="{ highlighted: highlighted.includes(source.id) }"
              >
                <div class="source-record-title">
                  <Icon name="file" :size="18" /><strong>{{
                    source.title
                  }}</strong
                  ><StatusBadge tone="blue">{{ source.kind }}</StatusBadge>
                </div>
                <dl>
                  <div>
                    <dt>引用范围</dt>
                    <dd>{{ source.scope }}</dd>
                  </div>
                  <div>
                    <dt>来源文件 / 记录</dt>
                    <dd>{{ source.reference }}</dd>
                  </div>
                  <div>
                    <dt>原始记录 ID</dt>
                    <dd>
                      {{
                        source.recordId ?? "未提供（展示标识不等同于业务记录）"
                      }}
                    </dd>
                  </div>
                  <div>
                    <dt>数据版本</dt>
                    <dd>{{ source.version ?? "未提供" }}</dd>
                  </div>
                  <div>
                    <dt>来源发布时间</dt>
                    <dd>{{ source.publishedAt ?? "未提供" }}</dd>
                  </div>
                  <div class="full-row">
                    <dt>适用条件</dt>
                    <dd>{{ source.conditions }}</dd>
                  </div>
                </dl>
              </article>
              <details v-if="result?.backend" class="backend-audit">
                <summary>原始响应、警告与审计证据</summary>
                <pre>{{ JSON.stringify(result.backend.raw, null, 2) }}</pre>
              </details>
            </div>
            <footer class="drawer-footer">
              <div class="drawer-footnote">
                <Icon name="shield" :size="16" />请核对来源与适用条件后使用结论
              </div>
              <ThemeToggle />
            </footer>
          </div>
        </Transition>
      </dialog>
    </Teleport>
  </section>
</template>
