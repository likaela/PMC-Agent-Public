import type { ScenarioId } from "../data/model";
export interface SessionPointer {
  session_id: string;
  scenario_id?: ScenarioId | null;
}
export interface PointerStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}
export interface SessionPreference {
  mode: "mock" | "live";
  scenario_id?: ScenarioId | null;
  demo_selected?: boolean;
}
export function preferenceKey(baseUrl: string): string {
  return sessionKey(baseUrl) + ":preference";
}
export function readPreference(
  storage: PointerStorage,
  baseUrl: string,
): SessionPreference | null {
  try {
    const p = JSON.parse(storage.getItem(preferenceKey(baseUrl)) ?? "null");
    if (p?.mode === "mock") {
      // Discard obsolete offline selection, not backend Demo history pointers.
      storage.removeItem(preferenceKey(baseUrl));
      return null;
    }
    if (
      !p ||
      p.mode !== "live" ||
      (p.scenario_id != null && !["demo_scenario_50_vs_100", "review_real_bom_simulation", "kq_z112_review_slim", "kq_z112_review_slim_synthetic"].includes(p.scenario_id)) ||
      (p.demo_selected !== undefined && typeof p.demo_selected !== "boolean")
    )
      return null;
    return p;
  } catch {
    return null;
  }
}
// Every page entry is enterprise. Demo history needs a new explicit user selection.
export function readInitialScenario(
  _storage: PointerStorage,
  _baseUrl: string,
  _isReload: boolean,
): ScenarioId | null {
  return null;
}
export function savePreference(
  storage: PointerStorage,
  baseUrl: string,
  preference: SessionPreference,
): void {
  try {
    storage.setItem(preferenceKey(baseUrl), JSON.stringify(preference));
  } catch {
    /* Optional browser cache. */
  }
}
export function sessionKey(
  baseUrl: string,
  scenarioId?: ScenarioId | null,
): string {
  return `pmc-agent:v1.14.1:${baseUrl.replace(/\/+$/, "")}:${scenarioId ?? "enterprise"}`;
}
// Cache only an opaque pointer; facts and answers remain in the backend conversation store.
export function saveSession(
  storage: PointerStorage,
  key: string,
  pointer: SessionPointer,
): void {
  try {
    storage.setItem(key, JSON.stringify(pointer));
  } catch {
    /* Storage unavailable: in-memory session remains usable. */
  }
}
export function readSession(
  storage: PointerStorage,
  key: string,
  scenarioId?: ScenarioId | null,
): SessionPointer | null {
  try {
    const value = storage.getItem(key);
    if (!value) return null;
    const p = JSON.parse(value);
    if (
      !p ||
      typeof p.session_id !== "string" ||
      !p.session_id ||
      (p.scenario_id ?? null) !== (scenarioId ?? null)
    ) {
      storage.removeItem(key);
      return null;
    }
    return p;
  } catch {
    clearSession(storage, key);
    return null;
  }
}
export function clearSession(storage: PointerStorage, key: string): void {
  try {
    storage.removeItem(key);
  } catch {
    /* Safe on restricted browsers. */
  }
}
