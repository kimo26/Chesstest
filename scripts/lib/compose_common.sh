#!/usr/bin/env bash
# Shared helpers for setup.sh and start.sh.

if ! declare -f have >/dev/null 2>&1; then
  have() { command -v "$1" >/dev/null 2>&1; }
fi

if ! declare -f path_exists >/dev/null 2>&1; then
  path_exists() { [[ -e "$1" ]]; }
fi

if ! declare -f docker_runtimes_json >/dev/null 2>&1; then
  docker_runtimes_json() {
    docker info --format '{{json .Runtimes}}' 2>/dev/null || printf '{}\n'
  }
fi

normalise_gpu_backend() {
  printf '%s' "${1:-auto}" | tr '[:upper:]' '[:lower:]'
}

gpu_backend_requested_is_auto() {
  [[ "${GPU_BACKEND_REQUESTED:-auto}" == "auto" ]]
}

has_nvidia_runtime() {
  local runtimes
  runtimes="$(docker_runtimes_json)"
  [[ "$runtimes" == *'"nvidia"'* ]]
}

can_use_nvidia_backend() {
  have nvidia-smi \
    && have nvidia-container-runtime \
    && have nvidia-container-cli \
    && has_nvidia_runtime
}

can_use_rocm_backend() {
  path_exists /dev/kfd && path_exists /dev/dri
}

can_use_vulkan_backend() {
  path_exists /dev/dri
}

resolve_gpu_backend() {
  GPU_BACKEND_REQUESTED="$(normalise_gpu_backend "${GPU_BACKEND:-auto}")"
  GPU_BACKEND_SELECTED="cpu"
  GPU_BACKEND_MESSAGE=""

  if [[ "${USE_GPU:-0}" != "1" ]]; then
    export GPU_BACKEND_REQUESTED GPU_BACKEND_SELECTED GPU_BACKEND_MESSAGE
    return 0
  fi

  case "$GPU_BACKEND_REQUESTED" in
    auto)
      if can_use_nvidia_backend; then
        GPU_BACKEND_SELECTED="nvidia"
        GPU_BACKEND_MESSAGE="Auto-selected NVIDIA GPU backend"
      elif can_use_rocm_backend; then
        GPU_BACKEND_SELECTED="rocm"
        GPU_BACKEND_MESSAGE="Auto-selected ROCm GPU backend"
      elif can_use_vulkan_backend; then
        GPU_BACKEND_SELECTED="vulkan"
        GPU_BACKEND_MESSAGE="Auto-selected Vulkan GPU backend"
      else
        GPU_BACKEND_SELECTED="cpu"
        GPU_BACKEND_MESSAGE="No compatible GPU backend detected; continuing in CPU mode"
      fi
      ;;
    nvidia)
      if can_use_nvidia_backend; then
        GPU_BACKEND_SELECTED="nvidia"
        GPU_BACKEND_MESSAGE="Using requested NVIDIA GPU backend"
      else
        die "GPU backend 'nvidia' was requested, but the NVIDIA driver/toolkit/runtime chain is not ready on this host." || return 1
      fi
      ;;
    rocm)
      if can_use_rocm_backend; then
        GPU_BACKEND_SELECTED="rocm"
        GPU_BACKEND_MESSAGE="Using requested ROCm GPU backend"
      else
        die "GPU backend 'rocm' was requested, but /dev/kfd and /dev/dri are not both available on this host." || return 1
      fi
      ;;
    vulkan)
      if can_use_vulkan_backend; then
        GPU_BACKEND_SELECTED="vulkan"
        GPU_BACKEND_MESSAGE="Using requested Vulkan GPU backend"
      else
        die "GPU backend 'vulkan' was requested, but /dev/dri is not available on this host." || return 1
      fi
      ;;
    cpu)
      GPU_BACKEND_SELECTED="cpu"
      GPU_BACKEND_MESSAGE="CPU mode forced via GPU_BACKEND=cpu"
      ;;
    *)
      die "Unknown GPU backend '$GPU_BACKEND_REQUESTED'. Expected one of: auto, nvidia, rocm, vulkan, cpu." || return 1
      ;;
  esac

  export GPU_BACKEND_REQUESTED GPU_BACKEND_SELECTED GPU_BACKEND_MESSAGE
}

compose_setup_files() {
  if [[ -z "${GPU_BACKEND_SELECTED:-}" ]]; then
    resolve_gpu_backend
  fi

  COMPOSE_ARGS=(-f docker-compose.yml)
  case "${GPU_BACKEND_SELECTED:-cpu}" in
    nvidia) COMPOSE_ARGS+=(-f docker-compose.gpu.nvidia.yml) ;;
    rocm)   COMPOSE_ARGS+=(-f docker-compose.gpu.rocm.yml) ;;
    vulkan) COMPOSE_ARGS+=(-f docker-compose.gpu.vulkan.yml) ;;
  esac
  if [[ "${HOST_ACCESS:-0}" = "1" ]]; then
    COMPOSE_ARGS+=(-f docker-compose.host-access.yml)
    if [[ -f .env.host-access.local ]]; then
      # shellcheck disable=SC1091
      set -a
      . ./.env.host-access.local
      set +a
      ok "Loaded host-access overrides from .env.host-access.local"
    fi
  fi
}

compose_cmd() {
  docker compose "${COMPOSE_ARGS[@]}" "$@"
}

ensure_gpu_requirements() {
  resolve_gpu_backend
}

print_gpu_backend_status() {
  if [[ "${USE_GPU:-0}" != "1" ]]; then
    ok "CPU mode (forced). Omit --cpu to re-enable automatic GPU detection."
    return 0
  fi

  if [[ -n "${GPU_BACKEND_MESSAGE:-}" ]]; then
    if [[ "${GPU_BACKEND_SELECTED:-cpu}" == "cpu" ]]; then
      warn "$GPU_BACKEND_MESSAGE"
    else
      ok "$GPU_BACKEND_MESSAGE"
    fi
  fi
}

gpu_startup_error_in_log() {
  local log_path="$1"
  rg -q \
    "nvidia-container-cli|could not select device driver|/dev/kfd|/dev/dri|rocm|vulkan|amdgpu" \
    "$log_path"
}

gpu_auto_fallback_allowed() {
  [[ "${USE_GPU:-0}" = "1" ]] \
    && gpu_backend_requested_is_auto \
    && [[ "${GPU_BACKEND_SELECTED:-cpu}" != "cpu" ]]
}

fallback_to_cpu_compose_mode() {
  GPU_AUTO_FALLBACK_FROM="${GPU_BACKEND_SELECTED:-unknown}"
  GPU_BACKEND_SELECTED="cpu"
  GPU_BACKEND_MESSAGE="GPU backend '${GPU_AUTO_FALLBACK_FROM}' failed to start; continuing in CPU mode"
  export GPU_AUTO_FALLBACK_FROM GPU_BACKEND_SELECTED GPU_BACKEND_MESSAGE
  compose_setup_files
}

is_port_in_use() {
  local port="$1"
  if have ss; then
    ss -ltn 2>/dev/null | awk -v p="$port" '
      NR > 1 {
        n = split($4, a, ":")
        if (a[n] == p) found = 1
      }
      END { exit found ? 0 : 1 }
    '
    return
  fi
  if have lsof; then
    lsof -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1
    return
  fi
  return 1
}

first_free_port_from() {
  local start="$1"
  local port
  for port in $(seq "$start" $((start + 200))); do
    if ! is_port_in_use "$port"; then
      echo "$port"
      return 0
    fi
  done
  return 1
}

resolve_host_port() {
  local var_name="$1" default_port="$2" label="$3"
  local requested="${!var_name-}"
  local chosen
  if [[ -z "$requested" ]]; then
    requested="$default_port"
  fi
  chosen="$requested"
  if is_port_in_use "$requested"; then
    chosen="$(first_free_port_from $((requested + 1)))" || die "No free host port found near $requested for $label."
    warn "$label host port $requested is busy; using $chosen"
  fi
  export "$var_name=$chosen"
}

configure_dynamic_host_ports() {
  local label="${1:-resolving host ports}"
  step "$label"
  export HOST_FRONTEND_BIND="${HOST_FRONTEND_BIND:-127.0.0.1}"
  resolve_host_port HOST_FRONTEND_PORT 5173 "Frontend"

  if [[ "${HOST_ACCESS:-0}" = "1" ]]; then
    export HOST_API_BIND="${HOST_API_BIND:-127.0.0.1}"
    export HOST_OLLAMA_BIND="${HOST_OLLAMA_BIND:-127.0.0.1}"
    export HOST_POSTGRES_BIND="${HOST_POSTGRES_BIND:-127.0.0.1}"
    export HOST_REDIS_BIND="${HOST_REDIS_BIND:-127.0.0.1}"
    resolve_host_port HOST_API_PORT 8000 "API"
    resolve_host_port HOST_OLLAMA_PORT 11434 "Ollama"
    resolve_host_port HOST_POSTGRES_PORT 5432 "Postgres"
    resolve_host_port HOST_REDIS_PORT 6379 "Redis"
    ok "Ports — frontend:$HOST_FRONTEND_PORT api:$HOST_API_PORT ollama:$HOST_OLLAMA_PORT postgres:$HOST_POSTGRES_PORT redis:$HOST_REDIS_PORT"
  else
    ok "Ports — frontend:$HOST_FRONTEND_PORT (internal-only backends; pass --host-access to publish api/ollama/postgres/redis)"
  fi
}

iptables_chains_ok() {
  if ! have iptables; then
    return 0
  fi
  iptables -L DOCKER-ISOLATION-STAGE-2 -n >/dev/null 2>&1
}

repair_isolation_chains_with() {
  local ipt_bin="$1"
  sudo "$ipt_bin" -t filter -N DOCKER-ISOLATION-STAGE-1 2>/dev/null || true
  sudo "$ipt_bin" -t filter -N DOCKER-ISOLATION-STAGE-2 2>/dev/null || true
  sudo "$ipt_bin" -t filter -F DOCKER-ISOLATION-STAGE-1 || return 1
  sudo "$ipt_bin" -t filter -F DOCKER-ISOLATION-STAGE-2 || return 1
  sudo "$ipt_bin" -t filter -C DOCKER-ISOLATION-STAGE-1 -j DOCKER-ISOLATION-STAGE-2 2>/dev/null \
    || sudo "$ipt_bin" -t filter -A DOCKER-ISOLATION-STAGE-1 -j DOCKER-ISOLATION-STAGE-2 || return 1
  sudo "$ipt_bin" -t filter -C DOCKER-ISOLATION-STAGE-2 -j RETURN 2>/dev/null \
    || sudo "$ipt_bin" -t filter -A DOCKER-ISOLATION-STAGE-2 -j RETURN || return 1
  return 0
}

repair_isolation_chains() {
  local repaired=1
  local seen=()
  local candidate
  for candidate in iptables iptables-nft iptables-legacy; do
    if have "$candidate"; then
      seen+=("$candidate")
      if repair_isolation_chains_with "$candidate"; then
        ok "Repaired DOCKER-ISOLATION chains via $candidate"
        repaired=0
      fi
    fi
  done
  if [[ ${#seen[@]} -eq 0 ]]; then
    warn "No iptables binary found; cannot repair isolation chains automatically."
    return 1
  fi
  return "$repaired"
}

confirm_restart_docker() {
  if [[ "${ASSUME_YES:-0}" = "1" ]]; then
    echo "  (--yes) restart Docker daemon to recover networking → yes"
    return 0
  fi
  if [[ ! -t 0 ]]; then
    warn "Non-interactive shell and no --yes flag — refusing to restart Docker automatically"
    return 1
  fi
  local reply
  read -r -p "  ? Restart the Docker daemon now to recreate its iptables chains? (needs sudo) [y/N] " reply
  [[ "$reply" =~ ^[Yy]([Ee][Ss])?$ ]]
}

restart_docker_and_wait() {
  if ! have systemctl; then
    die "Cannot restart Docker automatically — no systemctl. Run: sudo service docker restart"
  fi
  if ! confirm_restart_docker; then
    die "Run 'sudo systemctl restart docker' then re-run this script."
  fi
  step "restarting the Docker daemon (sudo)"
  sudo systemctl restart docker
  local i
  for i in $(seq 1 20); do
    if docker info >/dev/null 2>&1; then
      if iptables_chains_ok; then
        ok "Docker daemon up and iptables chains detected"
      else
        warn "Docker daemon is up but DOCKER-ISOLATION-STAGE-2 is still not visible yet."
      fi
      return 0
    fi
    sleep 1
  done
  die "Docker daemon did not become ready within 20 s. Check 'sudo systemctl status docker'."
}

compose_up_with_recovery() {
  local log
  log="$(mktemp /tmp/chesscoach-up-XXXXXX.log)"

  if compose_cmd up -d 2>&1 | tee "$log"; then
    rm -f "$log"
    return 0
  fi

  if gpu_auto_fallback_allowed && gpu_startup_error_in_log "$log"; then
    warn "Selected GPU backend '${GPU_BACKEND_SELECTED}' failed to start."
    warn "Falling back to CPU mode because GPU backend selection is automatic."
    compose_cmd down --remove-orphans >/dev/null 2>&1 || true
    fallback_to_cpu_compose_mode
    rm -f "$log"

    local cpu_retry_log
    cpu_retry_log="$(mktemp /tmp/chesscoach-up-XXXXXX.log)"
    if compose_cmd up -d 2>&1 | tee "$cpu_retry_log"; then
      rm -f "$cpu_retry_log"
      ok "Recovered in CPU mode after GPU startup failure"
      return 0
    fi
    rm -f "$cpu_retry_log"
    die "docker compose up failed after automatic CPU fallback — see the output above."
  fi

  if ! grep -qE "DOCKER-ISOLATION-STAGE|iptables failed|iptables:" "$log" 2>/dev/null; then
    rm -f "$log"
    die "docker compose up failed — see the output above."
  fi
  rm -f "$log"

  warn "Docker's iptables chains look broken — this usually happens after"
  warn "a firewall reload (ufw/firewalld) or an iptables backend switch."
  step "attempting direct iptables chain repair"
  repair_isolation_chains || warn "Direct chain repair did not fully succeed; trying daemon restart path."

  local retry_log
  retry_log="$(mktemp /tmp/chesscoach-up-XXXXXX.log)"
  if compose_cmd up -d 2>&1 | tee "$retry_log"; then
    rm -f "$retry_log"
    ok "Recovered after direct iptables chain repair"
    return 0
  fi
  if ! grep -qE "DOCKER-ISOLATION-STAGE|iptables failed|iptables:" "$retry_log" 2>/dev/null; then
    rm -f "$retry_log"
    die "docker compose up failed after chain repair — see the output above."
  fi
  rm -f "$retry_log"

  warn "Direct chain repair was not enough; trying Docker daemon restart."
  restart_docker_and_wait
  step "repairing iptables chains after restart"
  repair_isolation_chains || warn "Post-restart chain repair did not fully succeed."
  compose_cmd down --remove-orphans >/dev/null 2>&1 || true
  docker network prune -f >/dev/null 2>&1 || true
  compose_cmd up -d || die "compose up failed even after daemon restart. Check 'sudo systemctl status docker'."
}
