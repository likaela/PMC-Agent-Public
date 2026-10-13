#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
case "${1:-docker}" in
  docker) docker compose up -d --build; bash scripts/check_health.sh "http://127.0.0.1:${PMC_HTTP_PORT:-8080}" ;;
  native) sudo systemctl enable --now pmc-agent; sudo nginx -t; sudo systemctl reload nginx; bash scripts/check_health.sh http://127.0.0.1 ;;
  stop-docker) docker compose stop ;;
  stop-native) sudo systemctl stop pmc-agent ;;
  *) echo 'Usage: start_production.sh [docker|native|stop-docker|stop-native]' >&2; exit 1 ;;
esac
