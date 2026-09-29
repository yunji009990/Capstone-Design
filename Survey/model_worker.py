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
               "MODEL_FIXTURE_GLB",
               "TRIPO_FACE_TRANSPLANT", "TRIPO_FACE_LIMIT",
               "TRIPO_GEOMETRY_QUALITY",
               # 머리 경로(TRIPO_PIPELINE=head)용. 몸통 자산과 Blender 가 있어야 돌아가고,
               # 없으면 전신 경로로 내려가며 그 사유가 작업 기록에 남는다.
               "TRIPO_PIPELINE", "HEAD_BODY_GLB", "BLENDER_BIN",
               "HEAD_CUTOUT_MODEL", "HEAD_CUTOUT_FIT",
               "HEAD_BODY_BONE", "HEAD_BODY_FROM", "HEAD_BODY_SHARE"}
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
            # DB 의 error 는 유족 화면에 나가므로 일반 문구로 두고, 실제 사유는 로그에만
            # 남긴다. 예전에는 예외 종류만 적어서, PipelineFailure 가 아닌 것으로 끝난
            # 작업(예: RuntimeError)의 원인을 나중에 알 방법이 없었다.
            #
            # SubmissionUnknown 은 `raise ... from exc` 로 만들어지므로 정작 무엇이
            # 끊겼는지는 __cause__ 에 있다. 겉 예외만 적으면 단계 이름밖에 안 남는다.
            cause = exc.__cause__ if isinstance(exc, SubmissionUnknown) and exc.__cause__ else exc
            detail = str(cause) or type(cause).__name__
            if len(detail) > 500:
                # Tripo 는 실패 응답 본문을 통째로 실어 보낼 때가 있다. 로그가 한 건에
                # 잠기지 않게 자른다. 요청 헤더는 담기지 않으므로 API 키는 새지 않는다.
                detail = detail[:500] + "…(잘림)"
            # 예상한 실패는 한 줄로 충분하고, 예상 못 한 예외는 어디서 터졌는지가 필요하다.
            unexpected = not isinstance(exc, (PipelineFailure, SubmissionUnknown))
            log.warning("job=%s state=%s error_type=%s detail=%s",
                        sid, state, type(exc).__name__, detail, exc_info=unexpected)
        finally:
            finished.set()
            thread.join(timeout=2)
            model_queue.heartbeat(owner, configured=not client.is_stub)
        if args.once:
            break


if __name__ == "__main__":
    main()
