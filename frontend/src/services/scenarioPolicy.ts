// Local application whitelist. The trial build replaces this entire module.
export function isAllowedScenario(value: unknown): boolean {
  return typeof value === "string" && [
    "demo_scenario_50_vs_100", "review_real_bom_simulation",
    "kq_z112_review_slim", "kq_z112_review_slim_synthetic",
  ].includes(value);
}
