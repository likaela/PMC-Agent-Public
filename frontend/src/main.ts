import { createApp } from "vue";
import { createRouter, createWebHashHistory } from "vue-router";
import App from "./App.vue";
import HomeView from "./views/HomeView.vue";
import WorkspaceView from "./views/WorkspaceView.vue";
import "./theme.css";
import "./live.css";
import "./styles.css";
import "./motion.css";
import "./theme-surfaces.css";
import "./theme-controls.css";
import "./embedded.css";
import { initializeStreamlitBridge } from "./services/streamlitBridge";
import { embeddedScrollRoot } from "./interactions/pageScroll";

if (import.meta.env.VITE_STREAMLIT === "true") initializeStreamlitBridge();

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", component: HomeView },
    { path: "/workspace", component: WorkspaceView },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
  scrollBehavior: () => {
    embeddedScrollRoot()?.scrollTo({ top: 0, left: 0, behavior: "instant" });
    return { top: 0 };
  },
});
createApp(App).use(router).mount("#app");
