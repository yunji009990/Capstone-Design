"""로컬 웹·CPU 모델 작업자. 기존 Web/Survey를 별도 주소와 저장소에서 실행한다."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import importlib
import os
from pathlib import Path
import sys
import threading
import uuid

from local_demo_config import DATA, ROOT, PACKAGED, address


def env_file():
    return DATA / "web.env"


def private_settings():
    values = {}
    if env_file().is_file():
        for line in env_file().read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def environment(config):
    # 실행하는 셸의 학교 서버 주소·시험용 GLB를 상속하지 않는다.
    return {"SESSION_URL": address(config, "registration"), "SESSION_TOKEN": config["token"],
            "SURVEY_DATA_DIR": str(DATA / "survey"), "WEB_WORKSPACE_DIR": str(DATA / "web-workspace"),
            "WEB_ENV_FILE": str(env_file()), "MODEL_WORKER_ENV": str(env_file()),
            "WEB_PREPARED_FRIEND_DIR": str(ROOT / "prepared-friend" if PACKAGED else DATA / "prepared-friend"),
            "INSIGHTFACE_ROOT": str(DATA / "models/insightface"), "MODEL_FIXTURE_GLB": ""}


def configure(config):
    os.environ.update(private_settings())
    os.environ.update(environment(config))
    if PACKAGED:
        os.environ.pop("TRIPO_API_KEY", None)
    for path in (ROOT, ROOT / "Web", ROOT / "Survey"):
        sys.path.insert(0, str(path))


def preflight():
    if PACKAGED:
        sys.path.insert(0, str(ROOT / "Web"))
        from prepared_friend import load_bundle
        load_bundle(ROOT / "prepared-friend")
        return
    settings = private_settings()
    if not settings.get("TRIPO_API_KEY"):
        raise ValueError("로컬 Tripo 설정이 없습니다. import-web을 먼저 실행하세요.")
    for key in ("HEAD_BODY_GLB", "HEAD_CUTOUT_MODEL", "BLENDER_BIN"):
        if not Path(settings.get(key, "missing")).is_file():
            raise ValueError("로컬 웹 생성 자산이 없습니다: " + key)
    if not (DATA / "models/insightface/models/buffalo_l/det_10g.onnx").is_file():
        raise ValueError("로컬 얼굴 검출 모델이 없습니다. import-web을 먼저 실행하세요.")


def web_app(config):
    configure(config)
    app = importlib.import_module("Web.app").app
    if PACKAGED:
        from fastapi.responses import JSONResponse

        @app.middleware("http")
        async def prepared_only(request, call_next):
            if request.method == "POST" and request.url.path not in (
                    "/gate", "/persona", "/test/friend/start", "/local-demo/stop", "/purge"):
                return JSONResponse({"detail": "이 배포본은 준비된 친구 전용입니다. 테스트 → 친구를 눌러 주세요."}, status_code=403)
            return await call_next(request)

    async def health():
        return {"status": "ready", "mode": "local"}

    # '/' 정적 파일 mount보다 먼저 검사해야 관리·health 경로가 가려지지 않는다.
    from starlette.routing import Route
    from starlette.responses import JSONResponse

    async def health_response(request):
        return JSONResponse(await health())

    app.router.routes.insert(0, Route("/health", health_response))
    return app


def worker_app(config):
    configure(config)
    from fastapi import FastAPI
    from core import model_queue
    from model_worker import run_worker
    stopped = threading.Event()
    owner = uuid.uuid4().hex
    thread = threading.Thread(target=run_worker, name="local-model-worker", daemon=True,
                              args=(stopped,), kwargs={"owner": owner, "settings_override": environment(config)})

    @asynccontextmanager
    async def lifespan(app):
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            await asyncio.to_thread(thread.join, 25)

    app = FastAPI(lifespan=lifespan)
    app.state.stop_worker = stopped.set

    @app.get("/health")
    def health():
        state = model_queue.status()
        ready = thread.is_alive() and state["online"] and state["configured"]
        return {"status": "ready" if ready else "unavailable", "cpu_only": True,
                "active_jobs": state["active_jobs"]}

    return app
