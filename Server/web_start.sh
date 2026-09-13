#!/usr/bin/env bash
# Compatibility entrypoint for the independently managed platform web.
set -euo pipefail
exec bash "${PLATFORM_SERVICE_DIR:-$HOME/webapp/Server}/service.sh" web start
