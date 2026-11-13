#!/usr/bin/env bash
# One-time setup: checks prerequisites, creates the virtual environment, installs
# dependencies, prepares storage and configuration, and migrates the database.
# It never installs system-wide packages and never overwrites an existing .env.
set -euo pipefail
source "$(dirname "$0")/common.sh"
cd "$PROJECT_ROOT"

echo "${BOLD}Scholaria — setup${RESET}"
echo

# 1. Prerequisites ---------------------------------------------------------------
echo "${BOLD}1. Checking prerequisites${RESET}"
if [[ "$(uname -s)" == "Darwin" ]]; then ok "macOS $(sw_vers -productVersion)"; else warn "Not macOS ($(uname -s)); continuing"; fi
if [[ "$(uname -m)" == "arm64" ]]; then ok "Apple Silicon (arm64)"; else warn "Architecture $(uname -m) (expected arm64)"; fi
if command -v brew >/dev/null 2>&1; then ok "Homebrew found"; else warn "Homebrew not found (optional): https://brew.sh"; fi
FREE_GB=$(df -g "$PROJECT_ROOT" | awk 'NR==2 {print $4}')
if [[ "${FREE_GB:-0}" -ge 10 ]]; then ok "Free disk space: ${FREE_GB} GB"; else warn "Low disk space: ${FREE_GB} GB (models need ~4 GB)"; fi

PYTHON311=""
if [[ -x "$PY" ]] && "$PY" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 11) else 1)' 2>/dev/null; then
  ok "Existing virtual environment uses Python 3.11"
elif command -v python3.11 >/dev/null 2>&1; then
  PYTHON311="$(command -v python3.11)"
  ok "Python 3.11 found: $PYTHON311"
elif command -v uv >/dev/null 2>&1; then
  ok "uv found (will provide a project-local Python 3.11)"
else
  fail "Python 3.11 not found."
  info "Install it with:  brew install python@3.11   (or install uv: brew install uv)"
  exit 1
fi

if command -v ollama >/dev/null 2>&1; then
  ok "Ollama CLI found ($(ollama --version 2>/dev/null | head -1))"
elif [[ -d /Applications/Ollama.app ]]; then
  ok "Ollama.app found"
else
  warn "Ollama is not installed. Install the native Apple Silicon build:"
  info "brew install ollama        (or download from https://ollama.com/download/mac)"
fi
echo

# 2-3. Virtual environment and dependencies ----------------------------------------
echo "${BOLD}2. Python environment${RESET}"
if [[ ! -x "$PY" ]]; then
  if [[ -n "$PYTHON311" ]]; then
    "$PYTHON311" -m venv "$VENV"
  else
    uv venv --python 3.11 "$VENV"
  fi
  ok "Created .venv"
else
  ok "Using existing .venv ($("$PY" --version))"
fi

echo "${BOLD}3. Installing Python dependencies${RESET}"
if [[ -x "$VENV/bin/pip" ]]; then
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -r requirements.txt
else
  uv pip install --quiet --python "$PY" -r requirements.txt
fi
ok "Dependencies installed"
echo

# 4. Storage --------------------------------------------------------------------------
echo "${BOLD}4. Storage directories${RESET}"
mkdir -p storage/uploads storage/datasets storage/exports storage/logs storage/run
ok "storage/ ready"

# 5. Configuration ----------------------------------------------------------------------
echo "${BOLD}5. Configuration${RESET}"
if [[ -f .env ]]; then
  ok ".env exists (left unchanged)"
else
  cp .env.example .env
  ok "Created .env from .env.example"
fi

# 6-7. Database ---------------------------------------------------------------------------
echo "${BOLD}6. Database (SQLite + Alembic migrations)${RESET}"
"$PY" -c 'import sqlite3; c = sqlite3.connect(":memory:"); c.execute("create virtual table t using fts5(x)")' \
  && ok "SQLite FTS5 available" || warn "SQLite FTS5 unavailable; a slower fallback search will be used"
(cd backend && "$VENV/bin/alembic" upgrade head)
ok "Database migrated to latest version"
echo

# 8-9. Ollama -------------------------------------------------------------------------------
echo "${BOLD}7. Ollama${RESET}"
if ollama_up; then
  ok "Ollama server reachable at $OLLAMA_URL"
  if model_installed "$DEFAULT_MODEL"; then ok "Default model installed: $DEFAULT_MODEL"
  else warn "Default model missing. Install it with:"; info "ollama pull $DEFAULT_MODEL"; fi
  if model_installed "$FALLBACK_MODEL"; then ok "Fallback model installed: $FALLBACK_MODEL"
  else warn "Fallback model missing (optional, smaller):"; info "ollama pull $FALLBACK_MODEL"; fi
else
  warn "Ollama server not running (start.sh will try to start it)."
  info "After installing Ollama, download the models:"
  info "ollama pull $DEFAULT_MODEL"
  info "ollama pull $FALLBACK_MODEL"
fi
echo
ok "${BOLD}Setup complete.${RESET} Start the app with:  ./scripts/start.sh   (or: make start)"
