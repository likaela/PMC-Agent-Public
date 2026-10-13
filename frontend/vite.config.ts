import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";
import { resolve } from "node:path";
import type { Plugin } from "vite";

const frontendRoot = process.cwd();
const safeComponents = new Set(["Icon", "StatusBadge", "MessageList", "QueryComposer", "ResultPanel", "MaterialTable", "SourceDetails", "SmartQueries"]);
function trialBoundary(entry = "trial"): Plugin {
  return {
    name: "synthetic-trial-module-boundary",
    generateBundle(_, bundle) {
      const modules = new Set<string>();
      for (const value of Object.values(bundle)) {
        if (value.type !== "chunk") continue;
        for (const id of Object.keys(value.modules)) {
          const normalized = id.replaceAll("\\", "/").split("?")[0];
          if (normalized.includes("/node_modules/") || normalized.startsWith("\0")) continue;
          const relative = normalized.replace(frontendRoot.replaceAll("\\", "/") + "/", "");
          const component = /^src\/components\/([^/]+)\.vue$/.exec(relative);
          if (!(relative.startsWith(entry + "/") || (component && safeComponents.has(component[1])) ||
                ["src/data/model.ts", "src/services/apiClient.ts", "src/services/responseAdapter.ts", "src/services/productLanguage.ts", "src/styles.css"].includes(relative)))
            this.error("An unapproved application module entered the trial build");
          modules.add(relative);
        }
      }
      for (const value of Object.values(bundle)) {
        const text = value.type === "chunk" ? value.code : typeof value.source === "string" ? value.source : Buffer.from(value.source).toString();
        if (/KQ-|kq_z112|review_real_bom_simulation|data[\\/]raw|data[\\/]normalized|[CE]:[\\/]/i.test(text))
          this.error("Private identifiers detected in trial output");
      }
      this.emitFile({ type: "asset", fileName: "module-closure.json", source: JSON.stringify([...modules].sort(), null, 2) });
    },
  };
}
export default defineConfig(({ mode }) => {
  const trialOut = process.env.PMC_TRIAL_BUILD_OUT;
  const reviewOut = process.env.PMC_REVIEW_BUILD_OUT;
  if (trialOut && reviewOut) throw new Error("Choose one isolated build");
  if (trialOut || reviewOut) {
    const entry = reviewOut ? "review" : "trial";
    const output = reviewOut ?? trialOut!;
    const runtimeRoot = resolve(frontendRoot, "../.runtime") + "/";
    if (!resolve(output).replaceAll("\\", "/").startsWith(runtimeRoot.replaceAll("\\", "/")))
      throw new Error("Trial output must be a dedicated runtime directory");
    return {
      root: resolve(frontendRoot, entry),
      publicDir: false,
      envDir: false,
      plugins: [vue(), trialBoundary(entry)],
      resolve: { alias: [
        { find: "../composables/useAgent", replacement: resolve(frontendRoot, entry, reviewOut ? "useReviewAgent.ts" : "useTrialAgent.ts") },
        { find: "./scenarioPolicy", replacement: resolve(frontendRoot, entry, "scenarioPolicy.ts") },
      ] },
      define: { "import.meta.env.VITE_API_BASE_URL": JSON.stringify("/") },
      build: { outDir: resolve(output), emptyOutDir: false, sourcemap: false },
    };
  }
  if (process.env.VITE_TRIAL_MODE === "true") throw new Error("Use the dedicated trial build script");
  const env = loadEnv(mode, process.cwd(), "VITE_");
  return {
    plugins: [vue()],
    define: {
      "import.meta.env.VITE_API_BASE_URL": JSON.stringify(
        process.env.VITE_API_BASE_URL ?? env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000",
      ),
    },
    server: { port: 5173, strictPort: true },
  };
});
