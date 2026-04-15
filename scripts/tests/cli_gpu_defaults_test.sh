#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

assert_contains() {
  local needle="$1"
  local haystack="$2"
  local message="$3"
  if [[ "$haystack" != *"$needle"* ]]; then
    echo "FAIL: $message" >&2
    echo "  missing: $needle" >&2
    echo "  output:  $haystack" >&2
    exit 1
  fi
}

start_default_output="$(
  CHESS_COACH_TEST_ARGS_ONLY=1 \
  "$REPO_ROOT/scripts/start.sh"
)"
assert_contains "USE_GPU=1" "$start_default_output" "start.sh should auto-try GPU by default"
assert_contains "GPU_BACKEND=auto" "$start_default_output" "start.sh should default to automatic backend selection"

start_cpu_output="$(
  CHESS_COACH_TEST_ARGS_ONLY=1 \
  "$REPO_ROOT/scripts/start.sh" --cpu
)"
assert_contains "USE_GPU=0" "$start_cpu_output" "start.sh --cpu should disable automatic GPU attempts"
assert_contains "GPU_BACKEND=cpu" "$start_cpu_output" "start.sh --cpu should force CPU mode"

setup_default_output="$(
  CHESS_COACH_TEST_ARGS_ONLY=1 \
  "$REPO_ROOT/scripts/setup.sh"
)"
assert_contains "USE_GPU=1" "$setup_default_output" "setup.sh should auto-try GPU by default"
assert_contains "GPU_BACKEND=auto" "$setup_default_output" "setup.sh should default to automatic backend selection"

setup_cpu_output="$(
  CHESS_COACH_TEST_ARGS_ONLY=1 \
  "$REPO_ROOT/scripts/setup.sh" --cpu
)"
assert_contains "USE_GPU=0" "$setup_cpu_output" "setup.sh --cpu should disable automatic GPU attempts"
assert_contains "GPU_BACKEND=cpu" "$setup_cpu_output" "setup.sh --cpu should force CPU mode"

echo "cli_gpu_defaults_test: PASS"
