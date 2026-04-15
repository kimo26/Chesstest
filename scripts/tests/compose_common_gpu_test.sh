#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

FAKE_COMMANDS=()
FAKE_PATHS=()
FAKE_RUNTIMES_JSON='{}'

step() { :; }
ok() { :; }
warn() { :; }
die() { echo "$*" >&2; return 1; }

have() {
  local wanted="$1"
  local cmd
  for cmd in "${FAKE_COMMANDS[@]}"; do
    if [[ "$cmd" == "$wanted" ]]; then
      return 0
    fi
  done
  return 1
}

path_exists() {
  local wanted="$1"
  local path
  for path in "${FAKE_PATHS[@]}"; do
    if [[ "$path" == "$wanted" ]]; then
      return 0
    fi
  done
  return 1
}

docker_runtimes_json() {
  printf '%s\n' "$FAKE_RUNTIMES_JSON"
}

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/lib/compose_common.sh"

assert_eq() {
  local expected="$1"
  local actual="$2"
  local message="$3"
  if [[ "$expected" != "$actual" ]]; then
    echo "FAIL: $message" >&2
    echo "  expected: $expected" >&2
    echo "  actual:   $actual" >&2
    exit 1
  fi
}

assert_contains() {
  local needle="$1"
  local haystack="$2"
  local message="$3"
  if [[ "$haystack" != *"$needle"* ]]; then
    echo "FAIL: $message" >&2
    echo "  missing: $needle" >&2
    echo "  in:      $haystack" >&2
    exit 1
  fi
}

assert_not_contains() {
  local needle="$1"
  local haystack="$2"
  local message="$3"
  if [[ "$haystack" == *"$needle"* ]]; then
    echo "FAIL: $message" >&2
    echo "  unexpected: $needle" >&2
    echo "  in:         $haystack" >&2
    exit 1
  fi
}

reset_state() {
  COMPOSE_ARGS=()
  HOST_ACCESS=0
  USE_GPU=0
  GPU_BACKEND=auto
  GPU_BACKEND_SELECTED=
  GPU_REQUIRE_NVIDIA_RUNTIME=0
  FAKE_COMMANDS=()
  FAKE_PATHS=()
  FAKE_RUNTIMES_JSON='{}'
}

test_cpu_default_uses_base_compose_only() {
  reset_state
  compose_setup_files
  assert_eq "cpu" "$GPU_BACKEND_SELECTED" "CPU should be selected when --gpu is off"
  assert_eq "-f docker-compose.yml" "${COMPOSE_ARGS[*]}" "CPU mode should only use the base compose file"
}

test_auto_selects_nvidia_backend() {
  reset_state
  USE_GPU=1
  FAKE_COMMANDS=(nvidia-smi nvidia-container-runtime nvidia-container-cli)
  FAKE_RUNTIMES_JSON='{"nvidia":{"path":"nvidia-container-runtime"}}'
  compose_setup_files
  assert_eq "nvidia" "$GPU_BACKEND_SELECTED" "Auto GPU mode should prefer NVIDIA when runtime is healthy"
  assert_contains "docker-compose.gpu.nvidia.yml" "${COMPOSE_ARGS[*]}" "NVIDIA override should be included"
}

test_auto_selects_rocm_backend() {
  reset_state
  USE_GPU=1
  FAKE_PATHS=(/dev/kfd /dev/dri)
  compose_setup_files
  assert_eq "rocm" "$GPU_BACKEND_SELECTED" "Auto GPU mode should choose ROCm when KFD and DRI are present"
  assert_contains "docker-compose.gpu.rocm.yml" "${COMPOSE_ARGS[*]}" "ROCm override should be included"
}

test_auto_selects_vulkan_backend() {
  reset_state
  USE_GPU=1
  FAKE_PATHS=(/dev/dri)
  compose_setup_files
  assert_eq "vulkan" "$GPU_BACKEND_SELECTED" "Auto GPU mode should choose Vulkan when only DRI is present"
  assert_contains "docker-compose.gpu.vulkan.yml" "${COMPOSE_ARGS[*]}" "Vulkan override should be included"
}

test_auto_falls_back_to_cpu_when_no_gpu_backend_matches() {
  reset_state
  USE_GPU=1
  compose_setup_files
  assert_eq "cpu" "$GPU_BACKEND_SELECTED" "Auto GPU mode should fall back to CPU when no backend matches"
  assert_not_contains "docker-compose.gpu." "${COMPOSE_ARGS[*]}" "CPU fallback should not add a GPU override"
}

test_forced_backend_fails_when_host_is_incompatible() {
  reset_state
  USE_GPU=1
  GPU_BACKEND=nvidia
  if resolve_gpu_backend; then
    echo "FAIL: forced NVIDIA backend should fail on an incompatible host" >&2
    exit 1
  fi
}

test_host_access_still_stacks_with_gpu_overrides() {
  reset_state
  USE_GPU=1
  HOST_ACCESS=1
  FAKE_COMMANDS=(nvidia-smi nvidia-container-runtime nvidia-container-cli)
  FAKE_RUNTIMES_JSON='{"nvidia":{"path":"nvidia-container-runtime"}}'
  compose_setup_files
  assert_contains "docker-compose.gpu.nvidia.yml" "${COMPOSE_ARGS[*]}" "GPU override should remain enabled"
  assert_contains "docker-compose.host-access.yml" "${COMPOSE_ARGS[*]}" "Host access override should still be enabled"
}

test_auto_gpu_fallback_rebuilds_compose_args_for_cpu_mode() {
  reset_state
  USE_GPU=1
  HOST_ACCESS=1
  GPU_BACKEND_SELECTED=nvidia
  GPU_BACKEND_REQUESTED=auto
  COMPOSE_ARGS=(-f docker-compose.yml -f docker-compose.gpu.nvidia.yml -f docker-compose.host-access.yml)
  fallback_to_cpu_compose_mode
  assert_eq "cpu" "$GPU_BACKEND_SELECTED" "GPU fallback should switch the selected backend to CPU"
  assert_eq "nvidia" "$GPU_AUTO_FALLBACK_FROM" "Fallback should record the previous backend"
  assert_not_contains "docker-compose.gpu.nvidia.yml" "${COMPOSE_ARGS[*]}" "CPU fallback should drop the GPU override"
  assert_contains "docker-compose.host-access.yml" "${COMPOSE_ARGS[*]}" "CPU fallback should preserve host access overrides"
}

test_cpu_default_uses_base_compose_only
test_auto_selects_nvidia_backend
test_auto_selects_rocm_backend
test_auto_selects_vulkan_backend
test_auto_falls_back_to_cpu_when_no_gpu_backend_matches
test_forced_backend_fails_when_host_is_incompatible
test_host_access_still_stacks_with_gpu_overrides
test_auto_gpu_fallback_rebuilds_compose_args_for_cpu_mode

echo "compose_common_gpu_test: PASS"
