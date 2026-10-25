<script setup lang="ts">
// Adapted from Inspira UI BlurReveal (MIT). One slot group, never individual characters.
import { Motion } from "motion-v";
import { useMotionPreferences } from "../../composables/useMotionPreferences";
const props = withDefaults(
  defineProps<{
    duration?: number;
    delay?: number;
    blur?: number;
    yOffset?: number;
  }>(),
  {
    duration: 0.6,
    delay: 0,
    blur: 0,
    yOffset: 14,
  },
);
const { reduced } = useMotionPreferences();
</script>
<template>
  <Motion
    class="pmc-reveal"
    :initial="
      reduced
        ? false
        : { opacity: 0, filter: `blur(${props.blur}px)`, y: props.yOffset }
    "
    :animate="reduced ? { opacity: 1, filter: 'blur(0px)', y: 0 } : undefined"
    :while-in-view="{ opacity: 1, filter: 'blur(0px)', y: 0 }"
    :in-view-options="{ once: true, amount: 0.08 }"
    :transition="{
      duration: reduced ? 0 : props.duration,
      delay: reduced ? 0 : props.delay,
      ease: [0.22, 1, 0.36, 1],
    }"
    ><slot
  /></Motion>
</template>
