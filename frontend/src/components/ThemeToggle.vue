<script setup lang="ts">
import Icon from "./Icon.vue";
import { useTheme } from "../composables/useTheme";
const { theme, setTheme } = useTheme();
</script>
<template>
  <div class="theme-toggle" role="group" aria-label="外观主题">
    <button
      type="button"
      :aria-pressed="theme === 'light'"
      aria-label="浅色主题"
      @click="setTheme('light')"
    >
      <Icon name="sun" :size="15" /><span>浅色</span>
    </button>
    <button
      type="button"
      :aria-pressed="theme === 'dark'"
      aria-label="深色主题"
      @click="setTheme('dark')"
    >
      <Icon name="moon" :size="15" /><span>深色</span>
    </button>
  </div>
</template>
