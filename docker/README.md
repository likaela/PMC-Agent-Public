# Docker部署

根目录Dockerfile包含三个阶段：前端构建、FastAPI后端、Nginx静态服务。Compose构建backend与web目标；业务输入只复制data/public，运行状态写入命名卷，不写入镜像业务数据包。

```bash
docker compose up -d --build
```

默认访问http://127.0.0.1:8080，API不映射主机端口。生产前端采用同源/api。完整安装与运维参见[Ubuntu部署说明](../docs/ubuntu_deployment.md)。
