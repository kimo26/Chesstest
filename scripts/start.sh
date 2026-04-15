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

ASSUME_YES=0
HOST_ACCESS=0
USE_GPU=1
GPU_BACKEND="${GPU_BACKEND:-auto}"
FOLLOW=0
REBUILD=0

while [[ $# -gt 0 ]]; do
  arg="$1"
  case "$arg" in
    -f|--follow)     FOLLOW=1 ;;
    -b|--rebuild)    REBUILD=1 ;;
    --gpu)           USE_GPU=1 ;;
    --cpu)
      USE_GPU=0
      GPU_BACKEND=cpu
      ;;
    --gpu-backend)
      shift
      [[ $# -gt 0 ]] || die "Missing value for --gpu-backend"
      USE_GPU=1
      GPU_BACKEND="$1"
      ;;
    --gpu-backend=*)
      USE_GPU=1
      GPU_BACKEND="${arg#*=}"
      ;;
    --host-access)   HOST_ACCESS=1 ;;
    --yes|-y)        ASSUME_YES=1 ;;
    -h|--help)
      sed -n '2,10p' "$0"
      echo ""
      echo "Usage: ./scripts/start.sh [-f|--follow] [-b|--rebuild] [--gpu] [--cpu] [--gpu-backend <auto|nvidia|rocm|vulkan|cpu>] [--host-access] [--yes]"
      echo "  --gpu          explicitly enable automatic GPU detection (default)"
      echo "  --cpu          skip GPU detection and force CPU mode"
      echo "  --gpu-backend  force one backend: auto, nvidia, rocm, vulkan, cpu"
      echo "  --host-access  publish API/Ollama/Postgres/Redis on localhost ports"
      echo "  --yes          auto-confirm Docker daemon restart during recovery"
      exit 0
      ;;
    *)
      die "Unknown flag: $arg"
      ;;
  esac
  shift
done

if [[ "${CHESS_COACH_TEST_ARGS_ONLY:-0}" = "1" ]]; then
  printf 'USE_GPU=%s\n' "$USE_GPU"
  printf 'GPU_BACKEND=%s\n' "$GPU_BACKEND"
  exit 0
fi

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/lib/compose_common.sh"

if ! have docker; then
  die "Docker is not installed — run ./scripts/setup.sh first."
fi
if ! docker compose version >/dev/null 2>&1; then
  die "'docker compose' plugin missing — run ./scripts/setup.sh first."
fi
if ! docker info >/dev/null 2>&1; then
  die "Cannot talk to the Docker daemon. Start Docker Desktop or open a new shell (if you were just added to the 'docker' group)."
fi

ensure_gpu_requirements
compose_setup_files
print_gpu_backend_status
configure_dynamic_host_ports "resolving host ports"

if [[ $REBUILD = 1 ]]; then
  step "docker compose build"
  compose_cmd build
fi

step "docker compose up -d"
compose_up_with_recovery

ok "Web UI:  http://localhost:${HOST_FRONTEND_PORT}"
if [[ $HOST_ACCESS = 1 ]]; then
  ok "API:    http://localhost:${HOST_API_PORT}"
  ok "Ollama: http://localhost:${HOST_OLLAMA_PORT}"
  ok "Postgres host port: ${HOST_POSTGRES_PORT}"
  ok "Redis host port:    ${HOST_REDIS_PORT}"
else
  ok "API/Ollama/Postgres/Redis are internal-only. Re-run with --host-access to expose them on localhost."
fi

if [[ $FOLLOW = 1 ]]; then
  step "tailing logs (Ctrl-C to detach — services stay up)"
  compose_cmd logs -f
fi
