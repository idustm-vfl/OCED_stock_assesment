#!/usr/bin/env bash
# Workspace-aware shell launcher. Prompts to activate .venv if not already active.

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
VENV_PATH="$REPO_ROOT/.venv/bin/activate"

if [[ -z "${VIRTUAL_ENV:-}" && -f "$VENV_PATH" ]]; then
  read -r -p "Activate .venv now? [Y/n] " REPLY
  REPLY=${REPLY:-Y}
  case "$REPLY" in
    Y|y)
      # shellcheck disable=SC1090
      source "$VENV_PATH"
      ;;
    *)
      echo "Skipping venv activation.";;
  esac
fi

exec bash -l
