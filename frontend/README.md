# Oncall Agent Frontend

开发态前后端分离，生产态构建到 `../static` 后由 FastAPI 一体托管。

```bash
npm ci
npm run dev      # http://localhost:5173，/api 代理到 http://127.0.0.1:9900
npm run build    # 输出到 ../static
```

默认 API Base 是 `/api`。如果必须直连其他后端，可复制 `.env.example` 为 `.env.local` 并设置：

```bash
VITE_API_BASE_URL=http://127.0.0.1:9900/api
```
