import { QueryError, type ScenarioId } from "../src/data/model";
export interface ReviewProduct {
  product_code: string; product_name: string | null; revision: string; unit: string | null;
  line_count: number; synthetic: boolean; scenario_id: ScenarioId | null;
  capabilities: string[]; limitations: string[];
  default_mode: string;
  modes: {mode: string; label: string; line_count: number; scenario_id: ScenarioId | null; dataset_version: string | null}[];
}
export async function reviewRequest(path: string, body?: unknown, signal?: AbortSignal): Promise<any> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(abort, 30000);
  try {
    const response = await fetch(path, { method: body === undefined ? "GET" : "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
      headers: body === undefined ? undefined : {"Content-Type": "application/json"}, signal: controller.signal });
    if (!response.ok) throw new QueryError("server", "请求未完成，请检查账号权限及连接后重试。");
    return await response.json();
  } catch (e) {
    if (e instanceof QueryError) throw e;
    throw new QueryError("network", "无法连接分析服务，请稍后重试。");
  } finally { clearTimeout(timer); signal?.removeEventListener("abort", abort); }
}
