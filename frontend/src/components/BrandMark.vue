<script setup lang="ts">
withDefaults(defineProps<{ size?: number }>(), { size: 32 });
</script>
<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 32 32"
    fill="none"
    aria-hidden="true"
  >
    <path d="M3 9 16 2l13 7-13 7L3 9Z" fill="currentColor" />
    <path
      d="m3 15 13 7 13-7M3 22l13 7 13-7"
      stroke="currentColor"
      stroke-width="3"
      stroke-linejoin="round"
    />
  </svg>
</template>
