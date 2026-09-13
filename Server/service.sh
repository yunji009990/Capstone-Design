#!/usr/bin/env bash
set -euo pipefail
service_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
service_name="${1:-}"
service_action="${2:-status}"
# Optional non-secret routing for old commands after registration moves to webapp.
if [[ -f "$service_dir/service-paths.env" ]]; then source "$service_dir/service-paths.env"; fi
if [[ "$service_name" == session || "$service_name" == web || "$service_name" == tripo ]]; then
  if [[ -n "${PLATFORM_SERVICE_DIR:-}" && "$PLATFORM_SERVICE_DIR" != "$service_dir" ]]; then
    exec bash "$PLATFORM_SERVICE_DIR/service.sh" "$service_name" "$service_action"
  fi
fi
service_app_dir="$service_dir"
service_state_dir="$service_dir"
service_kind=api
case "$service_name" in
  session) service_module=registration.app; service_port=8000; service_env=registration; service_host=0.0.0.0 ;;
  dialogue) service_module=dialogue_server; service_port=8002; service_env=dialogue; service_host=0.0.0.0 ;;
  tts) service_module=tts_server; service_port=8003; service_env=qwentts; service_host=127.0.0.1 ;;
  web) service_module=app; service_port=8500; service_env=web; service_host=0.0.0.0
       service_app_dir="${WEB_APP_DIR:-$HOME/webapp/Web}"; service_state_dir="$service_app_dir" ;;
  tripo) service_module=model_worker; service_env=tripo; service_kind=worker
         service_app_dir="${SURVEY_APP_DIR:-$HOME/webapp/Survey}" ;;
  *) echo 'Usage: service.sh {session|web|tripo|dialogue|tts} {start|stop|status}' >&2; exit 2 ;;
esac
if [[ -f "$service_dir/$service_name.env" ]]; then
  set -a
  source "$service_dir/$service_name.env"
  set +a
fi
service_python="$HOME/venv/$service_env/bin/python"
service_pidfile="$service_state_dir/$service_name.pid"
service_pid=""
if [[ -f "$service_pidfile" ]]; then service_pid="$(cat "$service_pidfile")"; fi
service_running=false
if [[ "$service_pid" =~ ^[0-9]+$ ]] && kill -0 "$service_pid" 2>/dev/null; then
  service_command="$(tr '\0' ' ' < "/proc/$service_pid/cmdline")"
  service_identity="$service_module:app --app-dir $service_app_dir"
  if [[ "$service_kind" == worker ]]; then service_identity="$service_app_dir/model_worker.py"; fi
  if [[ "$service_command" != *"$service_identity"* ]]; then
    echo "PID file does not belong to $service_name; refusing to signal it." >&2
    exit 1
  fi
  service_running=true
fi
case "$service_action" in
  start)
    if $service_running; then echo "$service_name already running ($service_pid)"; exit 0; fi
    if [[ "$service_name" == session && -z "${SESSION_TOKEN:-}" ]] ||
       [[ "$service_name" == dialogue && -z "${DIALOGUE_TOKEN:-}" ]]; then
      echo "Configure the token in $service_name.env first." >&2; exit 1
    fi
    if [[ "$service_name" == dialogue && -n "${DIALOGUE_PERSONA_URL:-}" && -z "${DIALOGUE_PERSONA_TOKEN:-}" ]]; then
      echo 'Configure DIALOGUE_PERSONA_TOKEN for the registration API.' >&2; exit 1
    fi
    if [[ ! -x "$service_python" ]]; then echo "Missing independent environment: $service_python" >&2; exit 1; fi
    umask 077
    if [[ "$service_kind" == worker ]]; then
      export MODEL_WORKER_ENV="${MODEL_WORKER_ENV:-$HOME/webapp/Web/.env}"
      nohup "$service_python" "$service_app_dir/model_worker.py" \
        > "$service_state_dir/$service_name.log" 2>&1 < /dev/null &
    else
      nohup "$service_python" -m uvicorn "$service_module:app" --app-dir "$service_app_dir" \
      --host "$service_host" --port "$service_port" --ws-max-size 32768 \
      --ws-max-queue 16 --no-access-log > "$service_state_dir/$service_name.log" 2>&1 < /dev/null &
    fi
    echo $! > "$service_pidfile"
    echo "$service_name starting (PID $!); check its health/status endpoint and log."
    ;;
  stop)
    if ! $service_running; then echo "$service_name is stopped"; exit 0; fi
    kill -TERM "$service_pid"
    for ((service_wait=0; service_wait<40; service_wait++)); do
      if ! kill -0 "$service_pid" 2>/dev/null; then
        rm -f -- "$service_pidfile"
        echo "$service_name stopped"; exit 0
      fi
      sleep 0.5
    done
    echo "$service_name is still shutting down; inspect its log." >&2; exit 1
    ;;
  status)
    if $service_running; then echo "$service_name running ($service_pid)"; else echo "$service_name stopped"; fi
    ;;
  *) echo 'Action must be start, stop or status' >&2; exit 2 ;;
esac
