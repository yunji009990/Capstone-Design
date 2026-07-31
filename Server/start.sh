#!/usr/bin/env bash
cd "$(dirname "$0")"
source $RAON_BASE/venv/raon/bin/activate
export RAON_VERIFY=0
if [ -f server.pid ] && ps -p $(cat server.pid) >/dev/null 2>&1; then
  echo "이미 실행 중 (PID $(cat server.pid))"; exit 0
fi
nohup python -m uvicorn app:app --host 0.0.0.0 --port 8000 > server.log 2>&1 &
echo $! > server.pid
echo "서버 시작 (PID $(cat server.pid)) — 준비까지 약 20초"
echo "로그: tail -f $(pwd)/server.log"
