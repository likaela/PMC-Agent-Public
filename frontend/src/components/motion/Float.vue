<script setup lang="ts">
// Adapted from Inspira UI Float (MIT). See docs/motion-2026-10-06/third-party.md.
import { Motion, useAnimationFrame, useMotionValue } from "motion-v";
import { watch } from "vue";
import { useMotionPreferences } from "../../composables/useMotionPreferences";
const props = withDefaults(
  defineProps<{ paused?: boolean; amplitude?: number; speed?: number }>(),
  {
    paused: false,
    amplitude: 9,
    speed: 0.8,
  },
);
const { reduced, visible } = useMotionPreferences();
const y = useMotionValue(0);
const rotateZ = useMotionValue(0);
let time = 0;
// Delta time avoids making motion faster on high refresh-rate screens.
useAnimationFrame((_elapsed, delta) => {
  if (props.paused || reduced.value || !visible.value) return;
  time += (Math.min(delta, 64) / 1000) * props.speed;
  y.set(Math.sin(time * 0.8) * props.amplitude);
  rotateZ.set(Math.sin(time * 0.4) * 0.6);
});
watch(reduced, (value) => {
  if (value) {
    y.set(0);
    rotateZ.set(0);
  }
});
</script>
<template>
  <Motion
    class="pmc-float"
    :data-motion="reduced ? 'reduced' : paused ? 'paused' : 'running'"
    :style="{ y, rotateZ }"
  >
    <slot />
  </Motion>
</template>
