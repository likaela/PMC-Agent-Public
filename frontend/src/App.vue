<script setup lang="ts">
import AppShell from "./components/AppShell.vue";
</script>
<template>
  <RouterView v-slot="{ Component, route }">
    <div :key="route.path" class="route-page">
      <component :is="Component" v-if="route.path === '/'" />
      <AppShell v-else><component :is="Component" /></AppShell>
    </div>
  </RouterView>
</template>
