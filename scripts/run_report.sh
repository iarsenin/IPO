#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
VENV_DIR="${IPO_VENV:-$HOME/.venvs/ipo}"

if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
  mkdir -p "$(dirname "$VENV_DIR")"
  if command -v python3.12 >/dev/null 2>&1; then
    python3.12 -m venv --copies "$VENV_DIR"
  else
    python3 -m venv --copies "$VENV_DIR"
  fi
fi

source "$VENV_DIR/bin/activate"

if ! python -m pip --version >/dev/null 2>&1; then
  python -m ensurepip --upgrade
fi

REQUIREMENTS_HASH="$(shasum -a 256 "$PROJECT_ROOT/requirements.txt" | awk '{print $1}')"
REQUIREMENTS_STAMP="$VENV_DIR/.requirements.sha256"
if [[ ! -f "$REQUIREMENTS_STAMP" ]] || [[ "$(cat "$REQUIREMENTS_STAMP")" != "$REQUIREMENTS_HASH" ]]; then
  python -m pip install -r "$PROJECT_ROOT/requirements.txt"
  printf "%s\n" "$REQUIREMENTS_HASH" > "$REQUIREMENTS_STAMP"
fi

cd "$PROJECT_ROOT"
PYTHONPATH=src python -m ipo_update.runner "$@"
