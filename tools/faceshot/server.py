"""GLB 의 얼굴을 정면에서 찍어 PNG 로 남기는 보조 서버.

face_similarity.py 는 이미지끼리만 비교하므로, 완성된 모델을 비교에 넣으려면
모델 얼굴을 찍은 사진이 필요하다. Unity 를 열지 않고 브라우저의 three.js 로 찍는다.

    python tools/faceshot/server.py
    # 그다음 브라우저에서 (out 은 저장할 이름)
    http://127.0.0.1:8778/faceshot/index.html?glb=../_work/tripo_trial_<이름>/generated.glb&out=<이름>

방위 네 장(az0/az90/az180/az270)을 찍어 tools/_work/faceshot_shots/ 에 저장한다.
모델마다 정면이 다른 축을 보고 있어서, 어느 것이 얼굴인지는 검출기가 고르게 둔다.
서버는 tools/ 아래만 내보내고 127.0.0.1 에만 묶는다.
"""
import base64
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

TOOLS = Path(__file__).resolve().parents[1]
OUT = TOOLS / "_work" / "faceshot_shots"
PORT = 8778


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(TOOLS), **kwargs)

    def log_message(self, *args):
        pass

    def do_POST(self):
        query = parse_qs(urlparse(self.path).query)
        # 페이지가 보낸 이름을 그대로 경로로 쓰지 않는다.
        requested = (query.get("name") or ["shot"])[0]
        name = "".join(c for c in requested if c.isalnum() or c in "_-") or "shot"
        payload = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        (OUT / (name + ".png")).write_bytes(base64.b64decode(payload.split(",", 1)[-1]))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
        print("saved", name + ".png", flush=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"http://127.0.0.1:{PORT}/faceshot/index.html?glb=../_work/<폴더>/generated.glb&out=<이름>")
    print(f"저장 위치: {OUT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
