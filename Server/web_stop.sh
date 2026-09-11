#!/usr/bin/env bash
cd "$(dirname "$0")/Web"
[ -f web.pid ] || { echo "web.pid 가 없습니다"; exit 0; }
PID=$(cat web.pid)
if ps -p "$PID" >/dev/null 2>&1; then
  kill "$PID"; echo "종료 요청 (PID $PID)"
  for i in $(seq 10); do ps -p "$PID" >/dev/null 2>&1 || break; sleep 1; done
else
  echo "이미 죽어 있습니다 (PID $PID)"
fi
rm -f web.pid
