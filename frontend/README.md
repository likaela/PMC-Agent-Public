# PMC-Agent前端

Vue3、TypeScript、Vite工作台。所有业务结果来自Agent API，浏览器不计算库存、缺口、产量或采购数量。

```bash
npm ci
npm run dev
```

需要Node.js22.12以上，默认页面http://127.0.0.1:5173、API http://127.0.0.1:8000。自定义地址使用本目录.env.example，不得在VITE变量中放密钥。

```bash
npm run typecheck
VITE_API_BASE_URL=/ npm run build
```

Windows PowerShell用 `$env:VITE_API_BASE_URL='/'` 设置生产构建变量。生成的dist由Nginx提供，同源/api代理到后端。

参见[根README](../README.md)、[HTTP契约](docs/api-mapping.md)与[使用说明](../docs/reviewer_remote_guide.md)。
