#!/usr/bin/env bash
# Web/registration/Tripo only. Does not start or stop AI/GPU processes.
set -euo pipefail
platform_script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
case "${1:-}" in
  web|session|tripo) exec bash "$platform_script_dir/service.sh" "$@" ;;
  *) echo 'Usage: platform.sh {web|session|tripo} {start|stop|status}' >&2; exit 2 ;;
esac
