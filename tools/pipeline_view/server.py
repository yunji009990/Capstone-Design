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

운영 큐
───────
페이지가 운영 서버를 직접 부르면 CORS 에 막히므로 이 서버가 대신 물어 중계한다.
읽기 전용 `/status` 하나만 부르고 비밀값은 다루지 않는다. 서버가 꺼져 있거나
주소가 막혀 있어도 로컬 산출물 보기는 그대로 동작한다.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

TOOLS = Path(__file__).resolve().parents[1]
WORK = TOOLS / "_work"
PORT = 8779
UPSTREAM = "http://220.69.208.201:8500/status"

SESSION_DIR = re.compile(r"^[0-9a-f]{32}$")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")


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
        return super().do_GET()


if __name__ == "__main__":
    print(f"http://127.0.0.1:{PORT}/")
    print(f"읽는 곳: {WORK}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
