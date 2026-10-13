# Ubuntu轻量服务器部署PMC-Agent

## 1. 服务器与软件要求

推荐 Ubuntu **22.04或24.04**，最低 **2核CPU、4GB RAM、20GB磁盘**。第一次构建还需要网络下载Python/Node依赖和容器镜像；建议预留额外磁盘。

- **推荐Docker**：同时支持22.04/24.04，镜像内使用Python3.12和Node22，不受主机旧版本影响。
- **原生部署**：本说明采用Ubuntu24.04、Python3.12、Node22.12以上、Nginx、systemd。
- Ubuntu22.04默认Python3.10、发行版Node可能偏旧，不直接用于此项目；请选择Docker，而不是改动业务代码适配旧环境。

GitHub仓库提供所有必要业务数据，禁止另行依赖开发电脑目录。KQ-Z112的完整业务BOM为34种物料；KQ-BHX3588M为91种；DEMO-PMC-01为独立演示产品。

## 2. 连接服务器与安装基础工具

在Windows终端或其他电脑上执行（替换用户名/IP）：

```bash
ssh ubuntu@SERVER_IP
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip nginx nodejs npm curl ca-certificates
python3 --version
node --version
npm --version
```

上面的发行版工具适合作为基础环境，但**安装成功不等于版本合格**。原生方案必须Python3.12和Node22.12以上；Node过旧时按第5节升级。Docker方案不使用主机Python/Node运行应用，主机Nginx也不必启用。

不要把8000端口加入公网安全组。先在本机验证，之后只开放前端HTTP/HTTPS入口并限制来源IP。

## 3. 拉取项目

Docker可以克隆到自己的目录：

```bash
git clone https://github.com/YOUR_ACCOUNT/PMC-Agent-Public.git
cd PMC-Agent-Public
cp .env.example .env
```

请将YOUR_ACCOUNT替换为负责人发布的GitHub账号；仓库尚未上传时不要使用示例URL。其他Git Server使用实际仓库地址。部署前确认使用的发布提交包含根目录Dockerfile、docker-compose.yml、data/public/release.json；缺少这些文件时，不使用其他资料手工补齐。

不需要手工复制任何企业原件、数据库、确认或隐藏配置。`.env`在服务器上生成，不提交Git。

## 4. 推荐：Docker一键部署

### 安装Docker

```bash
bash scripts/install_ubuntu.sh docker
sudo docker version
sudo docker compose version
```

脚本使用[Docker官方Ubuntu安装来源](https://docs.docker.com/engine/install/ubuntu/)，不运行来历不明的`curl | sh`。若存在冲突的docker.io安装，脚本停止，不自动卸载系统软件。请按官方说明处理后重试。

### 构建并启动

```bash
sudo docker compose up -d --build
sudo docker compose ps
bash scripts/check_health.sh http://127.0.0.1:8080
```

已经构建的环境只需：

```bash
sudo docker compose up -d
```

两个服务：

- `backend`：FastAPI，读取公开业务包，创建独立运行库；不发布主机8000端口。
- `frontend`：Vue生产构建＋Nginx，静态页面与`/api`统一入口。

默认监听**127.0.0.1:8080**。后端健康检查通过后才启动前端；首次初始化和构建可能需要几分钟。

### 安全地从自己电脑访问

先在自己电脑开启SSH转发：

```bash
ssh -L 8080:127.0.0.1:8080 ubuntu@SERVER_IP
```

浏览器访问 `http://127.0.0.1:8080`。这不需要开放8080公网安全组。

### 使用服务器IP访问

由管理员确认访问范围，在服务器`.env`设置：

```ini
PMC_BIND_IP=0.0.0.0
PMC_HTTP_PORT=80
```

```bash
sudo docker compose up -d
```

浏览器访问 **`http://SERVER_IP`**。若主机Nginx正在占用80，请选择其他端口（例如8080），或由管理员停止专属于此部署的主机Nginx；不要终止其他业务服务。云安全组与防火墙应先仅允许指定测试者IP。

Docker端口映射可能绕过部分UFW规则，不能仅靠UFW宣称限制生效；检查云安全组及Docker网络规则，参见官方安装页的防火墙说明。

### 停止、日志与升级

```bash
sudo docker compose logs --tail=100 backend frontend
sudo docker compose stop
sudo docker compose up -d
```

会话保存在`pmc_runtime`命名卷，停止容器不会删除。**不要执行`docker compose down -v`，否则会删除运行历史。**升级前停服务、备份卷，再拉取并重建镜像。新业务数据不应重解释旧Evidence；会话版本不匹配时新建分析。

## 5. 原生部署：Ubuntu24.04

使用普通具有sudo权限的SSH用户。按配置约定，项目固定安装到`/opt/PMC-Agent`：

```bash
sudo install -d -o "$USER" -g "$(id -gn)" /opt/PMC-Agent
git clone https://github.com/YOUR_ACCOUNT/PMC-Agent-Public.git /opt/PMC-Agent
cd /opt/PMC-Agent
```

### Python后端

```bash
python3 --version
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -m scripts.run_public --init-only
```

必须是Python3.12。初始化会校验SHA-256并创建运行库；没有新事实导入或额外企业资料准备步骤。

手动调试后端：

```bash
python -m scripts.run_public
```

该生产入口使用`api.public_app:app`。默认127.0.0.1:8000，数据缺失时报错，不回退其他数据源。完成手动调试后按Ctrl+C停止，避免与systemd抢占8000。

### Node与前端

使用[Node官方发行版](https://nodejs.org/en/download/archive/v22.22.0)。如果第2节安装了旧Node，先通过受控方式安装新版本；可用以下命令下载并校验官方Linux包（x86_64用x64、ARM64用arm64）：

```bash
NODE_VERSION=22.22.0
NODE_ARCH=x64
NODE_FILE="node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz"
NODE_TEMP="$(mktemp -d -t pmc-node-XXXXXXXX)"
curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/${NODE_FILE}" -o "$NODE_TEMP/$NODE_FILE"
curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" -o "$NODE_TEMP/SHASUMS256.txt"
(cd "$NODE_TEMP"; grep " ${NODE_FILE}\$" SHASUMS256.txt | sha256sum -c -)
sudo tar -xJf "$NODE_TEMP/$NODE_FILE" -C /usr/local --strip-components=1
hash -r
node --version
npm --version
```

必须先看到校验`OK`再执行解压；不要使用Windows二进制。现有Node已合格则跳过这一步。

正式前端目录是 **`frontend/`**：

```bash
cd /opt/PMC-Agent/frontend
npm ci
VITE_API_BASE_URL=/ npm run build
```

`npm ci`使用已提交lockfile，比临时解析版本的`npm install`可复现。构建产生当前目录`dist/`；Nginx只提供dist内容，不提供整个项目。

### 自动安装原生服务

在全新、专用于本项目的服务器上，Ubuntu默认Nginx站点会占用默认入口。仅当确认没有其他业务时，取消这个**默认站点链接**（不删除其配置）：

```bash
ls -l /etc/nginx/sites-enabled/
sudo unlink /etc/nginx/sites-enabled/default
cd /opt/PMC-Agent
bash scripts/install_ubuntu.sh native
```

如默认链接不存在，不执行unlink。如服务器已有其他网站，让管理员配置专属server_name，不覆盖或关闭其他站点。安装脚本创建无登录shell的pmc-agent服务账号、校验公开数据、构建前端并安装以下文件：

- `deploy/nginx/pmc-agent.conf` → `/etc/nginx/sites-available/pmc-agent`。
- `deploy/systemd/pmc-agent.service` → `/etc/systemd/system/pmc-agent.service`。
- 前端dist内容 → `/var/www/pmc-agent/`。

脚本不擅自删除现有Nginx站点。已有Node太旧时明确退出，不掩盖不兼容。

### 启动与运维

```bash
bash scripts/start_production.sh native
sudo systemctl start pmc-agent
sudo systemctl enable pmc-agent
sudo systemctl status pmc-agent
sudo nginx -t
bash scripts/check_health.sh http://127.0.0.1
```

浏览器访问`http://SERVER_IP`，需安全组允许前端80端口。systemd以普通服务账号运行，仅允许写`data/runtime/`；业务包只读。

```bash
sudo systemctl restart pmc-agent
sudo systemctl stop pmc-agent
sudo journalctl -u pmc-agent --since today
```

日志可能含用户输入，不公开上传。修改前端后重新构建、复制dist并刷新页面；单纯重启后端不会更新静态资源。

## 6. 环境变量

默认`.env.example`无秘密且可运行。主要变量：

- `DATA_ROOT=data`：相对项目根解析，不依赖SSH登录时工作目录。
- `PMC_PUBLIC_DATA_ROOT`：可选，默认data/public；只允许完整、校验通过的交付包。
- `PMC_RUNTIME_ROOT`：可选，默认data/runtime；需运行账号有写权限，不得放入public。
- `HOST=127.0.0.1`、`PORT=8000`：原生后端；systemd固定该端口，与Nginx配置一致。
- `PMC_BIND_IP`、`PMC_HTTP_PORT`：Compose前端监听，默认本机8080。
- `VITE_API_BASE_URL=/`：**构建时**设置；不是浏览器本机的后端地址。

生产入口禁用第三方模型与外部追踪，无需填写DeepSeek Key。公开业务数据不等于授权外部模型接收Evidence。不要将密码或Key写进前端、Compose仓库文件或提交后的.env。

## 7. 部署验收

先健康检查：

```bash
bash scripts/check_health.sh http://127.0.0.1:8080
```

原生Nginx将地址换成`http://127.0.0.1`。要执行完整三产品真实HTTP检查，可在项目根目录使用已安装的Python环境：

```bash
python -m scripts.verify_deployment_http --base-url http://127.0.0.1:8080
```

Docker服务器未安装项目Python依赖时，可直接在后端容器验收：

```bash
sudo docker compose exec backend python -m scripts.verify_deployment_http --base-url http://127.0.0.1:8000
```

该脚本创建正常会话、调用真实Tool/Service、核对历史，不发布新数据。另用浏览器确认页面、物料表、来源与当前回答对应：

| 测试 | 预期 |
|---|---|
| 有哪些已发布产品？ | 三个产品 |
| 查询KQ-Z112的BOM | 34项，V01-C34 |
| 生产1片KQ-Z112可以吗？ | 满足 |
| 生产2片呢？ | 不足 |
| 最多生产多少？ | 当前产品最大满足1片 |
| 查询KQ-BHX3588M的BOM | 91项 |
| 生产10片需要哪些物料？ | 需求、已核验库存与不足明细；未知库存不补零 |
| 最多可以生产多少片？ | 按已核验库存计算的上限0片与限制物料 |
| 选择DEMO-PMC-01后生产50/75台 | 满足 |
| 生产76/100台 | 不足 |
| 最多生产多少台？ | 75台 |
| 生成采购建议 | 返回有事实支持的建议 |
| 生产0片 | 合法的输入错误提示，不触发库存异常 |
| 刷新、恢复历史、切换产品 | 回答和物料结果同步，不串用数据 |

## 8. 安全与访问控制

Nginx不提供原Excel、数据库、.env或项目目录下载；API不接受其他数据空间。不要直接暴露后端端口。

基础生产入口没有逐用户账号所有权校验。**授权数据可以公开分发，不代表用户对话可以公开。**现阶段用SSH转发或仅限指定IP；多人公网试用须配置认证、TLS、服务器独立账号及会话所有权验收。基础Compose启动成功不能视为多用户安全验收通过。

开启HTTPS需要域名/证书和受控反向代理，由部署管理员配置。本任务不自动创建公网隧道。不要把Git忽略的运行历史、部署私有账号配置或日志上传GitHub。

## 9. 排错

- **端口占用**：`sudo ss -lntp`，检查80/8080/8000；不要盲目kill其他程序。
- **Nginx欢迎页**：默认站点仍抢占入口，检查sites-enabled与server_name。
- **前端请求127.0.0.1:8000**：构建遗漏`VITE_API_BASE_URL=/`，重建并更新dist。
- **公开数据校验失败**：运行`python -m scripts.audit_public_release`，重新获取同一提交数据；不要修改hash来掩盖错误。
- **运行目录无权限**：服务账号需要runtime写权限，不应给整个项目777。
- **模型Key为空**：不影响生产入口；确定性回答正常工作。
- **依赖安装失败**：确认Python3.12、Node22，检查网络和官方镜像；不复制Windows venv到Linux。
- **会话恢复失败**：确认命名卷/runtime保留，数据配置变更后按提示新建会话。

## 10. 验证状态

发布验证覆盖Windows迁移副本、真实HTTP、前端测试与生产构建。Ubuntu22.04/24.04的GitHub Actions执行干净checkout、安装、容器构建、页面与API验收。

**Linux实机与Docker运行尚待实际验证**。CI尚未运行不算通过证明。服务器管理员第一次上线必须按第7节实际验收，结果写入交付记录；最新状态见[发布检查记录](release_validation.md)。
