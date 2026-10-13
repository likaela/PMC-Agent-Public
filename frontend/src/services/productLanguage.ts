// Display labels only. Evidence, source provenance and numeric values are unchanged.
export function sourceLabel(kind: string | undefined): string {
  return kind === "合成演示事实" ? "演示产品数据" : kind ?? "";
}
