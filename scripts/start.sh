#!/usr/bin/env bash
# Start Ollama (if needed), the FastAPI backend and the Streamlit frontend.
# Only processes started here are recorded in storage/run/*.pid; stop.sh stops
# exactly those processes.
set -euo pipefail
source "$(dirname "$0")/common.sh"
cd "$PROJECT_ROOT"
mkdir -p "$RUN_DIR" "$LOG_DIR"

OPEN_BROWSER=0
[[ "${1:-}" == "--open" ]] && OPEN_BROWSER=1

STARTED=()   # names of services started in this run (for cleanup on failure)

cleanup_on_failure() {
  fail "Startup failed — stopping services started by this script."
  for name in ${STARTED[@]+"${STARTED[@]}"}; do
    local file="$RUN_DIR/$name.pid"
    if pid_alive "$file"; then kill "$(cat "$file")" 2>/dev/null || true; fi
    rm -f "$file"
  done
  exit 1
}

echo "${BOLD}Starting Scholaria${RESET}"

# 1. Virtual environment ---------------------------------------------------------
if [[ ! -x "$PY" ]] || ! "$PY" -c 'import fastapi, streamlit, uvicorn' 2>/dev/null; then
  fail "Virtual environment missing or incomplete. Run ./scripts/setup.sh first."
  exit 1
fi
ok "Virtual environment OK"

# Already running?
if pid_alive "$RUN_DIR/api.pid" && pid_alive "$RUN_DIR/frontend.pid"; then
  ok "Already running."
  info "Frontend: http://127.0.0.1:$FRONTEND_PORT   API: http://$API_HOST:$API_PORT/docs"
  exit 0
fi

# 2-3. Ollama -------------------------------------------------------------------------
if ollama_up; then
  ok "Ollama already running at $OLLAMA_URL"
elif command -v ollama >/dev/null 2>&1; then
  info "Starting Ollama (one model at a time, keep-alive 2m)…"
  OLLAMA_HOST="${OLLAMA_URL#http://}" OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_NUM_PARALLEL=1 \
    OLLAMA_KEEP_ALIVE="${RA_KEEP_ALIVE:-2m}" nohup ollama serve >>"$LOG_DIR/ollama.log" 2>&1 &
  echo $! >"$RUN_DIR/ollama.pid"
  STARTED+=("ollama")
  if wait_for_url "$OLLAMA_URL/api/version" 20; then ok "Ollama started"; else
    warn "Ollama did not respond; see storage/logs/ollama.log. Continuing without it."
  fi
elif [[ -d /Applications/Ollama.app ]]; then
  info "Opening Ollama.app…"
  open -a Ollama
  wait_for_url "$OLLAMA_URL/api/version" 20 && ok "Ollama app started" || warn "Ollama app did not respond yet"
else
  warn "Ollama is not installed — the UI will start, but AI features need Ollama."
  info "Install: brew install ollama   then: ollama pull $DEFAULT_MODEL"
fi

# 4. Model availability -------------------------------------------------------------------
if ollama_up; then
  if model_installed "$DEFAULT_MODEL"; then ok "Model available: $DEFAULT_MODEL"
  elif model_installed "$FALLBACK_MODEL"; then warn "Default model missing; using fallback $FALLBACK_MODEL"
    info "Install the default with: ollama pull $DEFAULT_MODEL"
  else warn "No configured model installed. Run: ollama pull $DEFAULT_MODEL"; fi
fi

# 5-6. FastAPI ------------------------------------------------------------------------------
if pid_alive "$RUN_DIR/api.pid"; then
  ok "API already running"
else
  if port_in_use "$API_PORT"; then fail "Port $API_PORT is already in use by another program."; cleanup_on_failure; fi
  nohup "$PY" -m uvicorn app.main:app --app-dir backend --host "$API_HOST" --port "$API_PORT" \
    --log-level warning >>"$LOG_DIR/api.log" 2>&1 &
  echo $! >"$RUN_DIR/api.pid"
  STARTED+=("api")
  if wait_for_url "http://$API_HOST:$API_PORT/api/health" 40; then ok "API healthy at http://$API_HOST:$API_PORT"
  else fail "API did not become healthy. Last log lines:"; tail -n 20 "$LOG_DIR/api.log" || true; cleanup_on_failure; fi
fi

# 7. Streamlit --------------------------------------------------------------------------------
if pid_alive "$RUN_DIR/frontend.pid"; then
  ok "Frontend already running"
else
  if port_in_use "$FRONTEND_PORT"; then fail "Port $FRONTEND_PORT is already in use by another program."; cleanup_on_failure; fi
  RA_API_URL="http://$API_HOST:$API_PORT" nohup "$PY" -m streamlit run frontend/app.py \
    --server.address 127.0.0.1 --server.port "$FRONTEND_PORT" --server.headless true \
    --browser.gatherUsageStats false >>"$LOG_DIR/frontend.log" 2>&1 &
  echo $! >"$RUN_DIR/frontend.pid"
  STARTED+=("frontend")
  if wait_for_url "http://127.0.0.1:$FRONTEND_PORT/_stcore/health" 40; then ok "Frontend running"
  else fail "Frontend did not start. Last log lines:"; tail -n 20 "$LOG_DIR/frontend.log" || true; cleanup_on_failure; fi
fi

# 8. URLs -----------------------------------------------------------------------------------
echo
echo "${BOLD}Ready.${RESET}"
info "Application:  ${BOLD}http://127.0.0.1:$FRONTEND_PORT${RESET}"
info "API:          http://$API_HOST:$API_PORT"
info "API docs:     http://$API_HOST:$API_PORT/docs"
info "Logs:         storage/logs/   ·   Stop with: ./scripts/stop.sh"
[[ "$OPEN_BROWSER" == 1 ]] && open "http://127.0.0.1:$FRONTEND_PORT"
exit 0
