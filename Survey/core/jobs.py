"""백그라운드 작업 디스패처 — Tripo AI 3D 모델 생성을 별도 스레드에서 실행.

설계
────
- 웹 백엔드 프로세스 안에서 threading.Thread로 띄움. 별도 큐 서버 없이 단순.
- 진행 상황은 SQLite의 model_status 컬럼으로만 추적 → UI는 DB만 읽으면 됨.
- API 키 없으면 stub 모드로 즉시 종료 (네트워크 X).
- 같은 세션을 중복 디스패치하지 않도록 in-memory 가드.
- 백엔드가 죽으면 진행 중 작업도 같이 끝남 (재시도는 /admin 에서).
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from core import database, storage, tripo as _provider

log = logging.getLogger("jobs")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

_in_flight: set[str] = set()
_lock = threading.Lock()


def dispatch_model_job(session_id: str) -> str:
    """세션을 백그라운드 Tripo AI 작업으로 보냄.

    반환값: 작업 디스패치 결과 메시지 (stub | started | already-running | no-image).
    """
    with _lock:
        if session_id in _in_flight:
            return "already-running"
        s = database.get_session(session_id)
        if not s:
            return "no-session"
        if not s.get("has_image"):
            database.set_model_status(session_id, "stub", model_error="이미지 없음 — 모델 생성 생략")
            return "no-image"

        # 키 없으면 stub로 곧장 마킹하고 끝 (스레드 띄울 필요 없음)
        client = _provider.TripoClient.from_secrets()
        if client.is_stub:
            database.set_model_status(
                session_id, "stub",
                model_error="TRIPO_API_KEY 미설정 — 환경변수에 넣고 백엔드를 다시 띄우면 처리됨",
            )
            return "stub"

        _in_flight.add(session_id)

    t = threading.Thread(target=_run_meshy_job, args=(session_id,), daemon=True, name=f"tripo-{session_id}")
    t.start()
    return "started"


def _run_meshy_job(session_id: str) -> None:
    try:
        image_path = storage.find_image(session_id)
        if image_path is None or not image_path.exists():
            database.set_model_status(session_id, "failed", model_error="이미지 파일 누락")
            return

        client = _provider.TripoClient.from_secrets()
        if client.is_stub:
            database.set_model_status(session_id, "stub", model_error="tripo_api_key 미설정")
            return

        # 1) 작업 요청
        database.set_model_status(session_id, "queued")
        log.info(f"[{session_id}] Tripo 작업 제출 중")
        task_id = client.submit_image_to_3d(image_path)
        database.set_model_status(session_id, "processing", meshy_task_id=task_id)
        log.info(f"[{session_id}] task_id={task_id}, 폴링 시작")

        # 2) 완료 대기
        result = client.wait_until_ready(task_id)

        # 3) 결과 처리
        if result.status == "ready" and result.glb_url:
            glb_url = _seated_or_original(client, session_id, task_id, result.glb_url)
            dest = storage.session_dir(session_id) / "model.glb"
            client.download_glb(glb_url, dest)
            database.set_model_status(session_id, "ready", model_glb_path=str(dest.resolve()))
            log.info(f"[{session_id}] 모델 저장: {dest}")
        else:
            database.set_model_status(session_id, "failed",
                                      model_error=result.error or "알 수 없는 오류")
            log.warning(f"[{session_id}] 실패: {result.error}")

    except Exception as exc:
        log.exception(f"[{session_id}] 예외")
        database.set_model_status(session_id, "failed", model_error=str(exc))
    finally:
        with _lock:
            _in_flight.discard(session_id)


def _seated_or_original(client, session_id: str, model_task_id: str, fallback_url: str) -> str:
    """앉은 자세 glb 의 URL. 어느 단계든 막히면 원본(선 자세) URL 을 돌려준다.

    카페에 앉아 대화하는 장면이라 앉은 자세가 필요하다. image_to_model 에는
    자세 파라미터가 없어서 리깅 후 프리셋을 입힌다(자세한 사정은 tripo.py).

    여기서 실패해도 작업 전체를 실패로 만들지 않는다. 다리가 잘린 사진은
    리깅이 안 되는데, 그런 참여자에게 아무것도 안 보여주는 것보다 서 있는
    인물이라도 띄우는 편이 낫다. 앉히기는 부가 기능이지 필수가 아니다.

    TRIPO_POSE 를 비우면 이 단계를 통째로 건너뛴다 — 크레딧을 아끼거나
    문제가 생겼을 때 코드 수정 없이 예전 동작으로 돌아가기 위한 것이다.
    """
    pose = os.environ.get("TRIPO_POSE", "preset:sit").strip()
    if not pose:
        log.info(f"[{session_id}] TRIPO_POSE 비어 있음 — 자세 적용 건너뜀")
        return fallback_url

    try:
        # 과금 0 이라 항상 먼저 묻는다. 리깅(25)을 헛되이 쓰지 않는다.
        riggable, rig_type = client.check_riggable(model_task_id)
        if not riggable:
            log.warning(f"[{session_id}] 리깅 불가 — 선 자세 모델 사용")
            return fallback_url

        rig_task = client.rig_model(model_task_id, rig_type=rig_type or "biped")
        rig_res = client.wait_until_ready(rig_task)
        if rig_res.status != "ready":
            log.warning(f"[{session_id}] 리깅 실패({rig_res.error}) — 선 자세 모델 사용")
            return fallback_url

        anim_task = client.retarget_animation(rig_task, pose)
        anim_res = client.wait_until_ready(anim_task)
        if anim_res.status != "ready" or not anim_res.glb_url:
            log.warning(f"[{session_id}] 리타겟 실패({anim_res.error}) — 선 자세 모델 사용")
            return fallback_url

        log.info(f"[{session_id}] 자세 적용 완료: {pose}")
        return anim_res.glb_url

    except Exception as exc:
        # 자세 때문에 모델 전체를 못 쓰게 만들지 않는다.
        log.warning(f"[{session_id}] 자세 적용 중 예외({exc}) — 선 자세 모델 사용")
        return fallback_url


def retry(session_id: str) -> str:
    """관리자에서 수동 재시도."""
    return dispatch_model_job(session_id)
