<script setup lang="ts">
import Icon from "./Icon.vue";
import { useAgent } from "../composables/useAgent";
const {
  product,
  scope,
  session,
  connecting,
  connectionError,
  needsSync,
  hasMore,
  pending,
  ready,
  initialize,
  loadHistory,
  reset,
} = useAgent();
async function reconnect() {
  await initialize(true);
  if (session.value && !connectionError.value) await loadHistory();
}
</script>
<template>
  <div
    v-if="connecting || connectionError || needsSync || hasMore || session"
    class="conversation-context"
  >
    <div class="context-line">
      <span v-if="product" class="context-product"
        ><Icon name="layers" :size="15" />{{ product.product_code
        }}<small>{{ scope?.label }}</small></span
      >
      <span v-if="connecting" role="status" class="backend-note">{{
        ready ? "正在同步对话…" : "正在连接业务数据…"
      }}</span>
      <span
        v-else-if="!connectionError && !needsSync && !hasMore && session"
        class="context-hint"
        >可以接着问，也可以直接提到另一个产品</span
      >
      <button
        v-if="session && !connectionError && !needsSync && !hasMore"
        class="text-action"
        :disabled="pending || connecting"
        @click="loadHistory()"
      >
        <Icon name="retry" :size="14" />同步对话
      </button>
    </div>
    <div
      v-if="connectionError || needsSync || hasMore"
      class="connection-recovery"
      role="status"
    >
      <Icon name="info" :size="17" />
      <div>
        <p>
          {{
            connectionError?.message ||
            (hasMore
              ? "还有历史消息，加载完成后即可继续提问。"
              : "请同步对话后继续，避免重复提交。")
          }}
        </p>
        <small v-if="connectionError"
          >{{ connectionError.backendCode }}
          {{ connectionError.httpStatus }}</small
        >
      </div>
      <button
        v-if="hasMore && !connectionError"
        class="secondary-button"
        :disabled="connecting"
        @click="loadHistory(true)"
      >
        加载更多消息
      </button>
      <button
        v-else
        class="secondary-button"
        :disabled="connecting || pending"
        @click="reconnect"
      >
        {{ session ? "同步对话" : "重新连接" }}
      </button>
      <button
        v-if="
          needsSync || connectionError?.backendCode === 'ARCHIVED_CONFIGURATION'
        "
        class="text-action"
        :disabled="connecting || pending"
        @click="reset"
      >
        新建对话
      </button>
    </div>
  </div>
</template>
