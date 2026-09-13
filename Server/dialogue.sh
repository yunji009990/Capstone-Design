#!/usr/bin/env bash
# Dialogue API or optional TTS only. LLM lifecycle remains explicit.
set -euo pipefail
dialogue_script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
case "${1:-}" in
  api) shift; exec bash "$dialogue_script_dir/service.sh" dialogue "$@" ;;
  tts) exec bash "$dialogue_script_dir/service.sh" "$@" ;;
  *) echo 'Usage: dialogue.sh {api|tts} {start|stop|status}' >&2; exit 2 ;;
esac
