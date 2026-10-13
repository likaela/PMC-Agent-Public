#!/usr/bin/env bash
# Runs real HTTP checks; never publishes/imports data.
set -euo pipefail
base="${1:-http://127.0.0.1:8080}"
response="$(curl --fail --silent --show-error --retry 15 --retry-connrefused --retry-delay 2 --max-time 15 "$base/health")"
printf '%s\n' "$response"
curl --fail --silent --show-error --max-time 15 "$base/api/v1/agent/catalog"
printf '\n'
curl --fail --silent --show-error --max-time 15 -X POST "$base/api/v1/agent/query" \
  -H 'Content-Type: application/json' \
  --data '{"message":"查询KQ-Z112的BOM","persist_session":true}'
printf '\n'
