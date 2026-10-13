FROM node:22-bookworm-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN VITE_API_BASE_URL=/ npm run build

FROM python:3.12-slim AS backend
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src/backend/ src/backend/
COPY scripts/ scripts/
COPY data/public/ data/public/
RUN useradd --system --uid 10001 pmc-agent && mkdir -p data/runtime && chown pmc-agent data/runtime
USER pmc-agent
ENV DATA_ROOT=/app/data PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health',timeout=5)"
CMD ["python", "-m", "scripts.run_public", "--host", "0.0.0.0"]

FROM nginx:stable AS web
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY --from=frontend-build /frontend/dist/ /usr/share/nginx/html/
COPY deploy/nginx/docker.conf /etc/nginx/conf.d/default.conf
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s CMD curl -f http://127.0.0.1/health || exit 1
