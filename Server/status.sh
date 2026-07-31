#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ -f server.pid ] && ps -p $(cat server.pid) >/dev/null 2>&1; then
  echo "실행 중 (PID $(cat server.pid))"
  curl -s localhost:8000/health && echo
else
  echo "정지 상태"
fi
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
