#!/usr/bin/env bash
# deploy_platform.sh — 웹·등록·Tripo(platform) 코드를 운영 서버에 한 번에 올린다.
#
#   bash tools/deploy_platform.sh
#
# 하는 일: 묶음 만들기 → 전송 → 백업 → 덮어쓰기 → 워커 재기동 → 확인.
# 대화·TTS(~/capstone-server)는 건드리지 않는다. 그쪽은 service_bundle.py dialogue 로 따로 간다.
#
# 비밀값은 다루지 않는다. .env 는 묶음에 없고(.env.example 만 들어간다) 서버의 것을 그대로 쓴다.
# 접속 비밀번호는 ssh/scp 가 직접 물어본다 — 이 스크립트는 저장하지도 받지도 않는다.
#
# 워커는 파일만 바꾸면 반영되지 않는다. 파이썬은 임포트 시점에 코드를 메모리에 올리므로
# 반드시 프로세스를 다시 띄워야 한다. 그래서 재기동까지가 이 스크립트의 범위다.
#
# 되돌리기: 서버에 남는 ~/webapp_backup_<시각>/ 을 webapp 위에 되붙이고 워커를 다시 띄운다.
# 마지막에 정확한 명령을 찍어 준다.

set -euo pipefail

REMOTE="${REMOTE:-crc_unity@220.69.208.201}"
REMOTE_ROOT="${REMOTE_ROOT:-webapp}"          # 홈 기준 상대 경로
SERVICES="${SERVICES:-tripo}"                 # 재기동할 서비스. 예: SERVICES="tripo web"
ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
ZIP="$ROOT/tools/_work/service_split_20260912/platform.zip"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# ── 1. 묶음을 새로 만든다 ────────────────────────────────────
say "배포 묶음 만들기"
python "$ROOT/tools/service_bundle.py" platform
[ -f "$ZIP" ] || { echo "묶음이 없습니다: $ZIP" >&2; exit 1; }
ls -l "$ZIP"

# ── 2. 전송 ──────────────────────────────────────────────────
say "전송 ($REMOTE)"
scp "$ZIP" "$REMOTE:~/platform.zip"

# ── 3. 서버에서 백업 → 덮어쓰기 → 재기동 → 확인 ───────────────
say "서버에서 적용"
ssh "$REMOTE" REMOTE_ROOT="$REMOTE_ROOT" SERVICES="$SERVICES" 'bash -s' <<'REMOTE_SCRIPT'
set -euo pipefail
ROOT="$HOME/$REMOTE_ROOT"
STAMP="$(date +%Y%m%dT%H%M%S)"
BACKUP="$HOME/webapp_backup_$STAMP"

[ -d "$ROOT" ] || { echo "대상 폴더가 없습니다: $ROOT" >&2; exit 1; }
unzip -t ~/platform.zip > /dev/null || { echo "묶음이 깨졌습니다" >&2; exit 1; }

# 덮어쓸 파일만 골라 백업한다. 데이터·세션 폴더는 건드리지 않는다.
echo "-- 백업: $BACKUP"
mkdir -p "$BACKUP"
unzip -Z1 ~/platform.zip | grep -v '/$' > /tmp/platform_files.txt
while IFS= read -r f; do
  [ -f "$ROOT/$f" ] || continue
  mkdir -p "$BACKUP/$(dirname "$f")"
  cp -a "$ROOT/$f" "$BACKUP/$f"
done < /tmp/platform_files.txt
echo "   백업한 파일 $(find "$BACKUP" -type f | wc -l)개"

# 돌고 있는 워커를 먼저 세운다. pidfile 에 없는(수동으로 띄운) 프로세스도 찾아서 세운다.
if echo "$SERVICES" | grep -qw tripo; then
  echo "-- 워커 정지"
  bash "$ROOT/Server/platform.sh" tripo stop || true
  for pid in $(pgrep -f "$ROOT/Survey/model_worker.py" || true); do
    echo "   pidfile 밖의 워커 PID $pid 에 TERM"
    kill -TERM "$pid" 2>/dev/null || true
  done
  for _ in $(seq 1 60); do
    pgrep -f "$ROOT/Survey/model_worker.py" > /dev/null || break
    sleep 1
  done
  if pgrep -f "$ROOT/Survey/model_worker.py" > /dev/null; then
    echo "워커가 60초 안에 안 멈췄습니다. 배포를 중단합니다." >&2
    exit 1
  fi
fi

echo "-- 덮어쓰기"
unzip -o ~/platform.zip -d "$ROOT" > /dev/null
echo "   Survey/"
ls -l --time-style=long-iso "$ROOT/Survey/model_worker.py" "$ROOT/Survey/core/"*.py | sed 's/^/     /'

echo "-- 재기동: $SERVICES"
for svc in $SERVICES; do
  [ "$svc" = tripo ] || bash "$ROOT/Server/platform.sh" "$svc" stop || true
  bash "$ROOT/Server/platform.sh" "$svc" start
done

sleep 3
echo "-- 상태"
for svc in $SERVICES; do bash "$ROOT/Server/platform.sh" "$svc" status; done

if echo "$SERVICES" | grep -qw tripo; then
  echo "-- 워커 큐"
  "$HOME/venv/tripo/bin/python" "$ROOT/Survey/model_worker.py" --status || true
  echo "-- 워커 로그 (마지막 20줄)"
  tail -n 20 "$ROOT/Survey/tripo.log" 2>/dev/null | sed 's/^/     /' || echo "     (로그 없음)"
fi

echo
echo "되돌리려면:"
echo "  cp -a $BACKUP/. $ROOT/ && bash $ROOT/Server/platform.sh tripo stop && bash $ROOT/Server/platform.sh tripo start"
REMOTE_SCRIPT

say "끝"
