#!/usr/bin/env bash
# Shared helpers for setup/start/stop scripts. Sourced, not executed.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$PROJECT_ROOT/.venv"
PY="$VENV/bin/python"
RUN_DIR="$PROJECT_ROOT/storage/run"
LOG_DIR="$PROJECT_ROOT/storage/logs"

# Load .env (if present) so ports/URLs are configurable.
if [[ -f "$PROJECT_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  set +a
fi

API_HOST="${RA_API_HOST:-127.0.0.1}"
API_PORT="${RA_API_PORT:-8000}"
FRONTEND_PORT="${RA_FRONTEND_PORT:-8501}"
OLLAMA_URL="${RA_OLLAMA_URL:-http://127.0.0.1:11434}"
DEFAULT_MODEL="${RA_DEFAULT_MODEL:-qwen3:4b-instruct-2507-q4_K_M}"
FALLBACK_MODEL="${RA_FALLBACK_MODEL:-qwen3:1.7b}"

if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'
else
  BOLD=""; GREEN=""; YELLOW=""; RED=""; RESET=""
fi

ok()   { echo "${GREEN}✔${RESET} $*"; }
warn() { echo "${YELLOW}!${RESET} $*"; }
fail() { echo "${RED}✘${RESET} $*"; }
info() { echo "  $*"; }

# pid_alive <pidfile> -> 0 if the recorded process is running
pid_alive() {
  local file="$1"
  [[ -f "$file" ]] || return 1
  local pid
  pid="$(cat "$file" 2>/dev/null)"
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
}

# pid_matches <pid> <pattern> -> 0 if the process command line contains pattern
pid_matches() {
  local cmd
  cmd="$(ps -p "$1" -o command= 2>/dev/null)"
  [[ -n "$cmd" && "$cmd" == *"$2"* ]]
}

port_in_use() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

ollama_up() {
  curl -fsS -m 3 "$OLLAMA_URL/api/version" >/dev/null 2>&1
}

model_installed() {
  # Exact name match against Ollama's installed model list.
  local name="$1"
  [[ "$name" == *:* ]] || name="$name:latest"
  curl -fsS -m 5 "$OLLAMA_URL/api/tags" 2>/dev/null | "$PY" -c '
import json, sys
target = sys.argv[1]
names = {m.get("name") for m in json.load(sys.stdin).get("models", [])}
sys.exit(0 if target in names else 1)' "$name" 2>/dev/null
}

wait_for_url() {
  # wait_for_url <url> <seconds>
  local url="$1" seconds="$2" i
  for ((i = 0; i < seconds * 2; i++)); do
    curl -fsS -m 2 "$url" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  return 1
}
