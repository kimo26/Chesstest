#!/usr/bin/env bash
# One-shot, idempotent installer for the Chess Coach app.
#
# Docker is the ONLY supported install target. Postgres (pgvector), Redis,
# Ollama, the API, and the frontend all run in containers defined by
# docker-compose.yml. The host only needs:
#
#    * Docker Engine + the `docker compose` plugin
#
# If Docker isn't installed, this script will prompt for permission
# before installing it. Decline and the script exits with a clear
# message — we do NOT fall back to a bare-metal install.
#
# What this script does, in order:
#   1. Make sure Docker Engine + compose plugin are present.
#   2. (--no-models not set) Download Maia-1 base weights into ./models.
#   3. (--no-models not set) Clone the ECO openings TSVs into ./data.
#   4. `docker compose build` (caches lc0 build across runs).
#   5. `docker compose up -d` — postgres runs sql/*.sql on first boot.
#   6. Wait for Ollama, then pull qwen2.5:32b-instruct / 14b-instruct /
#      bge-m3 into its volume (skips models already present).
#
# Re-running is safe: every step prints "✓ already present" when it can.
#
# Flags:
#   --no-models     Skip Maia + Ollama model downloads (saves ~30 GB + time).
#   --gpu           Uncomment the NVIDIA block in docker-compose.yml.
#   --yes           Don't prompt — assume "yes" for Docker install.
#   -h | --help     Show this header and exit.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SKIP_MODELS=0
USE_GPU=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --no-models) SKIP_MODELS=1 ;;
    --gpu)       USE_GPU=1 ;;
    --yes|-y)    ASSUME_YES=1 ;;
    -h|--help)
      sed -n '2,30p' "$0"
      exit 0
      ;;
    *) echo "Unknown flag: $arg" >&2; exit 2 ;;
  esac
done

step() { echo -e "\n\033[1;34m▶ $*\033[0m"; }
ok()   { echo -e "  \033[32m✓\033[0m $*"; }
add()  { echo -e "  \033[36m↓\033[0m $*"; }
warn() { echo -e "  \033[33m!\033[0m $*"; }
die()  { echo -e "  \033[31m✗\033[0m $*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

echo "============================================================"
echo "  Chess Coach — Docker installer"
echo "  Repo:    $REPO_ROOT"
echo "  Models:  $([[ $SKIP_MODELS = 1 ]] && echo skip || echo yes)"
echo "  GPU:     $([[ $USE_GPU = 1 ]] && echo yes || echo no)"
echo "============================================================"

# ──────────────────────────────────────────────────────────────────────
#  Permission prompt helper. Honours --yes / $CI / non-TTY stdin.
# ──────────────────────────────────────────────────────────────────────
confirm() {
  local prompt="$1" default_no="${2:-1}"
  if [[ $ASSUME_YES = 1 ]]; then
    echo "  (--yes) $prompt → yes"
    return 0
  fi
  if [[ ! -t 0 ]]; then
    # Non-interactive shell with no explicit --yes: refuse to assume.
    warn "Non-interactive shell and no --yes flag — refusing to $prompt"
    return 1
  fi
  local reply
  read -r -p "  ? $prompt [y/N] " reply
  [[ "$reply" =~ ^[Yy]([Ee][Ss])?$ ]]
}

# ──────────────────────────────────────────────────────────────────────
#  1. Docker Engine + compose plugin
# ──────────────────────────────────────────────────────────────────────
step "1/5 Docker"

install_docker_linux() {
  # Uses Docker's official convenience script. It detects the distro
  # and sets up the apt/yum repo, then installs docker-ce + compose.
  add "running https://get.docker.com (sudo required)"
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sudo sh /tmp/get-docker.sh
  rm -f /tmp/get-docker.sh

  # Enable + start the daemon where systemd is present.
  if have systemctl; then
    sudo systemctl enable --now docker || true
  fi

  # Put the current user in the docker group so they don't need sudo
  # for every compose command. Takes effect in a new shell, but we can
  # use `sg docker` inside this script if necessary.
  if ! id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
    add "adding $USER to the 'docker' group"
    sudo usermod -aG docker "$USER" || true
    warn "You'll need a new shell (or 'newgrp docker') for group membership to apply."
  fi
}

install_docker_mac() {
  warn "Docker Desktop for Mac must be installed interactively."
  warn "  → https://www.docker.com/products/docker-desktop/"
  warn "  (or: brew install --cask docker)"
  if have brew && confirm "Install Docker Desktop via Homebrew cask now?"; then
    brew install --cask docker
    warn "Launch Docker Desktop once to complete setup, then re-run this script."
  fi
  die "Docker not available — install it and re-run."
}

if have docker; then
  ok "docker present ($(docker --version 2>/dev/null | head -1))"
else
  warn "Docker is not installed on this machine."
  echo "    Chess Coach runs its database, cache, LLM runtime, API, and"
  echo "    frontend as Docker containers — there is no bare-metal path."
  case "$(uname -s)" in
    Linux)
      if confirm "Install Docker Engine now via the official get.docker.com script? (needs sudo)"; then
        install_docker_linux
      else
        die "Docker is required. Install it manually (https://docs.docker.com/engine/install/) and re-run ./scripts/setup.sh."
      fi
      ;;
    Darwin)
      install_docker_mac
      ;;
    *)
      die "Unsupported OS: $(uname -s). Install Docker Desktop for your platform."
      ;;
  esac
fi

# compose plugin
if docker compose version >/dev/null 2>&1; then
  ok "docker compose plugin present"
else
  warn "'docker compose' plugin missing."
  case "$(uname -s)" in
    Linux)
      if confirm "Install docker-compose-plugin via the system package manager?"; then
        if have apt-get; then
          sudo apt-get update -qq && sudo apt-get install -y docker-compose-plugin
        elif have dnf; then
          sudo dnf install -y docker-compose-plugin
        elif have pacman; then
          sudo pacman -S --noconfirm docker-compose
        else
          die "No known package manager — install docker-compose-plugin manually."
        fi
      else
        die "docker compose plugin required."
      fi
      ;;
    *)
      die "Install Docker Desktop (ships with compose) or the compose plugin."
      ;;
  esac
fi

# Can the current user actually talk to the daemon?
if ! docker info >/dev/null 2>&1; then
  warn "Current user cannot talk to the Docker daemon yet."
  if have sg && id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
    warn "Re-run in a new shell, or prefix commands with: newgrp docker"
  fi
  die "Open a new shell (for the 'docker' group) or start Docker Desktop, then re-run."
fi

# ──────────────────────────────────────────────────────────────────────
#  2. GPU toggle for lc0 / ollama
# ──────────────────────────────────────────────────────────────────────
if [[ $USE_GPU = 1 ]]; then
  step "2/5 GPU compose override"
  if grep -q "# deploy:" docker-compose.yml; then
    add "uncommenting NVIDIA deploy block"
    # Uncomment the deploy: … block under ollama (exactly the 8 lines
    # that start with "    # ").
    python3 - <<'PY'
import re, pathlib
p = pathlib.Path("docker-compose.yml")
src = p.read_text()
src = re.sub(
    r"(?m)^    # (deploy:|  resources:|    reservations:|      devices:|        - driver: nvidia|          count: all|          capabilities: \[gpu\])",
    r"    \1", src,
)
p.write_text(src)
PY
    ok "GPU block enabled — install the NVIDIA Container Toolkit if you haven't."
  else
    ok "GPU block already active"
  fi
else
  step "2/5 GPU"
  ok "CPU mode (pass --gpu to opt in)"
fi

# ──────────────────────────────────────────────────────────────────────
#  3. Host-mounted assets (Maia weights, ECO TSVs)
# ──────────────────────────────────────────────────────────────────────
step "3/5 Host-mounted assets"
mkdir -p models data

if [[ $SKIP_MODELS = 0 ]]; then
  if [[ -f models/maia-1900.pb.gz ]]; then
    ok "Maia-1900 weights present"
  else
    add "downloading Maia-1900 weights"
    curl -fsSL -o models/maia-1900.pb.gz \
      "https://github.com/CSSLab/maia-chess/raw/master/maia_weights/maia-1900.pb.gz"
  fi
else
  warn "--no-models: skipping Maia weights"
fi

if [[ -d data/chess-openings ]]; then
  ok "data/chess-openings present"
else
  add "cloning lichess-org/chess-openings"
  git clone --depth 1 https://github.com/lichess-org/chess-openings data/chess-openings >/dev/null
fi

# ──────────────────────────────────────────────────────────────────────
#  4. Build + bring up the stack
# ──────────────────────────────────────────────────────────────────────
step "4/5 docker compose build + up"
add "docker compose build (first run will build lc0 — up to ~10 min)"
docker compose build
add "docker compose up -d"
docker compose up -d

# Postgres healthcheck wait.
add "waiting for postgres to become healthy"
for _ in $(seq 1 60); do
  if [[ "$(docker inspect -f '{{.State.Health.Status}}' chess_postgres 2>/dev/null)" = "healthy" ]]; then
    ok "postgres healthy"
    break
  fi
  sleep 2
done

# ──────────────────────────────────────────────────────────────────────
#  5. Ollama model pulls (into the container volume)
# ──────────────────────────────────────────────────────────────────────
if [[ $SKIP_MODELS = 0 ]]; then
  step "5/5 Ollama model pulls"
  # Wait for ollama to respond.
  for _ in $(seq 1 30); do
    if curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then break; fi
    sleep 2
  done
  for model in qwen2.5:32b-instruct qwen2.5:14b-instruct bge-m3; do
    if docker compose exec -T ollama ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -Fx "$model" >/dev/null; then
      ok "model $model already pulled"
    else
      add "ollama pull $model (can take a while — many GB)"
      docker compose exec -T ollama ollama pull "$model"
    fi
  done
else
  warn "--no-models: skipping Ollama pulls"
fi

echo ""
echo "============================================================"
echo "  ✅ Stack is up. Everything is running in Docker:"
echo ""
echo "      docker compose ps"
echo ""
echo "  Launch the app:       ./scripts/start.sh"
echo "  Open the web UI:      http://localhost:5173"
echo ""
echo "  First visit? The web app will walk you through the"
echo "  Chess.com-username onboarding wizard with live ETAs."
echo "============================================================"
