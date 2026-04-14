#!/usr/bin/env bash
# One-shot, idempotent installer for the Chess Coach app.
#
# What it does:
#   * Detects the OS + package manager (apt / brew / dnf).
#   * For every dependency, probes first and only installs what's missing.
#   * Installs: postgresql-16 + pgvector, redis, stockfish, python3.11,
#     node+npm, build tools, zstd, curl, git, (optional) docker.
#   * Installs Ollama (if missing) and pulls only the models that aren't
#     already present locally.
#   * Downloads Maia-1 base weights (if absent) and builds lc0 from source
#     (CPU by default, or CUDA if --gpu).
#   * Creates the chess_coach Postgres database / role (if absent) and
#     applies every sql/*.sql schema file (all idempotent).
#   * Creates the Python venv and installs the package in editable mode.
#   * Runs `npm install` in frontend/ when lockfile changed.
#
# What it does NOT do:
#   * Run data pipelines (ECO load, wiki refresh, Chess.com import,
#     descriptions, knowledge ingest). Those run through the first-run
#     onboarding wizard in the web app so the user can enter their
#     Chess.com username and watch progress with ETAs in the browser.
#
# Usage:
#   ./scripts/setup.sh                 # everything
#   ./scripts/setup.sh --no-models     # skip Ollama / LC0 / Maia downloads
#   ./scripts/setup.sh --gpu           # CUDA lc0 build (needs toolkit)
#   ./scripts/setup.sh --docker        # use docker compose for postgres+redis+ollama
#
# Re-running the script is safe: every step prints "✓ already installed"
# when a dep is present.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ──────────────────────────────────────────────────────────────────────
#  Flags
# ──────────────────────────────────────────────────────────────────────
SKIP_MODELS=0
USE_GPU=0
USE_DOCKER=0
for arg in "$@"; do
  case "$arg" in
    --no-models) SKIP_MODELS=1 ;;
    --gpu)       USE_GPU=1 ;;
    --docker)    USE_DOCKER=1 ;;
    -h|--help)
      sed -n '2,32p' "$0"
      exit 0
      ;;
  esac
done

step() { echo -e "\n\033[1;34m▶ $*\033[0m"; }
ok()   { echo -e "  \033[32m✓\033[0m $*"; }
add()  { echo -e "  \033[36m↓\033[0m $*"; }
warn() { echo -e "  \033[33m!\033[0m $*"; }

have() { command -v "$1" >/dev/null 2>&1; }

echo "============================================================"
echo "  Chess Coach — one-shot installer"
echo "  Repo:    $REPO_ROOT"
echo "  Models:  $([[ $SKIP_MODELS = 1 ]] && echo skip || echo yes)"
echo "  GPU lc0: $([[ $USE_GPU = 1 ]] && echo yes || echo no)"
echo "  Docker:  $([[ $USE_DOCKER = 1 ]] && echo yes || echo no)"
echo "============================================================"

# ──────────────────────────────────────────────────────────────────────
#  OS detection
# ──────────────────────────────────────────────────────────────────────
OS="$(uname -s)"
PKG=""
case "$OS" in
  Linux)
    if   have apt-get; then PKG="apt"
    elif have dnf;     then PKG="dnf"
    elif have pacman;  then PKG="pacman"
    fi
    ;;
  Darwin) PKG="brew" ;;
esac

if [[ -z "$PKG" ]]; then
  warn "Couldn't detect package manager. Install postgres+pgvector, redis, stockfish, build tools, node, and python3.11 manually."
fi

# ──────────────────────────────────────────────────────────────────────
#  1. System packages (only install what's missing)
# ──────────────────────────────────────────────────────────────────────
step "1/7 System packages"

install_apt() {
  local want=("$@")
  local missing=()
  for p in "${want[@]}"; do
    if dpkg -s "$p" >/dev/null 2>&1; then
      ok "$p already installed"
    else
      missing+=("$p")
    fi
  done
  if (( ${#missing[@]} )); then
    add "installing: ${missing[*]}"
    sudo apt-get update -qq
    sudo apt-get install -y "${missing[@]}"
  fi
}

install_brew() {
  for p in "$@"; do
    if brew list --formula "$p" >/dev/null 2>&1; then
      ok "$p already installed"
    else
      add "brew install $p"
      brew install "$p"
    fi
  done
}

install_dnf() {
  local want=("$@")
  local missing=()
  for p in "${want[@]}"; do
    if rpm -q "$p" >/dev/null 2>&1; then
      ok "$p already installed"
    else
      missing+=("$p")
    fi
  done
  if (( ${#missing[@]} )); then
    add "installing: ${missing[*]}"
    sudo dnf install -y "${missing[@]}"
  fi
}

case "$PKG" in
  apt)
    BASE_PKGS=(build-essential curl git wget ca-certificates zstd python3.11 python3.11-venv python3.11-dev stockfish nodejs npm)
    [[ $USE_DOCKER = 0 ]] && BASE_PKGS+=(postgresql postgresql-contrib postgresql-16-pgvector redis-server)
    [[ $USE_DOCKER = 1 ]] && BASE_PKGS+=(docker.io docker-compose-plugin)
    install_apt "${BASE_PKGS[@]}"
    if [[ $USE_DOCKER = 0 ]]; then
      sudo systemctl enable --now postgresql redis-server >/dev/null 2>&1 || true
    fi
    ;;
  brew)
    BASE_PKGS=(python@3.11 stockfish node zstd)
    [[ $USE_DOCKER = 0 ]] && BASE_PKGS+=(postgresql@16 redis pgvector)
    [[ $USE_DOCKER = 1 ]] && BASE_PKGS+=(docker docker-compose)
    install_brew "${BASE_PKGS[@]}"
    if [[ $USE_DOCKER = 0 ]]; then
      brew services start postgresql@16 >/dev/null 2>&1 || true
      brew services start redis >/dev/null 2>&1 || true
    fi
    ;;
  dnf)
    BASE_PKGS=(gcc gcc-c++ make curl git python3.11 python3.11-devel stockfish nodejs npm zstd)
    [[ $USE_DOCKER = 0 ]] && BASE_PKGS+=(postgresql-server postgresql-contrib redis)
    [[ $USE_DOCKER = 1 ]] && BASE_PKGS+=(docker docker-compose)
    install_dnf "${BASE_PKGS[@]}"
    if [[ $USE_DOCKER = 0 ]]; then
      sudo postgresql-setup --initdb >/dev/null 2>&1 || true
      sudo systemctl enable --now postgresql redis >/dev/null 2>&1 || true
      warn "pgvector must be built from source on Fedora (see README troubleshooting)."
    fi
    ;;
  *) warn "Skipping system packages — install manually." ;;
esac

# ──────────────────────────────────────────────────────────────────────
#  2. Docker path (optional) — OR bare-metal DB bootstrap
# ──────────────────────────────────────────────────────────────────────
if [[ $USE_DOCKER = 1 ]]; then
  step "2/7 Docker Compose (postgres+pgvector, redis, ollama)"
  if [[ ! -f docker-compose.yml ]]; then
    add "writing docker-compose.yml"
    cat > docker-compose.yml <<'YML'
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB:       chess_coach
      POSTGRES_USER:     chess
      POSTGRES_PASSWORD: chess
    ports: ["5432:5432"]
    volumes: ["./.docker/pg:/var/lib/postgresql/data"]
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
  ollama:
    image: ollama/ollama:latest
    ports: ["11434:11434"]
    volumes: ["./.docker/ollama:/root/.ollama"]
YML
  else
    ok "docker-compose.yml present"
  fi
  docker compose up -d
  ok "services up"
else
  step "2/7 Database bootstrap"
  if have psql; then
    if sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='chess_coach'" 2>/dev/null | grep -q 1; then
      ok "database chess_coach exists"
    else
      add "createdb chess_coach"
      sudo -u postgres createdb chess_coach
    fi
    if sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='chess'" 2>/dev/null | grep -q 1; then
      ok "role chess exists"
    else
      add "creating role chess"
      sudo -u postgres psql -c "CREATE ROLE chess WITH LOGIN PASSWORD 'chess' SUPERUSER;"
    fi
    for sql_file in sql/001_schema.sql sql/002_knowledge_base.sql sql/003_coach_memory.sql; do
      if [[ -f "$sql_file" ]]; then
        add "applying $sql_file"
        sudo -u postgres psql -d chess_coach -f "$sql_file" >/dev/null
      fi
    done
    ok "schema applied"
  else
    warn "psql not on PATH; skipping DB bootstrap"
  fi
fi

# ──────────────────────────────────────────────────────────────────────
#  3. Python venv + package
# ──────────────────────────────────────────────────────────────────────
step "3/7 Python venv + dependencies"
if [[ ! -d .venv ]]; then
  add "creating .venv"
  python3.11 -m venv .venv
else
  ok ".venv present"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip wheel >/dev/null
pip install -e '.' >/dev/null
ok "Python package installed (editable)"

# ──────────────────────────────────────────────────────────────────────
#  4. Frontend deps (skip if node_modules is newer than the lockfile)
# ──────────────────────────────────────────────────────────────────────
step "4/7 Frontend"
if [[ -d frontend ]]; then
  if [[ -d frontend/node_modules && frontend/node_modules -nt frontend/package-lock.json ]]; then
    ok "node_modules up to date"
  else
    add "npm install"
    (cd frontend && npm install --silent)
  fi
else
  warn "frontend/ not found; skipping"
fi

# ──────────────────────────────────────────────────────────────────────
#  5. Ollama + model pulls
# ──────────────────────────────────────────────────────────────────────
if [[ $SKIP_MODELS -eq 0 ]]; then
  step "5/7 Ollama + model pulls"
  if ! have ollama; then
    add "installing Ollama via official script"
    curl -fsSL https://ollama.com/install.sh | sh
  else
    ok "ollama already installed"
  fi
  if ! pgrep -x ollama >/dev/null 2>&1; then
    add "starting ollama serve (background)"
    nohup ollama serve >/tmp/ollama.log 2>&1 &
    sleep 3
  else
    ok "ollama daemon running"
  fi
  for model in qwen2.5:32b-instruct qwen2.5:14b-instruct bge-m3; do
    if ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -Fx "$model" >/dev/null; then
      ok "model $model already pulled"
    else
      add "ollama pull $model (this can take a while)"
      ollama pull "$model"
    fi
  done
else
  warn "5/7 SKIPPED (--no-models)"
fi

# ──────────────────────────────────────────────────────────────────────
#  6. lc0 + Maia-1 weights
# ──────────────────────────────────────────────────────────────────────
if [[ $SKIP_MODELS -eq 0 ]]; then
  step "6/7 lc0 + Maia-1 base weights"
  mkdir -p models
  if [[ -f models/maia-1900.pb.gz ]]; then
    ok "Maia-1900 weights present"
  else
    add "downloading Maia-1900 weights"
    curl -fsSL -o models/maia-1900.pb.gz \
      "https://github.com/CSSLab/maia-chess/raw/master/maia_weights/maia-1900.pb.gz"
  fi
  if have lc0; then
    ok "lc0 already installed"
  else
    if [[ $USE_GPU = 1 ]]; then
      warn "Build GPU lc0 manually from https://github.com/LeelaChessZero/lc0 (needs CUDA toolkit)."
    else
      add "building lc0 from source (CPU, ~5 min)"
      tmpdir="$(mktemp -d)"
      git clone --depth 1 https://github.com/LeelaChessZero/lc0 "$tmpdir/lc0"
      ( cd "$tmpdir/lc0" && ./build.sh release -Dblas=true -Dcudnn=false -Dplain_cuda=false >/dev/null )
      if sudo cp "$tmpdir/lc0/build/release/lc0" /usr/local/bin/lc0 2>/dev/null; then
        ok "lc0 installed to /usr/local/bin/lc0"
      else
        cp "$tmpdir/lc0/build/release/lc0" "$REPO_ROOT/models/lc0"
        warn "lc0 copied to models/lc0 (sudo unavailable)"
      fi
    fi
  fi
else
  warn "6/7 SKIPPED (--no-models)"
fi

# ──────────────────────────────────────────────────────────────────────
#  7. Seed assets (git submodule for ECO TSVs). Data pipelines run from
#     the web app's onboarding wizard, not here, so the user sees live
#     progress in the browser.
# ──────────────────────────────────────────────────────────────────────
step "7/7 Seed assets"
if [[ -d data/chess-openings ]]; then
  ok "data/chess-openings present"
else
  add "cloning lichess-org/chess-openings"
  mkdir -p data
  git clone --depth 1 https://github.com/lichess-org/chess-openings data/chess-openings >/dev/null
fi

echo ""
echo "============================================================"
echo "  ✅ Install complete. Launch with:"
echo ""
echo "      ./scripts/start.sh"
echo ""
echo "  Then open http://localhost:5173 — the web app will walk you"
echo "  through the Chess.com-username onboarding wizard and run every"
echo "  data pipeline with live progress bars + ETAs."
echo "============================================================"
