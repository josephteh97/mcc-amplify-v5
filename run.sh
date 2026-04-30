#!/usr/bin/env bash
# Boot the v5.3 backend + frontend dev servers in parallel.
# Backend: uvicorn (FastAPI)  on :8000
# Frontend: vite dev          on :5173 (proxies /api → :8000)

set -euo pipefail

cd "$(dirname "$0")"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

cleanup() {
  echo -e "\n${YELLOW}Shutting down…${NC}"
  [[ -n "${UVICORN_PID:-}" ]] && kill "$UVICORN_PID" 2>/dev/null || true
  [[ -n "${VITE_PID:-}"    ]] && kill "$VITE_PID"    2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [[ ! -d frontend/node_modules ]]; then
  echo -e "${YELLOW}Installing frontend deps…${NC}"
  (cd frontend && npm install)
fi

echo -e "${GREEN}Backend  → http://localhost:8000${NC}"
python3 -m uvicorn backend.api.app:app --reload --port 8000 &
UVICORN_PID=$!

echo -e "${GREEN}Frontend → http://localhost:5173${NC}"
(cd frontend && npm run dev) &
VITE_PID=$!

wait
