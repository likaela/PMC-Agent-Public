<script setup lang="ts">
withDefaults(defineProps<{ tone?: string }>(), { tone: "neutral" });
</script>
<template>
  <span class="badge" :class="`badge--${tone}`"
    ><span class="badge-dot"></span><slot
  /></span>
</template>
