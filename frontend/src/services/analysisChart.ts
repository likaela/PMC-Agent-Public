/**
 * Pure chart projection over the R19 result/table contract.
 * No HTTP, business arithmetic, fabricated history or mutations.
 * Decimal text is retained; Number is used only for drawing coordinates.
 */
export interface GapBarValue { value: number; text: string }
export interface GapBarStock extends GapBarValue { basis: string }
export interface GapBar {
  key: string; code: string; name: string;
  demand: GapBarValue | null; stock: GapBarStock | null;
}
export interface GapUnitGroup {
  unit: string; items: GapBar[]; max: number; missingCount: number;
}
export interface ShortageItem {
  key: string; code: string; name: string;
  shortage: GapBarValue; suggested: GapBarValue | null;
}
export interface ShortageUnitGroup {
  unit: string; items: ShortageItem[]; max: number;
  missingStockCount: number; unquantifiedCount: number;
}
export interface TrendPoint { at: string; timestamp: number; value: number; text: string }
export interface TrendSeries {
  key: string; code: string; name: string; unit: string; basis: string;
  product?: string; scope?: string; points: TrendPoint[];
}
type Row = Record<string, unknown>;
const STOCK = [
  ["stock_qty_in_bom_unit", "核对库存（物料单位）"],
  ["available_qty", "可用库存"], ["actual_stock_qty", "核验库存"],
  ["recorded_stock_qty", "账面记录"], ["on_hand_qty", "在库数量"],
] as const;
const HISTORY = ["inventory_history", "stock_history", "inventory_snapshots", "stock_snapshots"] as const;
const TIME = ["observed_at", "snapshot_at", "at", "date", "as_of", "recorded_at"] as const;
const MISSING = new Set(["未提供", "未查询到", "无记录", "—", "-", "", "未知"]);
const object = (v: unknown): Row => v && typeof v === "object" && !Array.isArray(v) ? v as Row : {};
const text = (v: unknown): string => typeof v === "string" ? v.trim() : "";
const has = (row: Row, key: string): boolean => Object.prototype.hasOwnProperty.call(row, key);
function decimal(value: unknown): GapBarValue | null {
  // JSON numbers cannot preserve backend Decimal precision, so chart facts must
  // use the existing Decimal-string contract rather than stringify a float.
  if (typeof value !== "string") return null;
  const clean = value.trim();
  if (MISSING.has(clean) || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(clean)) return null;
  const number = Number(clean);
  if (!Number.isFinite(number)) return null;
  if (number === 0 && /[1-9]/.test(clean)) return null; // reject scale underflow
  return { value: number, text: value };
}
export function parseDecimal(value: string | null | undefined): number | null {
  return decimal(value)?.value ?? null;
}
const nonnegative = (v: unknown): GapBarValue | null => {
  const result = decimal(v);
  return result && result.value >= 0 ? result : null;
};
function response(payload: unknown): Row {
  const value = object(payload);
  return has(value, "response") ? object(value.response) : value;
}
function dataOf(payload: unknown): Row {
  const value = response(payload), s = object(value.structured_data);
  if (s.data && typeof s.data === "object" && !Array.isArray(s.data)) return object(s.data);
  return object(object(s.reused_result).data ?? value.data);
}
function validResult(payload: unknown): boolean {
  const value = response(payload);
  return typeof value.status !== "string" || value.status === "ok";
}
function unitOf(row: Row): string { return text(row.unit) || "未提供单位"; }
function metaAt(rows: readonly Record<string, string>[], index: number, raw: unknown): Row | undefined {
  if (raw === undefined) return undefined;
  const materials = dataOf(raw).materials;
  if (!Array.isArray(materials)) return undefined;
  const meta = object(materials[index]);
  return text(meta.material_code) === text(rows[index].material_code) ? meta : { comparison_status: "unmatched" };
}
function stockAllowed(row: Row, meta?: Row): boolean {
  const value = meta ? { ...row, ...meta } : row;
  const unit = unitOf(row);
  if (unit === "未提供单位" || value.record_found === false || value.record_found === "false") return false;
  if (text(row.status) === "库存未查询到") return false;
  const status = text(value.comparison_status);
  if (status && status !== "comparable") return false;
  // The backend's validated normalized observation wins. duplicate_free=false
  // may describe original rows which R19 has already resolved under its policy;
  // a chart must not re-run or override that business decision.
  if (status === "comparable" && nonnegative(row.stock_qty_in_bom_unit)) return true;
  if (value.duplicate_free === false || value.duplicate_free === "false") return false;
  if (has(value, "stock_unit") || has(value, "inventory_unit")) {
    const sourceUnit = text(value.stock_unit) || text(value.inventory_unit);
    if (sourceUnit !== unit && !(status === "comparable" && nonnegative(row.stock_qty_in_bom_unit))) return false;
  }
  return true;
}
function stockOf(row: Row, meta?: Row): GapBarStock | null {
  if (!stockAllowed(row, meta)) return null;
  const source = meta ? { ...row, ...meta } : row;
  const sourceUnit = text(source.stock_unit) || text(source.inventory_unit);
  for (const [key, basis] of STOCK) {
    const value = nonnegative(row[key]);
    if (!value) continue;
    if (key === "stock_qty_in_bom_unit") {
      if (text(source.comparison_status) === "comparable") return { ...value, basis };
    } else if ((!has(source, "stock_unit") && !has(source, "inventory_unit")) || sourceUnit === unitOf(row)) {
      return { ...value, basis };
    }
  }
  return null;
}
export function buildGapGroups(
  rows: ReadonlyArray<Record<string, string>> | undefined, resultType: string | undefined, raw?: unknown,
): GapUnitGroup[] {
  if (!["production", "shortage"].includes(resultType ?? "") || !Array.isArray(rows) ||
      !validResult(raw)) return [];
  const groups = new Map<string, GapBar[]>();
  rows.forEach((row, index) => {
    const demand = nonnegative(row.required_qty);
    if (!demand) return;
    const code = text(row.material_code) || text(row.name) || "物料";
    const unit = unitOf(row), items = groups.get(unit) ?? [];
    items.push({ key: code + "-" + index, code, name: text(row.name), demand,
      stock: stockOf(row, metaAt(rows, index, raw)) });
    groups.set(unit, items);
  });
  return [...groups].map(([unit, items]) => ({ unit, items,
    max: Math.max(0, ...items.map(x => Math.max(x.demand?.value ?? 0, x.stock?.value ?? 0))),
    missingCount: items.filter(x => x.stock === null).length }));
}

/** Drawing ticks are approximate labels, never replacement facts. Bounded loop
 * avoids tiny-value duplicates and overflow/infinite loops at large magnitudes. */
export function niceTicks(max: number, count = 4): number[] {
  if (!Number.isFinite(max) || max <= 0) return [0];
  const ticks = [0];
  const bounded = Math.min(10, Math.max(1, Number.isFinite(count) ? Math.floor(count) : 4));
  const raw = max / bounded;
  const power = 10 ** Math.floor(Math.log10(raw));
  if (raw === 0 || power === 0 || !Number.isFinite(power)) return [0, max];
  const ratio = raw / power;
  const step = (ratio <= 1 ? 1 : ratio <= 2 ? 2 : ratio <= 5 ? 5 : 10) * power;
  if (!Number.isFinite(step) || step <= 0) return [0, max];
  for (let i = 1; i <= 12; i++) {
    const value = i * step;
    if (!Number.isFinite(value) || value > max) break;
    if (Number.isFinite(value) && value > ticks[ticks.length - 1]) ticks.push(value);
  }
  if (ticks[ticks.length - 1] < max) ticks.push(max);
  return ticks;
}
export function gapSummary(groups: GapUnitGroup[]): string {
  if (!groups.length) return "";
  return groups.map(group => "需求与库存对比（" + group.unit + "）：" + group.items.length +
    " 种物料，其中 " + group.missingCount + " 种库存未提供或无法比较；未提供不等于 0。").join("");
}
function positiveParts(value: string): [string, string] {
  const [whole, fraction = ""] = value.trim().replace(/^[+-]/, "").split(".");
  return [(whole || "0").replace(/^0+(?=\d)/, ""), fraction.replace(/0+$/, "")];
}
function comparePositive(a: string, b: string): number {
  const [aw, af] = positiveParts(a), [bw, bf] = positiveParts(b);
  if (aw.length !== bw.length) return aw.length - bw.length;
  if (aw !== bw) return aw < bw ? -1 : 1;
  const len = Math.max(af.length, bf.length), ax = af.padEnd(len, "0"), bx = bf.padEnd(len, "0");
  return ax === bx ? 0 : ax < bx ? -1 : 1;
}
export function buildShortageGroups(
  rows: ReadonlyArray<Record<string, string>> | undefined, resultType: string | undefined, raw?: unknown,
): ShortageUnitGroup[] {
  if (!["production", "shortage"].includes(resultType ?? "") || !Array.isArray(rows) ||
      !validResult(raw)) return [];
  const groups = new Map<string, ShortageUnitGroup>();
  rows.forEach((row, index) => {
    const unit = unitOf(row), code = text(row.material_code) || text(row.name) || "物料";
    const group = groups.get(unit) ?? { unit, items: [], max: 0, missingStockCount: 0, unquantifiedCount: 0 };
    const meta = metaAt(rows, index, raw);
    const shortage = nonnegative(row.shortage_qty);
    if (!stockAllowed(row, meta)) {
      group.missingStockCount++;
    } else if (shortage && shortage.value > 0) {
      group.items.push({ key: code + "-" + index, code, name: text(row.name), shortage,
        suggested: nonnegative(row.suggested_purchase_qty) });
    } else if (!stockOf(row, meta)) {
      group.missingStockCount++;
    } else if (text(row.status) === "不满足需求") {
      group.unquantifiedCount++;
    }
    groups.set(unit, group);
  });
  return [...groups.values()].filter(x => x.items.length || x.missingStockCount || x.unquantifiedCount)
    .map(group => {
      group.items.sort((a, b) => comparePositive(b.shortage.text, a.shortage.text) || a.code.localeCompare(b.code));
      group.max = group.items[0]?.shortage.value ?? 0;
      return group;
    });
}
export function shortageSummary(groups: ShortageUnitGroup[]): string {
  return groups.map(group => "缺料清单（" + group.unit + "）：" + group.items.length +
    " 种物料需要处理" + (group.items.length ? "，本单位最大缺口 " + group.items[0].shortage.text : "") +
    (group.missingStockCount ? "；" + group.missingStockCount + " 种库存未提供" : "") +
    (group.unquantifiedCount ? "；" + group.unquantifiedCount + " 种缺口数量未提供" : "") +
    (group.missingStockCount || group.unquantifiedCount ? "，均未计入缺口" : "") + "。").join("");
}
export function inventoryHistoryEntries(payload: unknown): unknown[] {
  const root = response(payload), structured = object(root.structured_data), reused = object(structured.reused_result);
  for (const holder of [structured, object(structured.data), reused, object(reused.data), root]) {
    for (const key of HISTORY) if (Array.isArray(holder[key])) return holder[key] as unknown[];
  }
  return [];
}
function reliableTime(value: unknown): { at: string; timestamp: number; kind: string } | null {
  const at = text(value);
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2}))?$/.exec(at);
  if (!m) return null;
  const year = +m[1], month = +m[2], day = +m[3];
  if (year < 1000 || month < 1 || month > 12 || day < 1 ||
      day > new Date(Date.UTC(year, month, 0)).getUTCDate()) return null;
  if (m[4] && (+m[4] > 23 || +m[5] > 59 || +m[6] > 59)) return null;
  if (m[8] && m[8] !== "Z") {
    const hour = +m[8].slice(1, 3), minute = +m[8].slice(4, 6);
    if (hour > 14 || minute > 59 || hour === 14 && minute !== 0) return null;
  }
  const timestamp = Date.parse(m[4] ? at : at + "T00:00:00Z");
  return Number.isFinite(timestamp) ? { at, timestamp, kind: m[4] ? "instant" : "date" } : null;
}
/** Invalid/missing points or conflicting same-time observations suppress the
 * affected series. We do not silently bridge holes or choose a convenient row. */
export function buildInventoryTrends(payload: unknown): TrendSeries[] {
  if (!validResult(payload)) return [];
  const entries = inventoryHistoryEntries(payload), data = dataOf(payload);
  const root = response(payload), binding = object(object(root.structured_data).product_context);
  const productDefault = text(data.product) || text(binding.product_code);
  const groups = new Map<string, TrendSeries>();
  const observedTimes = new Map<string, Set<number>>();
  const brokenBase = new Set<string>(), brokenMaterial = new Set<string>(), brokenSeries = new Set<string>();
  entries.forEach(entry => {
    const row = object(entry), code = text(row.material_code) || text(row.code);
    if (!code) return;
    const product = text(row.product_code) || text(row.product) || productDefault;
    const material = JSON.stringify([product, code]), unit = text(row.unit);
    if (!unit || MISSING.has(unit)) { brokenMaterial.add(material); return; }
    const scope = ["site", "warehouse", "location", "batch_no", "inventory_scope"]
      .map(key => text(row[key]) ? key + "=" + text(row[key]) : "").filter(Boolean).join("；");
    const base = JSON.stringify([product, code, unit, scope]);
    let instant: ReturnType<typeof reliableTime> = null;
    for (const key of TIME) if (has(row, key)) { instant = reliableTime(row[key]); break; }
    if (!instant) { brokenBase.add(base); return; }
    const observationKey = JSON.stringify([base, instant.kind]);
    const times = observedTimes.get(observationKey) ?? new Set<number>();
    times.add(instant.timestamp);
    observedTimes.set(observationKey, times);
    let selected: { field: string; basis: string; point: GapBarValue } | undefined;
    const present = STOCK.filter(([key]) => key !== "stock_qty_in_bom_unit" && has(row, key));
    for (const [field, basis] of present) {
      const point = nonnegative(row[field]);
      const key = JSON.stringify([base, field, instant.kind]);
      if (!point) brokenSeries.add(key);
      else if (!selected) selected = { field, basis, point };
    }
    if (!selected) {
      if (!present.length) brokenBase.add(base);
      return;
    }
    const key = JSON.stringify([base, selected.field, instant.kind]);
    const group = groups.get(key) ?? { key, code, name: text(row.name), unit,
      basis: selected.basis, product: product || undefined, scope: scope || undefined, points: [] };
    group.points.push({ ...instant, value: selected.point.value, text: selected.point.text });
    groups.set(key, group);
  });
  return [...groups.values()].filter(group => {
    const material = JSON.stringify([group.product || "", group.code]);
    const scope = group.scope || "";
    const base = JSON.stringify([group.product || "", group.code, group.unit, scope]);
    if (brokenMaterial.has(material) || brokenBase.has(base) || brokenSeries.has(group.key)) return false;
    const points = new Map<number, TrendPoint>();
    for (const point of group.points) {
      const prior = points.get(point.timestamp);
      if (prior && comparePositive(prior.text, point.text) !== 0) return false;
      if (!prior) points.set(point.timestamp, point);
    }
    group.points = [...points.values()].sort((a, b) => a.timestamp - b.timestamp);
    if (group.points.length < 2) return false;
    const kind = JSON.parse(group.key)[2];
    const times = observedTimes.get(JSON.stringify([base, kind])) ?? [];
    const first = group.points[0].timestamp, last = group.points.at(-1)!.timestamp;
    // A middle snapshot which only reports another quantity basis is still a
    // hole for this series. Do not draw a line across that missing observation.
    if ([...times].some(at => at > first && at < last && !points.has(at))) return false;
    return true;
  });
}
export function trendDateText(at: string): string {
  return reliableTime(at)?.at.slice(0, 10) ?? at;
}
export function trendSummary(series: TrendSeries[]): string {
  return series.map(item => {
    const first = item.points[0], last = item.points.at(-1);
    if (!first || !last) return "";
    return (item.product ? item.product + " / " : "") + item.code +
      "（" + item.unit + "，" + item.basis + (item.scope ? "，" + item.scope : "") +
      "）：" + item.points.length + " 个历史快照，从 " + trendDateText(first.at) + " 的 " + first.text +
      " 变为 " + trendDateText(last.at) + " 的 " + last.text + "。";
  }).join("");
}
