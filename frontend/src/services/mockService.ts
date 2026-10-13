import { findScenario } from "../data/fixtures";
import { QueryError, type AgentQueryService } from "../data/model";

function delay(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException("Cancelled", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, 750);
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
}
export function createMockService(): AgentQueryService {
  return {
    async query({ text }, signal) {
      await delay(signal);
      const scenario = findScenario(text);
      if (!scenario)
        throw new QueryError(
          "unsupported",
          "当前演示仅支持固定示例问题。请从“演示场景”选择，型号或数量变化不会套用已有结果。",
        );
      if (scenario.error === "network")
        throw new QueryError(
          "network",
          "模拟网络请求失败。本场景每次重试都会失败；可切换其他演示场景继续体验。",
        );
      if (scenario.error === "mapping") {
        console.info(
          "[PMC-Agent demo] Expected mapping failure fixture; no backend payload was requested.",
        );
        throw new QueryError(
          "mapping",
          "模拟响应映射失败。结果未通过展示结构校验，未生成业务结论。",
        );
      }
      return structuredClone(scenario.result!);
    },
  };
}
