import {
  computed,
  nextTick,
  onMounted,
  onBeforeUnmount,
  ref,
  type Ref,
} from "vue";
import { createWheelGate } from "../interactions/wheelGate";
import { pageScrollTarget } from "../interactions/pageScroll";
import { useMotionPreferences } from "./useMotionPreferences";

export function useHomePager(root: Ref<HTMLElement | undefined>) {
  const { reduced } = useMotionPreferences();
  const paged = ref(false);
  const active = ref(0);
  const transitioning = ref(false);
  const gate = createWheelGate();
  const duration = computed(() => (reduced.value ? 0 : 600));
  let observer: ResizeObserver | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let frame = 0;
  let disposed = false;
  let scrollTarget: HTMLElement | Window;
  const panels = () => [
    ...(root.value?.querySelectorAll<HTMLElement>(".home-screen") ?? []),
  ];
  function measure() {
    frame = 0;
    if (!root.value || disposed) return;
    const header = root.value.querySelector<HTMLElement>(".landing-header");
    const dock = root.value.querySelector<HTMLElement>(".home-dock");
    const height = window.visualViewport?.height ?? window.innerHeight;
    const available =
      height -
      (header?.offsetHeight ?? 76) -
      Math.max(dock?.offsetHeight ?? 0, 52);
    const zoomed =
      (window.visualViewport?.scale ?? 1) > 1.05 ||
      (window.outerWidth > 0 && window.outerWidth / window.innerWidth > 1.35);
    const eligible =
      window.innerWidth >= 1100 &&
      height >= 680 &&
      !zoomed &&
      matchMedia("(pointer: fine)").matches;
    // Content uses the same intrinsic layout in both modes: no toggle/measurement loop.
    const fits = panels().every((panel) => {
      const content = panel.querySelector<HTMLElement>(".screen-content");
      return content && content.offsetHeight + 48 <= available;
    });
    const next = eligible && fits;
    if (next !== paged.value) {
      paged.value = next;
      transitioning.value = false;
      clearTimeout(timer);
      gate.reset();
      if (!next)
        void nextTick().then(() => {
          if (!disposed && active.value === 1)
            panels()[1]?.scrollIntoView({
              block: "start",
              behavior: "instant",
            });
        });
    }
  }
  function scheduleMeasure() {
    if (!frame) frame = requestAnimationFrame(measure);
  }
  function go(index: number, control = false) {
    const next = Math.max(0, Math.min(1, index));
    if (transitioning.value || (paged.value && next === active.value)) return;
    if (!paged.value) {
      panels()[next]?.scrollIntoView({
        block: "start",
        behavior: reduced.value ? "instant" : "smooth",
      });
      return;
    }
    // Explicit controls may move focus out of a panel about to become inert; gestures never do.
    if (control && panels()[active.value]?.contains(document.activeElement)) {
      root.value
        ?.querySelectorAll<HTMLButtonElement>(".home-pagination button")
        [next]?.focus({ preventScroll: true });
    }
    active.value = next;
    transitioning.value = duration.value > 0;
    gate.lock(performance.now() + duration.value);
    timer = setTimeout(() => {
      transitioning.value = false;
    }, duration.value);
  }
  function wheel(event: WheelEvent) {
    if (
      !paged.value ||
      event.ctrlKey ||
      Math.abs(event.deltaX) > Math.abs(event.deltaY) ||
      !event.deltaY
    )
      return;
    if (
      (event.target as Element)?.closest(
        'input,textarea,select,[contenteditable="true"],[data-native-scroll]',
      )
    )
      return;
    event.preventDefault();
    const scale =
      event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? innerHeight : 1;
    const direction = gate.feed(event.deltaY * scale, performance.now());
    if (direction) go(active.value + direction);
  }
  function keyboard(event: KeyboardEvent) {
    if (
      !paged.value ||
      event.defaultPrevented ||
      event.altKey ||
      event.ctrlKey ||
      event.metaKey ||
      event.repeat
    )
      return;
    const target = event.target as Element;
    if (
      target.closest(
        'input,textarea,select,button,a,[contenteditable="true"],[role="button"]',
      )
    )
      return;
    if (
      target !== document.body &&
      target !== document.documentElement &&
      target !== scrollTarget &&
      !root.value?.contains(target)
    )
      return;
    const key = event.key;
    if (
      ![
        "ArrowDown",
        "ArrowUp",
        "PageDown",
        "PageUp",
        "Home",
        "End",
        " ",
      ].includes(key)
    )
      return;
    event.preventDefault();
    go(
      key === "Home"
        ? 0
        : key === "End"
          ? 1
          : active.value +
            (["ArrowUp", "PageUp"].includes(key) ||
            (key === " " && event.shiftKey)
              ? -1
              : 1),
    );
  }
  function scroll() {
    if (paged.value) return;
    const second = panels()[1];
    active.value =
      second && second.getBoundingClientRect().top < innerHeight * 0.5 ? 1 : 0;
  }
  onMounted(() => {
    scrollTarget = pageScrollTarget();
    root.value?.addEventListener("wheel", wheel, { passive: false });
    window.addEventListener("keydown", keyboard);
    window.addEventListener("resize", scheduleMeasure);
    scrollTarget.addEventListener("scroll", scroll, { passive: true });
    window.visualViewport?.addEventListener("resize", scheduleMeasure);
    observer = new ResizeObserver(scheduleMeasure);
    root.value
      ?.querySelectorAll(".landing-header,.home-dock,.screen-content")
      .forEach((el) => observer?.observe(el));
    scheduleMeasure();
    void document.fonts.ready.then(() => {
      if (!disposed) scheduleMeasure();
    });
  });
  onBeforeUnmount(() => {
    disposed = true;
    observer?.disconnect();
    root.value?.removeEventListener("wheel", wheel);
    window.removeEventListener("keydown", keyboard);
    window.removeEventListener("resize", scheduleMeasure);
    scrollTarget?.removeEventListener("scroll", scroll);
    window.visualViewport?.removeEventListener("resize", scheduleMeasure);
    cancelAnimationFrame(frame);
    clearTimeout(timer);
  });
  return { paged, active, transitioning, duration, go };
}
