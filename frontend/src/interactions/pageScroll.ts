/** Streamlit disables iframe document scrolling; the cloud build owns a viewport. */
export function embeddedScrollRoot(): HTMLElement | null {
  return document.documentElement.dataset.host === "streamlit"
    ? document.getElementById("app")
    : null;
}

export function pageScrollTarget(): HTMLElement | Window {
  return embeddedScrollRoot() ?? window;
}
