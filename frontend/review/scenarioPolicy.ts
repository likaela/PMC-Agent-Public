// IDs are disclosed by the authenticated catalog, never private build constants.
let allowed = new Set<string>(["demo_scenario_50_vs_100"]);
export function configureScenarios(values: (string | null)[]) {
  allowed = new Set(values.filter((v): v is string => typeof v === "string"));
}
export function isAllowedScenario(value: unknown): boolean {
  return typeof value === "string" && allowed.has(value);
}
