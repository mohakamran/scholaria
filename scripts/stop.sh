#!/usr/bin/env bash
# Stop only the processes started by start.sh (tracked in storage/run/*.pid).
# Unrelated Python, Streamlit or Ollama processes are never touched.
set -uo pipefail
source "$(dirname "$0")/common.sh"
cd "$PROJECT_ROOT"

stop_service() {
  local name="$1" pattern="$2" file="$RUN_DIR/$1.pid"
  if [[ ! -f "$file" ]]; then
    info "$name: not started by this project"
    return
  fi
  local pid
  pid="$(cat "$file")"
  if ! pid_alive "$file"; then
    rm -f "$file"; info "$name: not running (removed stale pid file)"; return
  fi
  if ! pid_matches "$pid" "$pattern"; then
    warn "$name: pid $pid is not a '$pattern' process; leaving it alone"
    rm -f "$file"; return
  fi
  kill -TERM "$pid" 2>/dev/null
  for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
  if kill -0 "$pid" 2>/dev/null; then
    warn "$name did not exit gracefully; forcing"
    kill -KILL "$pid" 2>/dev/null
  fi
  rm -f "$file"
  ok "$name stopped"
}

echo "${BOLD}Stopping Scholaria${RESET}"
stop_service frontend "streamlit run frontend/app.py"
stop_service api "uvicorn app.main:app"
stop_service ollama "ollama serve"
