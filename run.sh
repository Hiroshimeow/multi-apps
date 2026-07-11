#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
    echo "Error: uv is not installed or is not in PATH." >&2
    exit 1
fi

exec uv run python cli.py "$@"
