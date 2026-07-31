#!/usr/bin/env bash
cd "$(dirname "$0")"
RAON_BASE=${RAON_BASE:-$HOME}
source $RAON_BASE/venv/raon/bin/activate
export RAON_VERIFY=0
export RAON_CONT=${RAON_CONT:-1}      # 1=억양까지 복제(tts_continuation), 0=음색만(tts)
export RAON_TOKEN=23605a891e448b5aa46f82c8640b554c
if [ -f server.pid ] && ps -p $(cat server.pid) >/dev/null 2>&1; then
  echo "이미 실행 중 (PID $(cat server.pid))"; exit 0
fi
nohup python -m uvicorn app:app --host 0.0.0.0 --port 8000 > server.log 2>&1 &
echo $! > server.pid
echo "서버 시작 (PID $(cat server.pid)) — 준비까지 약 20초"
echo "로그: tail -f $(pwd)/server.log"
