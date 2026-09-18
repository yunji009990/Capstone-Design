"""Separate CPU worker: python Survey/model_worker.py [--once|--status]."""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import signal
import threading
import uuid


def load_settings():
    """Read only worker settings; never import the web app or AI dependencies."""
    path = Path(os.environ.get("MODEL_WORKER_ENV") or
                Path(__file__).resolve().parents[1] / "Web" / ".env")
    allowed = {"TRIPO_API_KEY", "TRIPO_POSE", "TRIPO_TPOSE", "SESSION_URL", "SESSION_TOKEN", "SURVEY_DATA_DIR",
               "MODEL_FIXTURE_GLB"}
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key in allowed:
                values[key] = value.strip().strip('"').strip("'")
    for key, value in values.items():
        os.environ[key] = value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    load_settings()
    from core import model_queue, tripo
    from core.model_pipeline import ModelPipeline, PipelineFailure, SubmissionUnknown
    if args.status:
        print(json.dumps(model_queue.status()))
        return
    import httpx
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    log = logging.getLogger("model-worker")
    owner = uuid.uuid4().hex
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())

    def deliver(sid, path):
        base = os.environ.get("SESSION_URL", "http://127.0.0.1:8000").rstrip("/")
        token = os.environ.get("SESSION_TOKEN", "")
        if not token:
            raise PipelineFailure("등록 서버 접속 토큰이 설정되지 않았습니다")
        with path.open("rb") as source, httpx.Client(timeout=120, trust_env=False) as client:
            response = client.post(base + "/session/" + sid + "/model",
                headers={"X-Token": token}, files={"model": ("model.glb", source, "model/gltf-binary")})
        if response.status_code != 200:
            raise PipelineFailure("모델 전달 실패 · HTTP " + str(response.status_code))

    while not stopped.is_set():
        load_settings()
        client = tripo.TripoClient.from_secrets()
        model_queue.heartbeat(owner, configured=not client.is_stub)
        job = model_queue.claim(owner)
        if job is None:
            if args.once:
                break
            stopped.wait(3)
            continue
        sid = job["session_id"]
        if client.is_stub:
            model_queue.save(job, owner, state="blocked", error="Tripo API 키 설정 후 재시도해 주세요")
            if args.once:
                break
            continue
        finished, lease_lost = threading.Event(), threading.Event()

        def pulse():
            while not finished.wait(10):
                try:
                    model_queue.heartbeat(owner, sid, configured=True)
                except Exception:
                    lease_lost.set()
                    return

        def check():
            if stopped.is_set() or lease_lost.is_set():
                raise InterruptedError("worker interrupted")
            model_queue.heartbeat(owner, sid, configured=True)

        thread = threading.Thread(target=pulse, daemon=True)
        thread.start()
        try:
            ModelPipeline(job, owner, client, deliver, check,
                          sleep=lambda seconds: stopped.wait(seconds)).run()
            log.info("job=%s state=ready", sid)
        except InterruptedError:
            log.info("job=%s paused; saved task IDs will be resumed", sid)
        except Exception as exc:
            state = "submission_unknown" if isinstance(exc, SubmissionUnknown) else "failed"
            error = ("제출 결과 확인 필요 · 자동 재제출하지 않습니다" if state == "submission_unknown"
                     else str(exc) if isinstance(exc, PipelineFailure)
                     else "작업 통신·저장 실패 · 기존 작업 ID를 유지했습니다")
            try:
                model_queue.save(job, owner, state=state, error=error)
            except RuntimeError:
                pass  # Deleted person or a newer worker now owns this job.
            log.warning("job=%s state=%s error_type=%s", sid, state, type(exc).__name__)
        finally:
            finished.set()
            thread.join(timeout=2)
            model_queue.heartbeat(owner, configured=not client.is_stub)
        if args.once:
            break


if __name__ == "__main__":
    main()
