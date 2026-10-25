<script setup lang="ts">
import Icon from "./Icon.vue";
defineProps<{ warnings: string[] }>();
</script>
<template>
  <aside v-if="warnings.length" class="warning-list" aria-label="结果警告">
    <h4><Icon name="alert" :size="16" />使用前请留意</h4>
    <ul>
      <li v-for="warning in warnings" :key="warning">{{ warning }}</li>
    </ul>
  </aside>
</template>
