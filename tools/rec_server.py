"""녹음 도우미 — 대본을 띄우고 그 자리에서 녹음해 파일로 저장한다.

참조 음성 실험 1차 녹음용이다. 안내문(`_녹음안내.txt`)만 드렸더니 무엇을 어떻게
녹음해야 하는지 알아보기 어려웠다. 화제가 10초마다 저절로 넘어가므로 말이 끊기지
않고, 파일명·형식·저장 위치를 사람이 신경 쓸 일이 없어진다.

  python tools/rec_server.py        ->  http://localhost:8600 이 저절로 열린다

**`file://` 로 열면 안 된다.** 크롬은 그것을 보안 컨텍스트로 안 봐서 마이크를
못 연다. 그래서 정적 파일 한 장인데도 서버를 둔다.

변환은 브라우저가 한다 — `OfflineAudioContext` 로 24kHz 모노까지 맞춘 wav 가
올라온다. 여기서 ffmpeg 을 부르지 않아도 되고 `ref_eval.py` 가 바로 읽는다.

실험이 끝나면 이 파일과 `rec.html` 은 지운다. 일회성 도구다.
"""
import os
import sys
import webbrowser

# 윈도우에서 자식 프로세스는 콘솔이 아니라 로케일(cp949)로 인코딩한다. 안 바꾸면
# 안내 문구의 줄표 하나에 서버가 통째로 죽는다. 문서 4절에 적힌 그 함정이다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

ROOT = os.path.expanduser("~/Desktop/테스트녹음파일/참조길이실험")
HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8600

# 받을 파일은 이것뿐이다. 이름을 그대로 경로에 쓰므로 흘러들어온 이름을 믿지 않는다.
NAMES = ["A_참조", "B_대조", "C1", "C2", "C3", "C4", "C5"]

app = FastAPI()


@app.get("/")
def page():
    return FileResponse(os.path.join(HERE, "rec.html"), media_type="text/html")


@app.get("/list")
def listing():
    import wave
    out = {}
    for n in NAMES:
        p = os.path.join(ROOT, n + ".wav")
        if not os.path.exists(p):
            continue
        try:
            with wave.open(p) as w:
                out[n] = round(w.getnframes() / w.getframerate(), 1)
        except Exception:
            out[n] = 0.0
    return out


@app.post("/save/{name}")
async def save(name: str, request: Request):
    if name not in NAMES:
        raise HTTPException(400, f"모르는 이름입니다: {name}")
    raw = await request.body()
    if len(raw) < 1000:
        raise HTTPException(400, "녹음이 비어 있습니다")
    os.makedirs(ROOT, exist_ok=True)
    dst = os.path.join(ROOT, name + ".wav")
    # 덮어쓰기 전에 직전 것을 옮겨 둔다. 잘못 눌러 좋은 녹음을 날리면 되돌릴
    # 방법이 없다 — 실제로 54.3초짜리 참조가 10.6초로 덮인 적이 있다.
    if os.path.exists(dst):
        old_dir = os.path.join(ROOT, "이전")
        os.makedirs(old_dir, exist_ok=True)
        n = 1
        while os.path.exists(os.path.join(old_dir, f"{name}_{n}.wav")):
            n += 1
        os.replace(dst, os.path.join(old_dir, f"{name}_{n}.wav"))
    with open(dst, "wb") as f:
        f.write(raw)
    return {"ok": True, "bytes": len(raw)}


@app.get("/play/{name}")
def play(name: str):
    if name not in NAMES:
        raise HTTPException(400, "모르는 이름입니다")
    p = os.path.join(ROOT, name + ".wav")
    if not os.path.exists(p):
        raise HTTPException(404, "아직 없습니다")
    return FileResponse(p, media_type="audio/wav")


@app.delete("/save/{name}")
def drop(name: str):
    """다시 녹음할 때 쓴다. 덮어쓰기로도 되지만, 지운 것이 눈에 보여야 헷갈리지 않는다."""
    if name not in NAMES:
        raise HTTPException(400, "모르는 이름입니다")
    p = os.path.join(ROOT, name + ".wav")
    if os.path.exists(p):
        os.remove(p)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    os.makedirs(ROOT, exist_ok=True)
    print(f"녹음 파일은 여기 쌓입니다 — {ROOT}")
    if "--no-open" not in sys.argv:
        webbrowser.open(f"http://localhost:{PORT}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
