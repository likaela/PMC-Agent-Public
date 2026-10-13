// Explicit production build flag; local enterprise development remains unchanged.
export const trialMode = import.meta.env.VITE_TRIAL_MODE === "true";
export const trialScenario = "demo_scenario_50_vs_100" as const;
