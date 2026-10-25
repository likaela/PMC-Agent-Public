import { onMounted, onUnmounted, ref } from "vue";

export function useMotionPreferences() {
  const reduced = ref(
    typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const finePointer = ref(false);
  const visible = ref(true);
  let reduceQuery: MediaQueryList;
  let pointerQuery: MediaQueryList;
  const sync = () => {
    reduced.value = reduceQuery.matches;
    finePointer.value = pointerQuery.matches;
    visible.value = !document.hidden;
  };
  onMounted(() => {
    reduceQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    pointerQuery = window.matchMedia("(hover: hover) and (pointer: fine)");
    sync();
    reduceQuery.addEventListener("change", sync);
    pointerQuery.addEventListener("change", sync);
    document.addEventListener("visibilitychange", sync);
  });
  onUnmounted(() => {
    reduceQuery?.removeEventListener("change", sync);
    pointerQuery?.removeEventListener("change", sync);
    document.removeEventListener("visibilitychange", sync);
  });
  return { reduced, finePointer, visible };
}
