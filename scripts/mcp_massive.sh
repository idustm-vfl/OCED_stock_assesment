#!/bin/bash
set -euo pipefail

exec /home/vscode/.local/bin/uvx --from git+https://github.com/massive-com/mcp_massive@v0.6.0 mcp_massive "$@"
