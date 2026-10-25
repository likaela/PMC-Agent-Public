# PMC-Agent

制造业智能计划助手。面向PMC计划员、生产协调人员与采购负责人，通过自然语言查询BOM和库存、核对生产物料需求，并解释实际不足数量及其来源。

## 系统架构

```text
Frontend（Vue）
       ↓ 同源 /api
Nginx → API（FastAPI）
       ↓
Agent：产品上下文 → 工具选择 → Evidence → 回答
       ↓
Business Service：BOM / 库存 / MRP / 采购
       ↓
Data Layer：data/public
```

| 目录 | 内容 |
|---|---|
| src/backend/ | Agent、API、确定性Service、Repository及数据库迁移 |
| frontend/ | Vue3 / TypeScript / Vite工作台 |
| data/public/ | 授权公开的产品、BOM、库存和演示数据 |
| docker/、deploy/ | 容器说明、Nginx和systemd配置 |
| scripts/ | 跨平台启动、安装、健康检查及HTTP验收 |
| docs/ | 使用说明、Ubuntu部署和数据展示规范 |

## 功能介绍

- 产品目录、BOM版本、物料组成及单耗查询。
- 按合法正整数生产数量计算需求，核对可验证的库存记录。
- 物料满足判断、实际缺口、限制物料及最大满足数量。
- 有事实与参数支持的采购建议，包括MOQ、包装倍数和成本。
- 连续追问、物料明细、来源追溯及历史会话恢复。

## 扩展问答能力

面向评委与使用者的增强问答层（`src/backend/services/pmc_capability_service.py`，确定性计算，不改界面风格与前端主代码）：

1. **产品名称展示与自动联想**：产品下拉与目录附带中文名称（KQ-Z112=飞腾八核核心板，KQ-BHX3588M=瑞芯微核心模块，DEMO-PMC-01=演示用例）；输入简称即可命中产品，如"查询Z112"→KQ-Z112，"查询3588"→KQ-BHX3588M，别名见 `data/public/products/product_names.json`。
2. **脱离产品的物料独立查询**：不选产品也可直接按物料编码/展示别名查询库存数量、批次（先进先出展示）与"是否缺料"（账面库存×各产品BOM单耗覆盖）；支持"多少个/数量是多少/还剩多少/结存多少"等数量问法，也支持按物料中文名称（如"贴片电阻"）查询，同名多个料号分别列示、数量不合并。
3. **缺料/急刻意图识别**：提问"哪种物料急缺/缺料/缺口"时返回具体缺料清单、需求/账面/缺口数量与缺料分析结论；未给投产数量时按 BOM 单耗给出急缺（不足1件）与最紧张物料排序及限制物料。
4. **计划类问答**：
   - "库存一共多少种物料"→整个库存表（主库存表）按料号去重的物料种类数，附记录条数、批次数与库位数；会话已绑定产品时仍返回全库存口径，明确写出产品（如"KQ-Z112 库存有多少种物料"）时才按该产品库存口径回答；
   - "支持的几个BOM，各自有多少元器件种类"→逐产品BOM版本与元器件种类数；
   - "投产N件"→自动核对库存是否足够，不足时自动生成需要采购的BOM表单并标明建议采购数量；
   - "投产N件并扣减库存，更新库存文件"→按先进先出（批次日期靠前优先）扣减账面库存并导出更新后的库存文件；
   - 同一物料多批次严格按批次日期靠前优先消耗与展示。

已发布数据带 SHA-256 完整性校验且保持只读：投产扣减以 `data/runtime/inventory_adjustments.json` 扣减流水生效，所有后续查询自动按扣减后账面数量计算，并在 `data/runtime/inventory_updates/` 导出更新后的库存文件副本。缺记录物料不补零，采购数量按缺口列示，未取得权威MOQ/包装倍数/价格参数时不作估算。

业务数字来自Python Tool/Service，不由大模型生成；没有库存记录不补零，重复待核验记录不盲目汇总。界面只展示当前问题相关的有效指标，明细和来源按需展开。

## 产品案例

| 产品 | BOM | 推荐操作 |
|---|---|---|
| KQ-Z112 | V01-C34，34种物料，片 | 54片满足、55片不足，最大满足54片 |
| KQ-BHX3588M | V01，91种物料，片 | BOM、库存、需求及限制物料 |
| DEMO-PMC-01（演示产品） | V01，多层BOM，台 | 50/75台满足、76/100台不足、采购建议 |

KQ-Z112的完整业务数据为34种物料。KQ-BHX3588M库存匹配58项，其中55项可核算，已核算物料产量上限38片；仍有36项无库存记录或待核验，缺失记录保持缺失，不伪造库存。

> 测试数据说明：当前库存表已按测试口径统一处理（所有物料账面数量 +1000 pcs），用于齐套/缺料/采购演示；正式发布前可从原始Excel重新导入或 `git checkout data/public` 恢复原始库存。

全部生产输入随仓库放在data/public：产品目录、BOM、库存、aliases、manifest、无会话的事实种子以及演示事实包。文件SHA-256用于完整性检查。无需额外复制Excel、确认或本机配置。

运行时会话存入自动创建的data/runtime，不能提交Git。物料数量核对不代替工单审批，采购建议不代表已到货。

## 本地运行

需要Python3.12、Node.js22.12以上。无需PostgreSQL或模型API Key。替换以下示例URL中的YOUR_ACCOUNT为实际发布账号。

### Windows

```powershell
git clone https://github.com/YOUR_ACCOUNT/PMC-Agent-Public.git
cd PMC-Agent-Public
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\venv\Scripts\python.exe -m scripts.run_public
```

另开终端，在项目根目录执行：

```powershell
cd frontend
npm ci
npm run dev
```

访问http://127.0.0.1:5173。

### Linux

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m scripts.run_public
```

另开终端执行 `cd frontend && npm ci && npm run dev`。Ubuntu22.04优先使用Docker，避免主机Python版本不合格。

## Docker部署

```bash
git clone https://github.com/YOUR_ACCOUNT/PMC-Agent-Public.git
cd PMC-Agent-Public
docker compose up -d --build
```

访问http://127.0.0.1:8080。Compose自动构建Python后端与Vue/Nginx；后端不映射主机端口。停止用 `docker compose stop`，会话命名卷保留；不要执行down -v删除运行历史。

```bash
docker compose ps
bash scripts/check_health.sh http://127.0.0.1:8080
docker compose exec backend python -m scripts.verify_deployment_http --base-url http://127.0.0.1:8000
```

## Ubuntu部署

支持Ubuntu22.04/24.04，推荐2核CPU、4GB内存、20GB磁盘。Docker方式：

```bash
bash scripts/install_ubuntu.sh docker
sudo docker compose up -d --build
```

Ubuntu24.04也支持Python3.12、Node22、Nginx与systemd原生部署。详细安装、服务器IP访问、服务启停与排错见[Ubuntu部署说明](docs/ubuntu_deployment.md)。

默认只监听本机。管理员可使用SSH转发或限制来源IP访问；多人公网前必须配置认证、TLS和会话所有权控制。基础API不具备逐用户历史访问控制，不能作为匿名私人对话平台直接开放。

## 使用示例

1. 有哪些已发布产品？
2. 查询KQ-Z112的BOM → 查看库存。
3. 生产1片可以吗？ → 生产2片呢？ → 为什么不足？
4. 最多可以生产多少片？ → 哪些物料限制产量？
5. 查询KQ-BHX3588M的BOM → 生产10片需要哪些物料？
6. 选择DEMO-PMC-01 → 生产50台 → 生产100台 → 生成采购建议。
7. 查看来源，继续追问，刷新或选择历史回答。

"智能查询"默认折叠，展开可选择问题。产品切换后使用对应库存，不串用产量。详细操作见[使用说明](docs/reviewer_remote_guide.md)。

## 配置与安全

.env.example提供安全默认值。DATA_ROOT默认data，PMC_RUNTIME_ROOT可指定可写运行目录，HOST/PORT默认127.0.0.1:8000。生产前端构建使用VITE_API_BASE_URL=/，Nginx同源代理/api。

公开数据包含获授权的业务数量和来源信息，不宣称匿名化。生产入口禁用第三方模型与外部追踪，不需要DeepSeek Key。不得提交.env、用户密码、Token、运行数据库和日志。

API：GET /health、GET /api/v1/agent/catalog、POST /api/v1/agent/sessions、POST /api/v1/agent/query、GET /api/v1/agent/sessions/{session_id}。

```json
{"message":"查询KQ-Z112的BOM","persist_session":true}
```

后续请求附session_id，响应保留answer、structured_data、provenance与warnings。界面自动处理产品和演示数据空间绑定。

## 验证

```bash
pip install -r requirements-dev.txt
python -m pytest
python -m scripts.audit_public_release
python -m scripts.check_public_release_source
```

前端运行 `npm run typecheck`、`npm run build`；三产品实际HTTP运行 `python -m scripts.verify_deployment_http --base-url http://127.0.0.1:8080`。数据缺失或校验失败明确报错，不返回本地假响应。

发布验证范围与尚待执行的Linux实机检查见[验证记录](docs/release_validation.md)。
