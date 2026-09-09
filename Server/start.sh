#!/usr/bin/env bash
cd "$(dirname "$0")"
RAON_BASE=${RAON_BASE:-$HOME}
source $RAON_BASE/venv/raon/bin/activate
export RAON_VERIFY=0
export RAON_CONT=${RAON_CONT:-1}      # 1=억양까지 복제(tts_continuation), 0=음색만(tts)
# GPU 95.6GB 중 이 프로세스가 쓸 수 있는 비율.
#
# **0.70 -> 0.50 (2026-09-06). 답변 LLM 을 밖으로 뺐기 때문이다.**
# 0.70 이 필요했던 이유는 답변 생성이 지난 30턴을 통째로 다시 읽었기 때문이다.
# 어텐션을 eager 로 돌아 표를 통째로 만드는 탓에 낱말²로 커졌다 — 54턴 60.8GB,
# 70턴 OOM. 답변을 OpenAI 호환 엔드포인트로 넘기면 Raon 이 보는 것은 발화 하나와
# 답변 한두 문장뿐이라 그 봉우리가 사라진다. 실측으로 20턴 뒤 59.2GB -> 40.5GB.
#
# 0.50 = 47.8GB. 가중치가 39.9GB 이므로 8GB 여유다. 비운 자리는 답변 LLM
# (vLLM, 포트 8001)이 쓴다.
#
# **되돌리는 법** — 답변을 다시 Raon 이 하게 하면 반드시 0.70 으로 올릴 것.
# 안 올리면 20턴도 못 가서 죽는다. 합성 봉우리는 아직 안 쟀다(오늘 잰 20턴은
# 글 경로라 합성이 안 돌았다). OOM 이 나면 여기부터 올린다.
export RAON_MEM_FRACTION=${RAON_MEM_FRACTION:-0.50}
# 오래 켜 두면 파편화로 VRAM 이 기어오른다. 세션마다 /reset 을 해도 그렇다.
# probe 를 여섯 번 돌리는 같은 부하에서 39.8 -> 66.1GB (OOM) 였던 것이
# 이걸 켜면 39.8 -> 52.9GB 로 끝난다. 중간에 내려가기도 한다 - 회수가 된다.
# OOM 로그에서 PyTorch 가 직접 제안한 값이다.
export PYTORCH_CUDA_ALLOC_CONF=${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}
# 소리 온도. 코드 기본값은 1.2 인데 그러면 "국어책 읽는 느낌"이 남는다.
# 1.6 으로 올리면 그게 크게 줄어든다 — 청취로만 드러났고 체감속도 지표는
# 0.145 로 똑같았다. 대가는 이음매가 흔들리는 것(첫낱말 샘 7/45 -> 19/45)인데,
# 참조를 1세대 합성으로 쓰면 3/45 로 잡힌다. 자세한 것은
# docs/음성대화_작업현황.md 3순위의 네 칸 표.
export RAON_TEMP=${RAON_TEMP:-1.6}
# 생성 초반 무음 프레임. 비우면 모델 기본값(2). 0 으로 두면 참조가 통째로 샌다.
export RAON_CONT_SILENCE=${RAON_CONT_SILENCE:-}
export RAON_TOKEN=23605a891e448b5aa46f82c8640b554c
# ── 답변 LLM ──────────────────────────────────────────────────────
# **손으로 붙이던 것을 기본값으로 옮겼다 (2026-09-07).** 전에는 이 셋을 명령줄에
# 붙여 띄웠는데, 그러면 재시작 한 번에 Raon + 판정기로 조용히 되돌아간다.
# 실제로 그렇게 죽었다 — 답은 나오는데 Raon 이 만든 것이라 아무도 몰랐고,
# 되살아난 판정기가 다시 뽑기를 부르며 20턴도 못 가 CUDA OOM 이 났다.
# 밖으로 뺀 것이 결론인 이상 기본값이 결론이어야 한다.
#
# **되돌리려면 셋을 같이 되돌린다** — 하나만 되돌리면 죽거나 지어낸다:
#   RAON_LLM=raon RAON_JUDGE=1 RAON_MEM_FRACTION=0.70 ./start.sh
#
# vLLM(8001)이 먼저 떠 있어야 한다. vllm_start.sh 참고.
# enable_thinking 은 꺼 둔다 — 켜면 추론 토큰이 max_completion_tokens 를
# 먹고 빈 답이 나온다.
export RAON_LLM=${RAON_LLM:-exaone}
export RAON_JUDGE=${RAON_JUDGE:-0}
export RAON_LLM_URL=${RAON_LLM_URL:-http://127.0.0.1:8001/v1/chat/completions}
export RAON_LLM_EXTRA=${RAON_LLM_EXTRA:-'{"chat_template_kwargs":{"enable_thinking":false}}'}
if [ -f server.pid ] && ps -p $(cat server.pid) >/dev/null 2>&1; then
  echo "이미 실행 중 (PID $(cat server.pid))"; exit 0
fi
nohup python -m uvicorn app:app --host 0.0.0.0 --port 8000 > server.log 2>&1 &
echo $! > server.pid
echo "서버 시작 (PID $(cat server.pid)) — 준비까지 약 20초"
echo "로그: tail -f $(pwd)/server.log"
