import type { AgentResult, Scenario, Source } from "./model";

const projectSource: Source = {
  id: "project-demo",
  title: "项目资料中的单层 BOM 与库存样例",
  kind: "项目资料示例",
  scope: "本次分析来源",
  reference: "PMC-Agent_Codex前端开发提示词.txt · 第七节（转述协作说明样例）",
  version: null,
  publishedAt: null,
  conditions:
    "仅适用于 ABC-1000、生产 1,000 台的固定演示。原始协作说明未交接；库存口径、占用与生效时间未提供。",
};
const syntheticSource = (id: string, conditions: string): Source => ({
  id,
  title: "前端交互验证 fixture",
  kind: "人工构造演示样例",
  scope: "本次分析来源",
  reference: `src/data/fixtures.ts · ${id}`,
  version: null,
  publishedAt: null,
  conditions,
});
const production: AgentResult = {
  id: "production",
  status: "partial",
  product: "ABC-1000",
  quantity: "1000",
  quantityUnit: "台",
  title: "主板存在缺口，完整齐套情况待确认",
  answer:
    "生产 1,000 台 ABC-1000，已知主板缺少 200 件。电机与螺丝的库存未提供，暂时无法完整判断物料齐套。",
  conclusion: "先核实主板补料，再补充其余库存",
  nextAction: "核查主板 200 件缺口，并补充电机、螺丝的可用库存与来源时间。",
  materials: [
    {
      id: "mainboard",
      name: "主板",
      spec: "ABC-1000 / 单层 BOM",
      required: "1000",
      available: "800",
      shortage: "200",
      unit: "件",
      state: "shortage",
      sourceIds: ["project-demo"],
    },
    {
      id: "motor",
      name: "电机",
      spec: "ABC-1000 / 单层 BOM",
      required: "2000",
      available: null,
      shortage: null,
      unit: "件",
      state: "unknown",
      sourceIds: ["project-demo"],
    },
    {
      id: "screw",
      name: "螺丝",
      spec: "ABC-1000 / 单层 BOM",
      required: "8000",
      available: null,
      shortage: null,
      unit: "件",
      state: "unknown",
      sourceIds: ["project-demo"],
    },
  ],
  sources: [projectSource],
  warnings: [
    "电机、螺丝库存未提供，不能视为零库存或已满足需求。",
    "未核实库存口径和发布时间；本结果不构成排产或交期承诺。",
  ],
};
const inventory: AgentResult = {
  id: "inventory",
  status: "inventory",
  product: null,
  quantity: null,
  quantityUnit: "台",
  title: "主板示例库存为 800 件",
  answer:
    "项目资料示例提供主板库存 800 件，发布时间未提供。库存口径尚待后端确认。",
  conclusion: "库存数值已提供，时效仍待核实",
  nextAction: "接入真实后端后，核对可用库存定义、占用情况及发布时间。",
  materials: [
    {
      id: "mainboard",
      name: "主板",
      spec: "项目资料库存样例",
      required: null,
      available: "800",
      shortage: null,
      unit: "件",
      state: "published",
      sourceIds: ["project-demo"],
    },
  ],
  sources: [
    {
      ...projectSource,
      conditions: "仅展示资料提供的主板库存 800 件；不代表当前企业实时库存。",
    },
  ],
  warnings: ["来源发布时间未提供，请勿将请求时间视为数据发布时间。"],
};
function synthetic(id: string, props: Partial<AgentResult>): AgentResult {
  return {
    id,
    status: "no-facts",
    product: null,
    quantity: null,
    quantityUnit: "台",
    title: "",
    answer: "",
    conclusion: "",
    nextAction: "",
    materials: [],
    sources: [
      syntheticSource(
        id,
        "仅用于验证界面状态，不属于企业事实，不适用于实际生产。",
      ),
    ],
    warnings: ["人工构造演示样例，仅用于前端状态验证。"],
    ...props,
  };
}
export const scenarios: Scenario[] = [
  {
    id: "production",
    label: "生产物料分析",
    prompt: "生产 1,000 台 ABC-1000，物料够吗？",
    category: "project",
    result: production,
  },
  {
    id: "inventory",
    label: "主板库存查询",
    prompt: "查询主板库存",
    category: "project",
    result: inventory,
  },
  {
    id: "purchase",
    label: "独立采购建议",
    prompt: "查看独立采购建议样例",
    category: "project",
    result: {
      id: "purchase",
      status: "purchase",
      product: null,
      quantity: null,
      quantityUnit: "台",
      title: "独立样例：建议采购 300 件",
      answer:
        "资料中的独立采购样例：缺料 200 件，安全库存 100 件，建议采购 300 件。该样例不等同于 ABC-1000 的采购结论。",
      conclusion: "采购建议需核实适用产品与条件",
      nextAction:
        "后续接入采购建议接口，补齐物料标识、约束、供应商和计算依据。",
      materials: [],
      purchase: {
        material: "未指定物料（独立样例）",
        shortage: "200",
        safetyStock: "100",
        suggested: "300",
        unit: "件",
        note: "数值直接来自固定样例，前端未进行采购计算；不产生采购订单。",
      },
      sources: [
        {
          ...projectSource,
          id: "purchase-example",
          title: "项目资料中的独立采购样例",
          conditions:
            "缺料 200、安全库存 100、建议采购 300；适用产品与物料未指定，不关联 ABC-1000。",
        },
      ],
      warnings: ["独立采购样例，不是 ABC-1000 的真实采购结论。"],
    },
  },
  {
    id: "ready",
    label: "所列物料齐套",
    prompt: "查看所列物料齐套演示",
    category: "synthetic",
    result: synthetic("ready", {
      status: "ready",
      product: "DEMO-READY",
      quantity: "100",
      title: "所列物料满足本次演示需求",
      answer:
        "人工构造结果：DEMO-READY 生产 100 台，所列两种物料满足需求。这不是实际产能或交期承诺。",
      conclusion: "所列物料条件满足",
      nextAction: "实际生产仍需后端核实完整 BOM、设备、人力和其他约束。",
      materials: [
        {
          id: "demo-board",
          name: "演示主板",
          spec: "人工构造 / DEMO-READY",
          required: "100",
          available: "120",
          shortage: "0",
          unit: "件",
          state: "sufficient",
          sourceIds: ["ready"],
        },
        {
          id: "demo-motor",
          name: "演示电机",
          spec: "人工构造 / DEMO-READY",
          required: "200",
          available: "200",
          shortage: "0",
          unit: "件",
          state: "sufficient",
          sourceIds: ["ready"],
        },
      ],
    }),
  },
  {
    id: "zero",
    label: "库存确为 0",
    prompt: "查看零库存演示",
    category: "synthetic",
    result: synthetic("zero", {
      status: "zero",
      title: "演示物料已发布库存为 0 件",
      answer:
        "演示连接器库存明确为 0 件，属于已提供的零值，不是库存字段缺失或无已发布事实。",
      conclusion: "已有库存记录，数值为零",
      nextAction: "核查补货情况与库存记录的适用条件。",
      materials: [
        {
          id: "demo-connector",
          name: "演示连接器",
          spec: "人工构造 / 零库存",
          required: null,
          available: "0",
          shortage: null,
          unit: "件",
          state: "published",
          sourceIds: ["zero"],
        },
      ],
    }),
  },
  {
    id: "no-facts",
    label: "无已发布事实",
    prompt: "查看无已发布事实演示",
    category: "synthetic",
    result: synthetic("no-facts", {
      title: "暂无该物料的已发布库存数据",
      answer: "演示传感器没有可用的已发布库存事实，不能将其库存视为 0。",
      conclusion: "缺少事实，无法给出库存数值",
      nextAction: "请由后端提供并发布适用的库存事实。",
      materials: [
        {
          id: "demo-sensor",
          name: "演示传感器",
          spec: "人工构造 / 无事实",
          required: null,
          available: null,
          shortage: null,
          unit: "件",
          state: "no-facts",
          sourceIds: ["no-facts"],
        },
      ],
    }),
  },
  {
    id: "conflict",
    label: "条件冲突",
    prompt: "查看条件冲突演示",
    category: "synthetic",
    result: synthetic("conflict", {
      status: "conflict",
      title: "BOM 适用版本存在冲突",
      answer:
        "人工构造场景中存在两个候选 BOM 版本，尚未确定适用版本，不能输出物料需求。",
      conclusion: "请先确认唯一适用版本",
      nextAction: "补充 BOM 版本和生效日期，再发起完整查询。",
      warnings: [
        "人工构造演示样例。",
        "适用条件存在冲突，本次不展示计算数值。",
      ],
    }),
  },
  {
    id: "uncalculable",
    label: "无法计算",
    prompt: "查看无法计算演示",
    category: "synthetic",
    result: synthetic("uncalculable", {
      status: "uncalculable",
      title: "缺少单位换算依据，暂无法计算",
      answer:
        "需求与库存的单位不一致，且缺少后端确认的换算关系。前端不会自行换算。",
      conclusion: "补充单位规则后再分析",
      nextAction: "请后端确认标准单位、转换关系和适用范围。",
    }),
  },
  {
    id: "network",
    label: "请求失败与重试",
    prompt: "模拟请求失败与重试",
    category: "synthetic",
    error: "network",
  },
  {
    id: "mapping",
    label: "响应映射失败",
    prompt: "模拟响应映射失败",
    category: "synthetic",
    error: "mapping",
  },
];
export function findScenario(text: string) {
  const normalize = (value: string) =>
    value.trim().replace(/[\s,，?？。]/g, "");
  return scenarios.find((s) => normalize(s.prompt) === normalize(text));
}
