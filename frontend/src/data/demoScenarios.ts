import type { ScenarioId } from "./model";
// Prompts only. Results must come from isolated backend published facts.
export const DEMO_SCENARIO_ID: ScenarioId = "demo_scenario_50_vs_100";
export const demoScenarios = [
  {
    id: "demo-50",
    label: "50 台齐套分析",
    prompt: "生产50台DEMO-PMC-01",
    icon: "layers",
    title: "生产齐套分析",
    description: "查询多层 BOM 和库存，展示后端返回的齐套结果。",
  },
  {
    id: "demo-100",
    label: "100 台缺料分析",
    prompt: "生产100台DEMO-PMC-01",
    icon: "database",
    title: "缺料与限制物料",
    description: "对比需求与权威可用库存，查看缺口及来源。",
  },
  {
    id: "demo-purchase",
    label: "100 台采购建议",
    prompt: "生产100台DEMO-PMC-01的采购建议",
    icon: "cart",
    title: "采购建议查询",
    description: "展示 MOQ、包装倍数、价格和风险分析的后端结果。",
  },
];
