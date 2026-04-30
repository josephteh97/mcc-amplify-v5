#!/bin/bash
# ============================================================
# Amplify AI — Start backend + frontend with a single command
# Usage:  ./scripts/run.sh      (from project root)
#         ./run.sh              (if you add a symlink at root)
# ============================================================

# Colour helpers
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"
FRONTEND_DIR="$PROJECT_ROOT/frontend"

# ── Load .env if present ──────────────────────────────────────────────────────
if [[ -f "$PROJECT_ROOT/.env" ]]; then
    set -a; source "$PROJECT_ROOT/.env"; set +a
fi
REVIT_SERVER="${WINDOWS_REVIT_SERVER:-http://localhost:5000}"
REVIT_KEY="${REVIT_SERVER_API_KEY:-}"

# =============================================================================
# MODE: --status  — print a live connection table and exit
# =============================================================================
if [[ "${1:-}" == "--status" ]]; then
    echo ""
    echo -e "${BOLD}${CYAN}  Amplify AI — Service Status${NC}"
    echo -e "  $(date '+%Y-%m-%d %H:%M:%S')"
    echo ""
    printf "  %-28s %-10s %s\n" "Service" "Status" "Detail"
    printf "  %-28s %-10s %s\n" "-------" "------" "------"

    _check() {
        local label="$1" url="$2" extra_args="${3:-}"
        local result
        result=$(curl -sf --max-time 3 $extra_args "$url" 2>/dev/null)
        local rc=$?
        if [[ $rc -eq 0 ]]; then
            # Extract key field from JSON if present
            local detail
            detail=$(echo "$result" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    print(d.get('status') or d.get('message') or d.get('revit_initialized','') or 'ok')
except:
    print('ok')
" 2>/dev/null || echo "ok")
            printf "  ${GREEN}%-28s %-10s${NC} %s\n" "$label" "ONLINE" "$detail"
        else
            printf "  ${RED}%-28s %-10s${NC} %s\n" "$label" "OFFLINE" "$url"
        fi
    }

    _check "Backend API"            "http://localhost:8000/"

    # Revit server — include API key header
    REVIT_RESULT=$(curl -sf --max-time 3 -H "X-API-Key: ${REVIT_KEY}" "${REVIT_SERVER}/health" 2>/dev/null)
    if [[ $? -eq 0 ]]; then
        REV_INIT=$(echo "$REVIT_RESULT" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    init=d.get('revit_initialized',False)
    status=d.get('status','unknown')
    print(f\"{status} | revit_initialized={init}\")
except:
    print('ok')
" 2>/dev/null || echo "ok")
        printf "  ${GREEN}%-28s %-10s${NC} %s\n" "Revit server (${REVIT_SERVER})" "ONLINE" "$REV_INIT"
    else
        printf "  ${RED}%-28s %-10s${NC} %s\n" "Revit server (${REVIT_SERVER})" "OFFLINE" "Run build.bat on Windows"
    fi

    # Frontend (Vite)
    _check "Frontend (Vite)"        "http://localhost:5173"

    # Ollama
    if command -v ollama &>/dev/null; then
        OLLAMA_MODELS=$(ollama list 2>/dev/null | tail -n +2 | awk '{print $1}' | tr '\n' ' ')
        printf "  ${GREEN}%-28s %-10s${NC} %s\n" "Ollama" "RUNNING" "${OLLAMA_MODELS:-no models}"
    else
        printf "  ${RED}%-28s %-10s${NC}\n" "Ollama" "NOT FOUND"
    fi

    # .env
    if [[ -f "$PROJECT_ROOT/.env" ]]; then
        KEY_PREVIEW="${REVIT_KEY:0:6}…"
        printf "  ${GREEN}%-28s %-10s${NC} WINDOWS_REVIT_SERVER=%s  KEY=%s\n" ".env file" "LOADED" "$REVIT_SERVER" "$KEY_PREVIEW"
    else
        printf "  ${YELLOW}%-28s %-10s${NC} %s\n" ".env file" "MISSING" "defaults in use"
    fi

    echo ""
    exit 0
fi

echo -e "${BOLD}${CYAN}"
echo "  ╔══════════════════════════════════════╗"
echo "  ║       🏗️  Amplify AI System           ║"
echo "  ║   Floor Plan → 3D BIM (RVT + glTF)  ║"
echo "  ╚══════════════════════════════════════╝"
echo -e "${NC}"

# ── Pre-flight checks ──────────────────────────────────────────────────────────

# Check Python
if ! command -v python3 &>/dev/null && ! command -v python &>/dev/null; then
    echo -e "${RED}✗ Python not found. Install Python 3.9+${NC}"
    exit 1
fi
PYTHON=$(command -v python3 || command -v python)

# Check Node / npm
if ! command -v npm &>/dev/null; then
    echo -e "${RED}✗ npm not found. Install Node.js 18+${NC}"
    exit 1
fi

# Install frontend dependencies if missing
if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    echo -e "${YELLOW}⚙  Installing frontend dependencies (first run)…${NC}"
    cd "$FRONTEND_DIR" && npm install --silent
    echo -e "${GREEN}✓ Frontend dependencies installed${NC}"
fi

# ── Kill both child processes on Ctrl+C ───────────────────────────────────────
cleanup() {
    echo -e "\n${YELLOW}Shutting down Amplify AI…${NC}"
    [ -n "$BACKEND_PID" ]  && kill "$BACKEND_PID"  2>/dev/null
    [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null
    wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
    echo -e "${GREEN}Goodbye.${NC}"
    exit 0
}
trap cleanup SIGINT SIGTERM

# ── Probe Revit server before booting backend ────────────────────────────────
# The pipeline emits transaction.json + GLTF locally regardless; the Revit
# build step is only attempted when WINDOWS_REVIT_SERVER is set AND the
# server answers /health at boot. Print the wiring decision so the user
# knows whether RVT files will be produced this session.
if [[ "${REVIT_AUTOBUILD:-1}" =~ ^(0|false|no)$ ]]; then
    echo -e "${YELLOW}⚙  Revit auto-build DISABLED (REVIT_AUTOBUILD=$REVIT_AUTOBUILD)${NC}"
    echo -e "   Recipes will be written to output/<storey>_transaction.json only."
elif [[ -n "$WINDOWS_REVIT_SERVER" ]]; then
    if curl -sf --max-time 3 -H "X-API-Key: ${REVIT_KEY}" \
           "${WINDOWS_REVIT_SERVER}/health" >/dev/null 2>&1; then
        echo -e "${GREEN}▶  Revit server   →  ${WINDOWS_REVIT_SERVER}  (ONLINE)${NC}"
    else
        echo -e "${YELLOW}⚠  Revit server   →  ${WINDOWS_REVIT_SERVER}  (OFFLINE — recipes still emit; RVT build will be skipped per job)${NC}"
    fi
elif [[ "${REVIT_MODE:-http}" == "file" && -n "$REVIT_SHARED_DIR" ]]; then
    echo -e "${GREEN}▶  Revit file-drop →  ${REVIT_SHARED_DIR}${NC}"
else
    echo -e "${YELLOW}⚙  Revit not configured (no WINDOWS_REVIT_SERVER or REVIT_MODE=file)${NC}"
    echo -e "   Set them in .env to have the pipeline POST to your Windows add-in."
    echo -e "   Recipes still land in output/<storey>_transaction.json for manual build."
fi

# ── Start Backend ─────────────────────────────────────────────────────────────
echo -e "${GREEN}▶  Starting backend  →  http://localhost:8000${NC}"
cd "$PROJECT_ROOT"

# Activate virtualenv if present (project-root or backend/ both supported)
if [ -d "$PROJECT_ROOT/venv" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/venv/bin/activate"
elif [ -d "$BACKEND_DIR/venv" ]; then
    # shellcheck disable=SC1091
    source "$BACKEND_DIR/venv/bin/activate"
fi

# v5.3 entrypoint is backend.api.app:app (FastAPI lifespan-managed); run
# uvicorn directly from the project root so the backend.* package is on
# the import path. PYTHONPATH=. makes the spawn explicit even if the
# user is using a venv that doesn't have the project installed in
# editable mode.
PYTHONPATH="$PROJECT_ROOT" "$PYTHON" -m uvicorn backend.api.app:app \
    --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# Wait for the backend to actually be ready before launching the frontend
echo -e "${YELLOW}⏳ Waiting for backend to be ready…${NC}"
for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/health >/dev/null 2>&1 || \
       curl -sf http://localhost:8000/api/health >/dev/null 2>&1 || \
       curl -sf http://localhost:8000/ >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
sleep 1  # one extra second for WS listeners to settle

# ── Start Frontend ────────────────────────────────────────────────────────────
echo -e "${GREEN}▶  Starting frontend →  http://localhost:5173${NC}"
cd "$FRONTEND_DIR"
npm run dev &
FRONTEND_PID=$!

# ── Ready ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}  ✓ Amplify AI is running${NC}"
echo -e "${GREEN}    Frontend:  http://localhost:5173${NC}"
echo -e "${GREEN}    Backend:   http://localhost:8000${NC}"
echo -e "${GREEN}    API docs:  http://localhost:8000/api/docs${NC}"
echo -e "${YELLOW}    Press Ctrl+C to stop both services.${NC}"
echo ""

# Wait for either process to exit
wait "$BACKEND_PID" "$FRONTEND_PID"
