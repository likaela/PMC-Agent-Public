# Agent HTTP契约适配

`createSession()`调用 `POST /api/v1/agent/sessions`；`queryAgent()`调用 `POST /api/v1/agent/query`；`getSession()`调用 `GET /api/v1/agent/sessions/{session_id}`。企业产品不传演示scenario；DEMO-PMC-01会话明确绑定 `demo_scenario_50_vs_100`。

后端响应包含 `intent`、`status`、`answer`、`structured_data`、`provenance`、`warnings` 和会话标识。`BackendResponse`保留原契约，`responseAdapter`依据意图及结构化结果生成独立 `FrontendViewModel`，不解析answer计算数值。

| 查询 | 默认展示 |
|---|---|
| 产品目录 | 可查询产品与编码 |
| BOM | 版本、物料、单位用量 |
| 库存 | 可核验库存数量与记录 |
| 指定生产数量 | 需求、满足状态、实际缺口 |
| 最大数量 | 可计算上限、核算范围、限制物料 |
| 采购 | 有事实依据的建议数量、参数和成本 |
| 来源 | 计算依据与可追溯记录 |

Decimal保留字符串，null不补零。没有有效值不生成空指标；部分覆盖指标不命名为完整产能。HTTP状态ok代表查询执行成功，不代替物料满足结论。

来源详情保留后端证据，普通卡片不直接显示数据库ID、技术错误或内部路径。历史恢复使用保存的回答，不重新执行Tool生成历史数字。请求取消、切换产品和新请求均隔离旧响应；错误不触发mock fallback。
