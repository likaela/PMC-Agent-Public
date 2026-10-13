# 公开发布验证记录

## 数据与文件范围

- KQ-Z112：V01-C34，34种完整业务物料；1片满足、2片不足、最大满足1片。
- KQ-BHX3588M：V01，91种物料；55项库存可核算，缺失记录不补零。
- DEMO-PMC-01：独立演示产品，最大75台，50/75台满足、76/100台不足，采购建议可用。
- data/public共16个文件，1,146,511字节，逐文件SHA-256校验；事实种子不包含用户会话。
- 只交付源码、运行数据、部署配置和使用说明。不包含开发仓库.git、Excel原件、账号配置、.env、运行数据库、日志、依赖目录或测试缓存。
- 文件清单见[file_manifest.txt](file_manifest.txt)。

## 验证入口

```bash
python -m pytest
python -m scripts.audit_public_release
python -m scripts.check_public_release_source
python -m scripts.verify_deployment_http --base-url http://127.0.0.1:8080
```

发布接口测试通过真实Agent、Tool、Service与公开事实，不Mock业务输出、不调用第三方模型。覆盖三产品目录与BOM、数量边界、最大满足数量、零数量、来源、采购及会话恢复。

Windows独立Python虚拟环境从锁定依赖安装成功，pip check通过，17项发布接口测试通过。前端npm ci、Typecheck和生产Build通过，部署脚本bash -n检查通过。前端交互与业务计算服务保持不变，整理仅涉及目录与启动路径。

## 独立克隆验收

初始化提交后，从本地新仓库clone到独立位置，再安装依赖并启动服务；不复制原开发目录的.env、业务数据库或缓存。验证后端健康、前端页面、KQ-Z112、KQ-BHX3588M、DEMO-PMC-01以及生产边界、采购和恢复。

实际克隆、HTTP和浏览器结果由交付报告记录，不将包含本机路径的运行日志提交源码。

## 尚待执行

本机无可用Docker/Ubuntu环境，Linux实机与容器启动需按[Ubuntu部署说明](ubuntu_deployment.md)另行执行。GitHub Actions已提供Ubuntu22.04/24.04安装、容器和真实HTTP自动验收，未运行CI不代表通过。

默认仅本机访问。多人公网试用前需要认证、TLS及会话所有权控制；本版本不自动开放公网、不提交部署密码、未授权第三方模型出站。
