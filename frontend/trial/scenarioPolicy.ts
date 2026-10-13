// No local/private scenario registry is imported into this build.
export const trialScenario = "demo_scenario_50_vs_100" as const;
export function isAllowedScenario(value: unknown): boolean {
  return value === trialScenario;
}
