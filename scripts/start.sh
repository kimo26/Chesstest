#!/usr/bin/env bash
# Launch the Chess Coach app. The whole stack runs under Docker Compose:
# postgres (pgvector), redis, ollama, the FastAPI backend, and the Vite
# dev server. This script is a thin wrapper around `docker compose up`.
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
die()  { echo -e "  \033[31m✗\033[0m $*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

if ! have docker; then
  die "Docker is not installed — run ./scripts/setup.sh first."
fi
if ! docker compose version >/dev/null 2>&1; then
  die "'docker compose' plugin missing — run ./scripts/setup.sh first."
fi
if ! docker info >/dev/null 2>&1; then
  die "Cannot talk to the Docker daemon. Start Docker Desktop or open a new shell (if you were just added to the 'docker' group)."
fi

FOLLOW=0
REBUILD=0
for arg in "$@"; do
  case "$arg" in
    -f|--follow)  FOLLOW=1 ;;
    -b|--rebuild) REBUILD=1 ;;
    -h|--help)
      sed -n '2,10p' "$0"
      echo ""
      echo "Usage: ./scripts/start.sh [-f|--follow] [-b|--rebuild]"
      exit 0 ;;
  esac
done

if [[ $REBUILD = 1 ]]; then
  step "docker compose build"
  docker compose build
fi

step "docker compose up -d"
docker compose up -d

ok "Web UI:  http://localhost:5173"
ok "API:    http://localhost:8000"
ok "Ollama: http://localhost:11434"

if [[ $FOLLOW = 1 ]]; then
  step "tailing logs (Ctrl-C to detach — services stay up)"
  docker compose logs -f
fi
