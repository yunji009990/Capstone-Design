"""사진 → T포즈 → 3D 모델을 한 화면에서 보는 뷰어.

왜 필요한가
───────────
지금까지는 단계별 산출물이 폴더에 흩어져 있어서, 어디서 무너지는지 보려면 파일을
하나씩 열어야 했다. 세 단계를 나란히 놓고 얼굴을 확대해 보면 숫자가 말하는 것을
눈으로 확인할 수 있다. 운영 큐 상태도 같이 띄워 지금 서버가 뭘 하는지 함께 본다.

    python tools/pipeline_view/server.py
    # 열리는 주소: http://127.0.0.1:8779/

무엇을 읽는가
─────────────
tools/_work 아래 두 종류를 자동으로 찾는다. 새 폴더를 넣으면 화면이 알아서 집어온다.

  세션 폴더   <32자리 16진수>/  front.*  tpose.*  model.glb
              (서버에서 scp 로 받은 것)
  실험 폴더   tripo_trial_*/    reference.png  reference_tpose.png
              generated.glb  rigged.glb  animated.glb

face_similarity.py 가 trial.json 에 남긴 face_identity 가 있으면 같이 보여준다.

운영 큐와 진행 중인 작업
────────────────────────
페이지가 운영 서버를 직접 부르면 CORS 에 막히므로 이 서버가 대신 물어 중계한다.

  /api/status  웹의 읽기 전용 /status. 큐 개수만 온다.
  /api/jobs    ssh 로 운영 DB 를 읽어 작업별 단계까지 가져온다. 사진을 올리면
               T포즈 → 3D 생성 → 리깅 → 앉기 가 차례로 켜지는 게 보인다.
  /api/remote  그 세션의 front·tpose 이미지를 ssh 로 가져와 중계한다. 생성이
               도는 중에도 T포즈가 나오는 즉시 화면에 뜬다.

ssh 는 키 인증이 걸려 있어야 하고(BatchMode), 안 되면 그 영역만 조용히 비운다.
읽기만 하고 아무것도 바꾸지 않는다. 서버가 꺼져 있어도 로컬 산출물 보기는 그대로다.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

TOOLS = Path(__file__).resolve().parents[1]
WORK = TOOLS / "_work"
PORT = 8779
UPSTREAM = "http://220.69.208.201:8500/status"
REMOTE = "crc_unity@220.69.208.201"
REMOTE_PY = "~/venv/tripo/bin/python"
CACHE = WORK / "remote_cache"

SESSION_DIR = re.compile(r"^[0-9a-f]{32}$")
REMOTE_FILE = re.compile(r"^(front|tpose)\.(png|jpg|jpeg)$")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")

# 운영 DB 에서 작업과 단계를 읽어 JSON 한 줄로 내놓는다. 서버에는 이 파일을 두지 않고
# 매번 stdin 으로 보낸다 — 배포 대상이 늘지 않고, 서버 파일을 건드리지도 않는다.
REMOTE_SNIPPET = r"""
import json, os, sys
os.chdir(os.path.expanduser("~/webapp/Survey"))
sys.path.insert(0, ".")
from core import database

ROOT = os.path.expanduser("~/webapp/Survey/data/sessions")
with database.connect() as conn:
    rows = [dict(r) for r in conn.execute(
        "SELECT session_id, state, error, steps_json, updated_at FROM model_jobs "
        "ORDER BY updated_at DESC LIMIT 12")]
out = []
for row in rows:
    steps = json.loads(row["steps_json"])
    folder = os.path.join(ROOT, row["session_id"])
    files = os.listdir(folder) if os.path.isdir(folder) else []
    out.append({
        "sid": row["session_id"],
        "state": row["state"],
        "error": row["error"] or "",
        "updated": row["updated_at"],
        "fixture": "fixture" in steps,
        "steps": {k: (v.get("state", "done") if isinstance(v, dict) else "done")
                  for k, v in steps.items() if k != "input"},
        "files": sorted(f for f in files if f.startswith(("front.", "tpose."))),
        "model_bytes": (os.path.getsize(os.path.join(folder, "model.glb"))
                        if "model.glb" in files else 0),
    })
print(json.dumps({"jobs": out}, ensure_ascii=False))
"""


def ssh_run(args, stdin=b"", timeout=25):
    """키 인증으로만 붙는다. 비밀번호를 물어야 하는 상황이면 그냥 실패시킨다."""
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", REMOTE] + args
    return subprocess.run(command, input=stdin, capture_output=True, timeout=timeout)


_jobs_cache: tuple[float, dict] = (0.0, {})


def remote_jobs() -> dict:
    """3초 안에 다시 물으면 앞 결과를 준다. 5초마다 도는 화면이 ssh 를 겹쳐 부르지 않게."""
    global _jobs_cache
    now = time.monotonic()
    if now - _jobs_cache[0] < 3:
        return _jobs_cache[1]
    try:
        done = ssh_run([REMOTE_PY, "-"], REMOTE_SNIPPET.encode())
        payload = json.loads(done.stdout.decode("utf-8")) if done.returncode == 0 else {
            "error": done.stderr.decode("utf-8", "replace")[-300:] or "ssh 실패"}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        payload = {"error": str(exc)}
    _jobs_cache = (now, payload)
    return payload


def remote_file(sid: str, name: str) -> Path | None:
    """세션 폴더의 이미지를 한 번만 가져와 로컬에 둔다. 실패는 캐시하지 않는다."""
    if not SESSION_DIR.match(sid) or not REMOTE_FILE.match(name):
        return None
    dest = CACHE / sid / name
    if dest.is_file() and dest.stat().st_size:
        return dest
    try:
        done = ssh_run(["cat", f"webapp/Survey/data/sessions/{sid}/{name}"], timeout=40)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0 or not done.stdout:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(done.stdout)
    return dest


def first(directory: Path, *stems: str) -> str | None:
    """stem 에 맞는 이미지 하나를 골라 tools/ 기준 상대 경로로 돌려준다."""
    for stem in stems:
        for ext in IMAGE_EXT:
            candidate = directory / (stem + ext)
            if candidate.is_file():
                return candidate.relative_to(TOOLS).as_posix()
    return None


def model(directory: Path, *names: str) -> str | None:
    for name in names:
        candidate = directory / name
        if candidate.is_file():
            return candidate.relative_to(TOOLS).as_posix()
    return None


def megabytes(path: str | None) -> float | None:
    if path is None:
        return None
    return round((TOOLS / path).stat().st_size / 1048576, 2)


def scores(directory: Path) -> list[dict]:
    """face_similarity 가 남긴 점수. 실험 폴더는 trial.json, 세션 폴더는 face_report.json."""
    for name in ("trial.json", "face_report.json"):
        path = directory / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        pairs = (data.get("face_identity") or {}).get("pairs")
        if pairs:
            return pairs
    return []


def collect() -> list[dict]:
    cases = []
    if not WORK.is_dir():
        return cases
    for directory in sorted(WORK.iterdir(), key=lambda p: -p.stat().st_mtime):
        if not directory.is_dir():
            continue
        if SESSION_DIR.match(directory.name):
            kind, source, tpose = "세션", first(directory, "front"), first(directory, "tpose")
            stages = [("모델", model(directory, "model.glb"))]
        elif directory.name.startswith("tripo_trial_"):
            kind = "실험"
            source, tpose = first(directory, "reference"), first(directory, "reference_tpose")
            stages = [(label, model(directory, name)) for label, name in
                      (("생성", "generated.glb"), ("리깅", "rigged.glb"),
                       ("애니메이션", "animated.glb"), ("동작 팩", "animated_pack.glb"))]
            # gltf-transform 으로 줄인 것들. 원본 2백만 면은 Quest 에 못 올리므로
            # 실제로 쓸 후보는 이쪽이다. 비율이 이름에 들어 있어 그대로 라벨로 쓴다.
            stages += [(f"감축 {p.stem.split('_r')[-1]}", p.relative_to(TOOLS).as_posix())
                       for p in sorted(directory.glob("animated_r*.glb"))]
        else:
            continue
        stages = [{"label": label, "path": path, "mb": megabytes(path)}
                  for label, path in stages if path]
        if not (source or tpose or stages):
            continue
        cases.append({
            "id": directory.name, "kind": kind,
            "updated": int(directory.stat().st_mtime),
            "source": source, "tpose": tpose, "stages": stages,
            "scores": scores(directory),
        })
    return cases


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(TOOLS), **kwargs)

    def log_message(self, *args):
        pass

    def send_json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/":
            self.path = "/pipeline_view/index.html"
        elif route == "/api/cases":
            return self.send_json({"cases": collect()})
        elif route == "/api/status":
            # 운영 서버가 꺼져 있어도 페이지는 계속 동작해야 한다.
            try:
                with urllib.request.urlopen(UPSTREAM, timeout=6) as response:
                    return self.send_json(json.loads(response.read().decode("utf-8")))
            except (urllib.error.URLError, OSError, ValueError) as exc:
                return self.send_json({"error": str(exc)}, 200)
        elif route == "/api/jobs":
            return self.send_json(remote_jobs())
        elif route == "/api/remote":
            query = parse_qs(urlparse(self.path).query)
            path = remote_file((query.get("sid") or [""])[0], (query.get("name") or [""])[0])
            if path is None:
                # 아직 안 만들어진 단계일 수 있다. 화면은 다음 회차에 다시 묻는다.
                self.send_response(404)
                self.end_headers()
                return None
            self.path = "/" + path.relative_to(TOOLS).as_posix()
        return super().do_GET()


if __name__ == "__main__":
    print(f"http://127.0.0.1:{PORT}/")
    print(f"읽는 곳: {WORK}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
