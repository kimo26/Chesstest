#!/usr/bin/env bash
# Launch the Chess Coach app (backend + frontend dev server).
#
# This is intentionally dumb: activate the venv, make sure services are
# running, start uvicorn in the background, then `npm run dev` in the
# foreground so Ctrl-C tears both down together.
#
# First launch? Visit http://localhost:5173 — the app detects an empty
# database and redirects you to /onboarding, where you type your
# Chess.com username and watch the data pipelines run with ETAs.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

step() { echo -e "\033[1;34m▶ $*\033[0m"; }
ok()   { echo -e "  \033[32m✓\033[0m $*"; }
warn() { echo -e "  \033[33m!\033[0m $*"; }
have() { command -v "$1" >/dev/null 2>&1; }

if [[ ! -d .venv ]]; then
  warn "No .venv — run ./scripts/setup.sh first"
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# Make sure Ollama is running (optional — chat just won't work without it).
if have ollama && ! pgrep -x ollama >/dev/null 2>&1; then
  step "Starting ollama"
  nohup ollama serve >/tmp/ollama.log 2>&1 &
  sleep 2
fi

# Docker-compose services if they exist.
if [[ -f docker-compose.yml ]] && have docker; then
  if ! docker compose ps --services --status=running 2>/dev/null | grep -q .; then
    step "docker compose up -d"
    docker compose up -d
  fi
fi

API_PORT=${API_PORT:-8000}
WEB_PORT=${WEB_PORT:-5173}

step "uvicorn on :$API_PORT"
uvicorn chess_coach.api.main:app --host 0.0.0.0 --port "$API_PORT" --reload &
API_PID=$!

trap 'kill $API_PID 2>/dev/null || true' EXIT

# Point the frontend at the API.
export VITE_API_URL="http://localhost:$API_PORT"

step "Vite dev server on :$WEB_PORT"
ok  "Open http://localhost:$WEB_PORT — first-run will walk you through onboarding."
(cd frontend && npm run dev -- --port "$WEB_PORT")
