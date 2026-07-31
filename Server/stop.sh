#!/usr/bin/env bash
cd "$(dirname "$0")"
[ -f server.pid ] || { echo "실행 중이 아닙니다"; exit 0; }
PID=$(cat server.pid)
kill $PID 2>/dev/null && echo "종료 요청 (PID $PID)"
for i in $(seq 20); do ps -p $PID >/dev/null 2>&1 || break; sleep 1; done
ps -p $PID >/dev/null 2>&1 && kill -9 $PID && echo "강제 종료"
rm -f server.pid
echo "VRAM 반납 완료"
