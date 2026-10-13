<script setup lang="ts">
withDefaults(defineProps<{ name: string; size?: number }>(), { size: 20 });
const paths: Record<string, string> = {
  cube: "m12 3 9 5v9l-9 5-9-5V8zm0 0v9m-9-4 9 4 9-4m-9 4v10",
  home: "M3 3h7v7H3zm11 0h7v7h-7zM3 14h7v7H3zm11 0h7v7h-7z",
  sparkles:
    "m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4zm7-2v4m-2-2h4",
  arrow: "M4 12h16m-6-6 6 6-6 6",
  chevron: "m9 5 7 7-7 7",
  down: "m6 9 6 6 6-6",
  box: "m3 7 9-4 9 4v11l-9 4-9-4zm0 0 9 4 9-4m-9 4v11M7 5l10 4",
  chat: "M21 11a8 8 0 0 1-8 8H6l-4 3V7a5 5 0 0 1 5-5h6a8 8 0 0 1 8 9ZM7 8h8M7 12h5",
  database:
    "M20 5c0 2-4 3-8 3S4 7 4 5s4-3 8-3 8 1 8 3Zm0 0v14c0 2-4 3-8 3s-8-1-8-3V5m0 7c0 2 4 3 8 3s8-1 8-3",
  check: "m5 12 4 4L19 6",
  checkcircle: "m8 12 3 3 5-6M22 12A10 10 0 1 1 2 12a10 10 0 0 1 20 0",
  alert: "m12 3 10 18H2zm0 5v6m0 3v1",
  info: "M22 12A10 10 0 1 1 2 12a10 10 0 0 1 20 0M12 11v6m0-10v1",
  file: "M14 2H5v20h14V7zm0 0v5h5M8 12h8m-8 4h6",
  link: "m10 14 4-4M8 16l-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m2 1 2-2a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0",
  clock: "M22 12A10 10 0 1 1 2 12a10 10 0 0 1 20 0m-10-6v6l4 3",
  help: "M22 12A10 10 0 1 1 2 12a10 10 0 0 1 20 0M9 8a3 3 0 0 1 6 1c0 2-3 2-3 5m0 3v1",
  send: "m22 2-7 20-4-9-9-4zm0 0L11 13",
  menu: "M3 6h18M3 12h18M3 18h18",
  close: "m6 6 12 12M6 18 18 6",
  retry: "M3 10a9 9 0 1 1 1 8M3 3v7h7",
  plus: "M12 4v16M4 12h16",
  search: "M19 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0m-2 6 5 5",
  layers: "m12 3 10 5-10 5L2 8zm-10 9 10 5 10-5M2 17l10 5 10-5",
  shield: "m12 2 9 4v6c0 6-9 10-9 10S3 18 3 12V6zm-4 9 3 3 5-6",
  cart: "M2 3h3l3 12h11l3-9H6m3 13h1v1H9zm9 0h1v1h-1z",
  stop: "M6 6h12v12H6z",
  filter: "M4 5h16l-6 7v7l-4 2v-9z",
};
</script>
<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.65"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name] || paths.box" />
  </svg>
</template>
