import { readonly, ref } from "vue";

export type Theme = "light" | "dark";
export const THEME_STORAGE_KEY = "pmc-agent-theme";
export function normalizeTheme(value: unknown): Theme {
  return value === "light" ? "light" : "dark";
}

/** Storage is an optional preference, never a prerequisite for changing appearance. */
export function createThemeState(
  initial: unknown,
  apply: (theme: Theme) => void,
  storage: () => Pick<Storage, "setItem"> | undefined,
) {
  const theme = ref<Theme>(normalizeTheme(initial));
  function sync(value: unknown) {
    const next = normalizeTheme(value);
    if (theme.value === next) return;
    theme.value = next;
    apply(next);
  }
  function setTheme(value: Theme) {
    sync(value);
    try {
      storage()?.setItem(THEME_STORAGE_KEY, theme.value);
    } catch {
      // Private browsing, quota or policy errors must not block the UI.
    }
  }
  return { theme: readonly(theme), setTheme, sync };
}
