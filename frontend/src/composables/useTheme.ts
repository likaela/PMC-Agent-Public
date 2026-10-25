import { createThemeState, THEME_STORAGE_KEY } from "./themeState";

// The head bootstrap sets this before CSS and before Vue is mounted.
const state = createThemeState(
  typeof document === "undefined"
    ? "dark"
    : document.documentElement.dataset.theme,
  (theme) => {
    document.documentElement.dataset.theme = theme;
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute("content", theme === "light" ? "#F5F7FB" : "#10141D");
  },
  () => window.localStorage,
);

// One listener for the application, not one per control or route mount.
if (typeof window !== "undefined") {
  const syncStorage = (event: StorageEvent) => {
    if (event.key === THEME_STORAGE_KEY || event.key === null)
      state.sync(event.newValue);
  };
  window.addEventListener("storage", syncStorage);
  if (import.meta.hot)
    import.meta.hot.dispose(() =>
      window.removeEventListener("storage", syncStorage),
    );
}
export function useTheme() {
  return { theme: state.theme, setTheme: state.setTheme };
}
