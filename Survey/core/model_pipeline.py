"""Restartable Tripo pipeline. All paid submissions are journaled before POST."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from . import model_queue, storage


class SubmissionUnknown(Exception):
    pass


class PipelineFailure(Exception):
    pass


class ModelPipeline:
    def __init__(self, job, owner, client, deliver, check=lambda: None, sleep=time.sleep):
        self.job, self.owner, self.client = job, owner, client
        self.deliver, self.check, self.sleep = deliver, check, sleep

    def save(self):
        self.check()
        model_queue.save(self.job, self.owner)

    def task(self, name, submit):
        self.check()
        steps = self.job["steps"]
        record = steps.get(name)
        if record is None:
            record = steps[name] = {"state": "submitting"}
            self.save()
            try:
                task_id = submit()
                if not isinstance(task_id, str) or not task_id:
                    raise ValueError("missing task ID")
            except Exception as exc:
                raise SubmissionUnknown(name) from exc
            record.update(state="submitted", task_id=task_id)
            # If this write fails, recovery sees 'submitting' and never re-POSTs.
            self.save()
        elif not record.get("task_id"):
            raise SubmissionUnknown(name)
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            self.check()
            response = self.client.get_task(record["task_id"])
            data = response.get("data") or {}
            status = str(data.get("status", "")).lower()
            if status == "success":
                record.update(state="succeeded", credits=data.get("consumed_credit"))
                self.save()
                return record["task_id"], data
            if status in {"failed", "banned", "expired", "cancelled", "canceled", "unknown"}:
                record["state"] = status
                self.save()
                raise PipelineFailure(name + ": 외부 작업 " + status)
            self.sleep(3)
        raise PipelineFailure(name + ": 대기 시간 초과 · 작업 ID를 유지했습니다")

    def run(self):
        sid = self.job["session_id"]
        path = storage.find_image(sid)
        if path is None:
            raise PipelineFailure("입력 이미지가 없습니다")
        fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
        steps = self.job["steps"]
        if "input" not in steps:
            steps["input"] = {"sha256": fingerprint,
                              "pose": os.environ.get("TRIPO_POSE", "preset:sit").strip()}
            self.save()
        elif fingerprint != steps["input"]["sha256"]:
            raise PipelineFailure("입력 이미지가 변경되었습니다. 새 생성 작업이 필요합니다")
        dest = storage.session_dir(sid) / "model.glb"
        artifact = steps.get("artifact")
        if artifact and (not dest.is_file() or hashlib.sha256(dest.read_bytes()).hexdigest() != artifact["sha256"]):
            raise PipelineFailure("저장된 모델 파일이 없거나 변경되었습니다")
        if not artifact:
            model_id, result = self.task("model", lambda: self.client.submit_image_to_3d(path))
            pose = steps["input"]["pose"]
            if pose:
                _, check = self.task("prerigcheck", lambda: self.client._submit({
                    "type": "animate_prerigcheck", "original_model_task_id": model_id}))
                out = check.get("output") or {}
                if not out.get("riggable"):
                    raise PipelineFailure("리깅할 수 없는 모델입니다. 입력 자세를 확인해 주세요")
                rig_id, _ = self.task("rig", lambda: self.client.rig_model(
                    model_id, rig_type=out.get("rig_type") or "biped"))
                _, result = self.task("animation", lambda: self.client.retarget_animation(rig_id, pose))
            output = result.get("output") or {}
            url = output.get("pbr_model") or output.get("model")
            if not isinstance(url, str) or not url:
                raise PipelineFailure("완성된 모델 다운로드 주소가 없습니다")
            partial = dest.with_suffix(".glb.part")
            self.client.download_glb(url, partial)
            self.check()
            with partial.open("rb") as stream:
                if stream.read(4) != b"glTF":
                    raise PipelineFailure("다운로드한 파일이 GLB가 아닙니다")
            if partial.stat().st_size > 100 * 1024 * 1024:
                raise PipelineFailure("모델 파일 크기 제한 초과")
            os.replace(partial, dest)
            steps["artifact"] = {"sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
                                 "bytes": dest.stat().st_size}
            self.save()
        self.check()
        # An interrupted delivery can safely upload the same immutable bytes again.
        self.deliver(sid, dest)
        steps["delivery"] = {"state": "succeeded", "sha256": steps["artifact"]["sha256"]}
        model_queue.save(self.job, self.owner, state="ready", model_path=str(dest.resolve()))
