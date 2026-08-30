#!/usr/bin/env bash
cd "$(dirname "$0")"
RAON_BASE=${RAON_BASE:-$HOME}
source $RAON_BASE/venv/raon/bin/activate
export RAON_VERIFY=0
export RAON_CONT=${RAON_CONT:-1}      # 1=억양까지 복제(tts_continuation), 0=음색만(tts)
# GPU 95.6GB 중 이 프로세스가 쓸 수 있는 비율. 0.60 이면 57.4GB 인데, 20턴쯤 대화하면
# 51GB 까지 올라가 여유가 없다. 실제로 56.5GB 에서 /chat 이 500 을 내기 시작했다.
# 0.70 이면 66.9GB 라 지금 최대치의 1.3배 여유가 생긴다. 상한을 아예 없애지는 않는다 —
# 폭주할 때 다른 프로세스까지 끌고 죽는 것을 막아준다.
export RAON_MEM_FRACTION=${RAON_MEM_FRACTION:-0.70}
# 생성 초반 무음 프레임. 비우면 모델 기본값(2). 첫 낱말이 뭉개지는 것과 관계를 본다.
export RAON_CONT_SILENCE=${RAON_CONT_SILENCE:-}
export RAON_TOKEN=23605a891e448b5aa46f82c8640b554c
if [ -f server.pid ] && ps -p $(cat server.pid) >/dev/null 2>&1; then
  echo "이미 실행 중 (PID $(cat server.pid))"; exit 0
fi
nohup python -m uvicorn app:app --host 0.0.0.0 --port 8000 > server.log 2>&1 &
echo $! > server.pid
echo "서버 시작 (PID $(cat server.pid)) — 준비까지 약 20초"
echo "로그: tail -f $(pwd)/server.log"
