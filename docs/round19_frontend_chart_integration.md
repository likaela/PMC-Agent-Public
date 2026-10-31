# R19 前端图表移植记录

日期：2026-10-31。状态：代码、自动化回归及本地生产构建浏览器验证完成；未暂存、提交、推送或部署。

## 1. 基线与改动保护

- 目标为 `releases/PMC-Agent-Public-R19`，原分支 `release/round19-20261025`。
- 原始及当前 HEAD 均为 `5c56c5390541ef7661b656bbf13c9ac048d4ffb5`。
- 独立工作分支：`codex/round19-frontend-charts-20261031`。没有 reset、覆盖工作区或切换到新版业务代码。
- 开始时已跟踪工作区干净、暂存区为空；既存 `.logs/`、bundle、说明文档及 `start_test.ps1` 等未跟踪文件均保留。
- 参考目录实际项目位于 `PMC-Agent-source-ddf409f(1)/PMC-Agent-source-ddf409f`。它是无 `.git` 的解压目录，不能据目录名证明提交身份。
- 开始记录了338个既存已跟踪/未跟踪文件的路径、大小及 SHA-256。另对全部411个 `data/` 和 `src/backend/` 文件（含忽略文件）做了校验；测试和浏览器操作后无差异。

## 2. 先比较再移植

| 来源版本差异 | 本轮处理 |
| --- | --- |
| 新增 `InventoryGapChart.vue` | 复用 SVG 需求/库存柱图，适配 R19 单位、空值和 Decimal 契约 |
| 新增 `ShortageBarChart.vue` | 复用按单位分组的缺口横向条形图 |
| 新增 `InventoryTrendChart.vue` | 复用时间折线组件；只接受明确的多时点数据，没有数据则隐藏 |
| 新增 `services/analysisChart.ts` | 适配为对 R19 结构化结果的纯展示投影，不复制业务计算 |
| `ResultPanel.vue` | 只接入三个组件及当前结果派生数据；保留原确认执行、权限和预览有效期逻辑 |
| `live.css` 中约348行图表样式 | 提取为独立 `chart.css`，不覆盖 R19 整体样式 |
| `AppShell.vue`、`agentStore.ts`、`apiClient.ts`、`streamlitBridge.ts` | 保留 R19；来源的云端/Streamlit差异与图表无关，不移植 |
| `responseAdapter.ts` | 不整体替换；仅补充 R19 已有的归一化库存及原库存单位展示列 |
| 来源新增前端测试 | 审查后保留适用的图表断言，补充 R19 专项；不复制不相关工作流/云端测试 |
| `package.json`、`package-lock.json` | 无新增依赖；保留 R19 锁文件及全部依赖版本，只修正测试入口 |
| `index.html`、`vite.config.ts` | 比较无差异；保持原文件 |

来源锁文件包含 registry 与 `source-map-js` 等非图表更新，未引入。后端、数据库、迁移、Docker、环境配置均未从来源目录复制。

## 3. 移植内容与数据契约

### 需求 / 库存对比

- 仅在本次选中回答为成功的 `production` 或 `shortage` 结果时显示。
- 数据来自原 `adaptBackendResponse()` 生成的 `backend.rows`，与物料明细表使用同一行集合。
- 使用 `material_code`、`name`、`unit`、`required_qty`；库存优先使用后端已归一化且 `comparison_status=comparable` 的 `stock_qty_in_bom_unit`。
- 否则只使用单位一致、可比较的 `available_qty`、`actual_stock_qty`、`recorded_stock_qty` 或 `on_hand_qty`，明确区分数量口径；不把在库量改称可用量。
- `structured_data.data.materials` 仅用于核对同位置同料号的单位/可比较状态；不从回答文字提取数字，不自行换算单位。
- 真实0绘制零线；null、无记录或不可比较的库存留空槽，绝不补0。后端库存原单位与物料单位不同时，表格同时呈现后端归一化数量和原单位。

### 缺料条形图

- 仅使用同一批行中已由后端给出的正数 `shortage_qty`；不在前端执行“需求减库存”。
- 同单位内排序；不同单位分别显示，不合计、不跨单位比较大小。
- 可选 `suggested_purchase_qty` 只有后端实际提供时才显示；不生成采购建议。
- 无记录和未量化缺口不伪装成确定缺口。空缺口与空库存分别统计说明。

### 库存趋势

- 只识别明确的 `inventory_history`、`stock_history`、`inventory_snapshots`、`stock_snapshots` 数组。
- 必须有同产品、物料、单位、数量口径及仓库/批次等范围下至少两个可靠时点。可见标题标识产品、范围、单位和库存口径。
- 不用聊天时间、fact reference 时间或多轮独立查询拼成趋势；不跨数量口径连线，不跨缺失观察点连线。
- 非法日期、同一时点相互冲突的数据、时间顺序无法可靠表达时隐藏对应序列；不插值、不预测。
- **当前 R19 真实响应没有这些历史数组，因此浏览器验收中的趋势图按约定隐藏。没有为图表增加后端接口或制造历史。** 折线渲染和门控已用显式合成历史在组件测试中验证，不能据此宣称真实历史业务已上线。

### 精度与原交互

- 标签、提示、表格继续保留后端 Decimal 字符串原文；仅绘图坐标使用 Number，拒绝非有限、溢出及非零下溢值。
- 按 Decimal 原文排序缺口，避免大数被 Number 舍入后错序；极小/极大坐标轴使用科学计数显示，原始事实标签不改写。
- 图表从当前所选结果派生；加载中、业务错误、非法输入时不保留上一条图表。
- 原 API 客户端、会话存储、产品切换、来源入口、库存执行事件及幂等/权限控制不变。没有新增图表请求。
- 图表区可整体收起；长物料集合横向滚动、长缺料列表纵向滚动，并沿用主题变量和响应式布局。

## 4. 实际文件清单

修改3个既有文件：

1. `frontend/src/components/ResultPanel.vue`：27行接入代码及独立样式引用。
2. `frontend/src/services/responseAdapter.ts`：增加4行，显示后端已有归一化库存和原库存单位。
3. `frontend/package.json`：仅调整测试脚本。

新增8个文件：

1. `frontend/src/components/InventoryGapChart.vue`
2. `frontend/src/components/ShortageBarChart.vue`
3. `frontend/src/components/InventoryTrendChart.vue`
4. `frontend/src/services/analysisChart.ts`
5. `frontend/src/chart.css`
6. `frontend/tests/chart.test.mjs`
7. `frontend/tests/chart_integration.test.mjs`
8. `docs/round19_frontend_chart_integration.md`

## 5. 依赖与测试入口

- 无新增、升级或安装 npm 包。`npm ls --depth=0` 正常；已安装直接依赖与 R19 锁文件一致，锁文件无变化。
- 原 `test:unit` 引用了当前目录不存在的4个测试文件；原 `npm test` 指向 Playwright，但目标目录没有对应 Playwright spec/config。
- 本轮将 `npm test` 接到可执行的 Node 测试集合，包含原有 `inventory_confirmation.test.mjs`、`session_execution_isolation.test.mjs` 和新增两个图表测试。原有36项安全测试未修改、未删除、未弱化。
- `test:e2e` 原 Playwright 命令保留；本轮实际页面验收使用独立 agent-browser 会话，不将其描述为已运行 Playwright suite。

## 6. 自动化实际结果

| 检查 | 结果 |
| --- | --- |
| R19 全量后端 `pytest tests` | **363 passed，0 failed，0 skipped**；3项第三方弃用警告 |
| `npm test` | **97 passed，0 failed，0 skipped**；原36项 + 新61项 |
| `npm run typecheck` | 通过 |
| `npm run build -- --outDir <外部验收目录>/web-standard --emptyOutDir false` | 通过，480模块；标准构建入口包含类型检查 |
| Vite runner 外部构建 | 通过；与标准构建输出相同 JS/CSS 内容标识 |
| `git diff --check` | 通过 |
| 411个数据/后端文件 SHA-256 | 全部不变 |

后端使用目标项目 `.venv/Scripts/python.exe`，命令为：

```powershell
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider --basetemp <外部临时目录>/pytest -q --tb=short tests
```

新增测试覆盖真实 adapter 与组件的联动、后端归一化库存、Decimal精度、null与0、单位冲突、跨产品、历史包装/证据复用、无趋势不造趋势、异常日期、请求失败/加载中门控，以及原库存确认与会话安全契约。

## 7. 真实 HTTP 与生产构建浏览器验收

复用 R19 既有 `tests/e2e/serve.py --mode public`，绑定 `127.0.0.1:18771`。前端使用本轮生产构建；运行数据库和会话均放在项目外部验收目录，通过原初始化流程复制运行数据。未操作企业原库，没有执行真实库存扣减。

记录了14次自然语言输入，其中13次实际 POST `/api/v1/agent/query`，1次产品目录由原 `/resolve` 路径直接完成。业务 API 均请求本地真实 R19 服务，没有 Mock、路由拦截或静态答案。

| 页面操作 | 实际结果 |
| --- | --- |
| `Z112生产100片，哪些物料不足？` | KQ-Z112，34项需求，3项后端缺口；图表34项/3项，与表格一致 |
| `BHX2588生产100片可以吗？` | 正确定位 KQ-BHX3588M，91项需求，5项缺口 |
| `查询3588的BOM` | 91项BOM，隐藏生产图表 |
| `看看库存` | BHX库存查询，91行明细，隐藏生产图表；没有伪造趋势 |
| `查询物料MAT-004库存` | 实际库存1308片，1条记录，原查询与来源保留 |
| `KQ-BHX3588M最多可以生产多少片` | 原后端给出58项有记录物料上限38片及范围说明；没有带出生产图表 |
| `生产0片`、`生产100公斤` | 原 `invalid_query`，没有旧图表和旧表格残留 |
| `Z112生产1片可以吗` | 34项满足，34项对比，无缺料条形；切换产品不串结果 |
| `为什么` | 复用原会话证据，34项结果相符，未重算业务 |
| `有哪些已发布产品` | 原 R19 两产品目录，隐藏生产图表 |
| `KQ-Z112生产100片可以吗` | 本次100片、3项不足；刷新后结果与图表一致 |
| `BHX3588生产100片可以吗` | 最终资源版本加载成功，91项对比、33项无记录空槽、5项缺口 |
| `帮我生成采购建议` | 沿用BHX本次100片，5项采购明细；原采购路径不受影响，无生产图表 |

额外核验：

- 从100片回答选择旧的1片回答：图表仍为34项，缺口变为0，不沿用3项缺口。
- 切换BHX历史：最新非法单位回答不显示旧图；主动选择历史100片回答恢复91项/5项缺口。
- 刷新最新合法回答与最新非法回答均与对应原会话结果一致。
- 对最终BHX生产响应逐项比较 HTTP、SVG提示和表格：**91项需求、58项可核对库存、33项空库存及5项缺口全部一致，0差异**。
- 图表收起时结果区从约1026px降至510px，原物料列表和来源入口保留。
- 浅色/深色正常；390px窗口页面宽度390px，图表318px容器内滚动1502px内容，无整页横向溢出。
- 实际网络记录发现本机安全软件注入的一个 Kaspersky GET 脚本请求被阻止（状态0、无请求体）；构建HTML没有该脚本，图表代码不包含网络调用。不能将本机注入脚本误归因于项目新增依赖。

基线特别说明：本次目标 R19 的产品目录只有 KQ-Z112 与 KQ-BHX3588M；不是旧开发版本的三产品环境。当前 R19 测试的产量边界为 Z112 54片、BHX已核算物料38片。本轮没有恢复Demo、替换数据或采用旧版本1片/75台等常量。

## 8. 验证边界与未做事项

- 真实多时点库存趋势无数据基础，按条件隐藏；仅测试其渲染与安全门控。
- 请求失败、服务错误和加载中由集成测试验证；没有为了测试而破坏或改写真实API。
- 库存执行的事务、权限和回执由363项原后端及36项原前端安全回归覆盖；浏览器没有执行企业库存写入。
- 未更改后端、BOM、库存、计算算法、API契约、部署配置、锁文件或环境密钥。没有公网服务和部署操作。

测试日志、生产构建、HAR、截图、清单和SHA校验记录保存在本机项目外部的 `r19-charts-20261031` 验收目录，未加入项目。临时浏览器与验收服务在验证完成后关闭。

## 9. 回滚方法（仅说明，未执行）

原 `release/round19-20261025` 和 HEAD 均保留。先导出本轮差异并保存这8个新增文件；经人工确认后，仅将第4节3个既有前端文件恢复到 `5c56c53` 内容，将第4节8个新增文件移到独立存档。不要执行整仓 reset、clean，不触碰既存未跟踪文件、后端、数据库或数据目录。随后重跑原安全测试和前端构建。

本轮暂存区为空，没有自动commit、push或部署。
