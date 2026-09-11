#!/usr/bin/env bash
# 등록 웹을 이 서버에서 띄운다. (2026-09-09)
#
# **왜 서버로 옮겼나** — 전에는 각자 PC 에서 띄웠는데, 새로 받은 사람은
# 파이썬·의존성·.env 를 다 갖춰야 해서 「서버가 안 켜진다」가 반복됐다.
# 유니티의 Tools > 서버 연결 상태 확인 > 「서버 켜기」가 `python` 을 그냥
# 부르는데, 그 이름이 어느 파이썬을 가리킬지는 기계마다 다르다.
#
# 옮길 수 있었던 이유는 **이 웹이 마이크를 안 쓰기 때문**이다. 음성·영상을
# 파일로 올리는 구조라 https 가 필요 없다. 브라우저 녹음을 넣게 되면
# 그때는 인증서를 붙여야 한다 — 크롬은 localhost 아닌 http 에서 마이크를 막는다.
#
# **RAON_URL 이 localhost 다.** 웹과 Raon 이 같은 기계에 있다.
#
# 못 하는 것 — **화자 분리.** nemo_env(1.8GB, 윈도우 경로)가 이 서버에 없다.
# 「한 명 (분리 안 함)」으로만 쓴다. 여러 사람이 섞인 녹음을 가르려면
# Web/extraction/README.md 를 보고 이 기계에 nemo_env 를 만들어야 한다.
cd "$(dirname "$0")/Web"
source "$HOME/venv/web/bin/activate"

PORT=${PORT:-8500}
if [ -f web.pid ] && ps -p "$(cat web.pid)" >/dev/null 2>&1; then
  echo "이미 실행 중 (PID $(cat web.pid))"; exit 0
fi

# 0.0.0.0 이라야 교내망에서 들어온다. 127.0.0.1 이면 이 기계에서만 보인다.
nohup python -m uvicorn app:app --host 0.0.0.0 --port "$PORT" > web.log 2>&1 &
echo $! > web.pid
echo "등록 웹 시작 (PID $(cat web.pid))"
echo "  http://220.69.208.201:$PORT"
echo "  로그: tail -f $(pwd)/web.log"
