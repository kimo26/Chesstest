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

# Ask before running `sudo systemctl restart docker`. In non-interactive
# shells (CI, piped stdin) default to yes — this is a recovery path, not a
# destructive one, and CI environments can't answer prompts.
confirm_restart_docker() {
  if [[ ! -t 0 ]]; then return 0; fi
  local reply
  read -r -p "  ? Restart the Docker daemon now to recreate its iptables chains? (needs sudo) [Y/n] " reply
  [[ -z "$reply" || "$reply" =~ ^[Yy]([Ee][Ss])?$ ]]
}

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

# `docker compose up` can fail with:
#   "iptables: Chain 'DOCKER-ISOLATION-STAGE-2' does not exist"
# when the host's iptables state has been clobbered (ufw/firewalld reload,
# nftables/iptables-legacy backend switch, manual `iptables -F`, etc.).
# Restarting the Docker daemon makes it recreate its chains. We try that
# automatically, once, if we detect the signature.
compose_up() {
  docker compose up -d 2>&1 | tee /tmp/chesscoach-up.log
  # tee exits 0 if either side of the pipe did; check compose's exit via PIPESTATUS.
  return "${PIPESTATUS[0]}"
}

step "docker compose up -d"
if ! compose_up; then
  if grep -q "DOCKER-ISOLATION-STAGE" /tmp/chesscoach-up.log 2>/dev/null \
     || grep -qi "iptables failed"        /tmp/chesscoach-up.log 2>/dev/null; then
    warn "Docker's iptables chains look broken — this usually happens after"
    warn "a firewall reload (ufw/firewalld) or an iptables backend switch."
    if have systemctl && confirm_restart_docker; then
      step "restarting the Docker daemon (sudo)"
      sudo systemctl restart docker
      # Give the daemon a moment to recreate its networks.
      for _ in $(seq 1 15); do
        if docker info >/dev/null 2>&1; then break; fi
        sleep 1
      done
      step "docker compose up -d (retry)"
      docker compose down --remove-orphans >/dev/null 2>&1 || true
      docker network prune -f >/dev/null 2>&1 || true
      docker compose up -d
    else
      die "Run 'sudo systemctl restart docker' (and re-run this script) to recover."
    fi
  else
    die "docker compose up failed — see the output above."
  fi
fi
rm -f /tmp/chesscoach-up.log

ok "Web UI:  http://localhost:5173"
ok "API:    http://localhost:8000"
ok "Ollama: http://localhost:11434"

if [[ $FOLLOW = 1 ]]; then
  step "tailing logs (Ctrl-C to detach — services stay up)"
  docker compose logs -f
fi
