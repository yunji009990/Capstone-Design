"""Restartable Tripo pipeline. All paid submissions are journaled before POST."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import face_paste, head_cutout, model_queue, storage
from .tripo import image_extension, image_url

BLENDER_DIR = Path(__file__).resolve().parents[1] / "blender"


def fixture_glb():
    """MODEL_FIXTURE_GLB 가 가리키는 GLB. 없거나 비면 None 이라 평소 경로로 간다.

    Tripo 가 매 회차 다른 T포즈를 그려 결과 편차가 큰 동안, 시연과 배선 확인을
    위해 준비된 모델 하나를 그대로 내보내는 임시 장치다. 사진과 무관한 같은
    모델이 나가므로 운영 체험에 켜 두지 않는다.
    """
    raw = os.environ.get("MODEL_FIXTURE_GLB", "").strip().strip('"').strip("'")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def flag(name, default):
    """.env 의 켜고 끄기. 값이 비어 있으면 꺼진 것으로 본다."""
    return os.environ.get(name, default).strip().lower() not in {"0", "false", "no", "off", ""}


def body_glb():
    """머리를 얹을 고정 몸통. HEAD_BODY_GLB 가 가리키는 리깅된 GLB.

    사람마다 전신을 새로 만들지 않고 이 하나를 돌려 쓴다. 없으면 머리 경로를 쓸 수 없다."""
    raw = os.environ.get("HEAD_BODY_GLB", "").strip().strip('"').strip("'")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def pipeline_mode():
    """'head' 면 머리만 만들어 고정 몸통에 얹고, 'full' 이면 예전처럼 전신을 만든다.

    머리 경로는 몸통 자산과 Blender 가 있어야 돌아간다. 둘 중 하나라도 없으면 전신
    경로로 내려간다 — 조용히 내려가면 안 되므로 호출부가 사유를 기록에 남긴다.
    """
    wanted = os.environ.get("TRIPO_PIPELINE", "head").strip().lower() or "head"
    if wanted != "head":
        return "full", ""
    if body_glb() is None:
        return "full", "HEAD_BODY_GLB 가 없어 전신 경로를 씁니다"
    if shutil.which(blender_bin()) is None and not Path(blender_bin()).is_file():
        return "full", "Blender 실행 파일이 없어 전신 경로를 씁니다"
    return "head", ""


def blender_bin():
    return os.environ.get("BLENDER_BIN", "blender").strip() or "blender"


def run_blender(script, *args, timeout=900):
    """Blender 를 배치로 돌린다. 결과 파일은 호출부가 확인한다."""
    command = [blender_bin(), "--background", "--python", str(BLENDER_DIR / script), "--",
               *[str(a) for a in args]]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise PipelineFailure("Blender 를 찾지 못했습니다: " + str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise PipelineFailure(f"{script}: 시간 초과") from exc
    if done.returncode != 0:
        tail = (done.stdout or "")[-400:] + (done.stderr or "")[-400:]
        raise PipelineFailure(f"{script}: 실패 · {tail.strip()[:400]}")
    return done.stdout


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

    def tpose(self, sid, path):
        """사진을 Tripo generate_image(t_pose)로 T포즈 이미지로 바꿔 그 파일을 돌려준다.

        손이 몸에 닿은 사진은 리깅 뒤 팔을 들 때 옷이 손을 따라 늘어나므로, 어떤
        사진이 들어와도 팔을 벌린 이미지로 만든 다음 생성한다. 유료 제출은 task()
        가 먼저 기록하고, 받은 파일은 해시로 재사용해 재시작 때 다시 요청하지 않는다."""
        steps = self.job["steps"]
        asset = (steps.get("tpose") or {}).get("asset")
        if asset:
            dest = storage.session_dir(sid) / asset["file"]
            if dest.is_file() and hashlib.sha256(dest.read_bytes()).hexdigest() == asset["sha256"]:
                return dest
        _, data = self.task("tpose", lambda: self.client.submit_tpose_image(path))
        url = image_url(data.get("output") or {})
        if not isinstance(url, str) or not url:
            raise PipelineFailure("T포즈 이미지 다운로드 주소가 없습니다")
        partial = storage.session_dir(sid) / "tpose.part"
        self.client.download_file(url, partial)
        self.check()
        with partial.open("rb") as stream:
            ext = image_extension(stream.read(16))
        if ext is None:
            raise PipelineFailure("T포즈 결과가 이미지 파일이 아닙니다")
        dest = storage.session_dir(sid) / ("tpose." + ext)
        os.replace(partial, dest)
        steps["tpose"]["asset"] = {"file": dest.name, "bytes": dest.stat().st_size,
                                   "sha256": hashlib.sha256(dest.read_bytes()).hexdigest()}
        self.save()
        return dest

    def transplant(self, sid, tpose_path, photo_path):
        """T포즈 얼굴 자리에 원본 사진의 얼굴을 얹은 이미지를 돌려준다.

        Tripo 가 보는 얼굴 픽셀을 늘리려는 단계다. 유료 호출이 아니고 결과는 파일 하나라,
        실패하면 원래 T포즈로 계속 간다 — 사람 하나가 모델을 아예 못 받는 것보다 낫다.
        효과가 세 사진 중 하나에서만 확인됐다는 점은 face_paste 의 주석에 적어 두었다."""
        steps = self.job["steps"]
        record = steps.get("transplant")
        dest = storage.session_dir(sid) / "tpose_face.png"
        if record and record.get("state") == "done" and dest.is_file():
            if hashlib.sha256(dest.read_bytes()).hexdigest() == record["sha256"]:
                return dest
        if record and record.get("state") == "skipped":
            return tpose_path
        try:
            info = face_paste.transplant(tpose_path, photo_path, dest)
        except face_paste.TransplantUnavailable as exc:
            steps["transplant"] = {"state": "skipped", "reason": "라이브러리 없음: " + str(exc)}
            self.save()
            return tpose_path
        except Exception as exc:
            # 얼굴을 못 찾는 사진은 있을 수 있다. 여기서 작업을 죽이지 않는다.
            steps["transplant"] = {"state": "skipped", "reason": str(exc)[:200]}
            self.save()
            return tpose_path
        steps["transplant"] = {"state": "done",
                               "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(), **info}
        self.save()
        return dest

    def cutout(self, sid, photo):
        """사진에서 머리카락·얼굴·목만 오려 낸 PNG 를 돌려준다.

        어깨와 상의가 남으면 흉상이 만들어져 고정 몸통과 겹친다. 머리 경로에서는 이
        단계가 선택이 아니라 전제라, 실패하면 작업을 세운다 — 얼굴 이식과 달리 건너뛰고
        계속할 방법이 없다."""
        steps = self.job["steps"]
        dest = storage.session_dir(sid) / "head_input.png"
        record = steps.get("cutout")
        if record and record.get("state") == "done" and dest.is_file():
            if hashlib.sha256(dest.read_bytes()).hexdigest() == record["sha256"]:
                return dest
        try:
            info = head_cutout.cutout(photo, dest, fit=os.environ.get(
                "HEAD_CUTOUT_FIT", "portrait").strip() or "portrait")
        except head_cutout.CutoutUnavailable as exc:
            raise PipelineFailure("머리 오려내기 준비물이 없습니다: " + str(exc)[:200]) from exc
        except head_cutout.CutoutFailed as exc:
            raise PipelineFailure("사진에서 얼굴을 찾지 못했습니다: " + str(exc)[:200]) from exc
        steps["cutout"] = {"state": "done",
                           "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(), **info}
        self.save()
        return dest

    def trim(self, sid, head_path):
        """생성된 머리에서 Tripo 가 지어낸 옷을 잘라 내고 목 단면을 기록한다.

        사진에서 옷을 지워도 생성 모델이 다시 만들어 낸다 — 닫힌 형상을 내놓아야 하므로
        목 아래를 무언가로 끝내기 때문이다. 만든 뒤 자르는 편이 확실하다."""
        steps = self.job["steps"]
        dest = storage.session_dir(sid) / "head_trim.glb"
        record = steps.get("trim")
        if record and record.get("state") == "done" and dest.is_file():
            if hashlib.sha256(dest.read_bytes()).hexdigest() == record["sha256"]:
                return dest
        run_blender("head_trim.py", head_path, dest)
        if not dest.is_file() or dest.read_bytes()[:4] != b"glTF":
            raise PipelineFailure("머리 다듬기 결과가 GLB 가 아닙니다")
        steps["trim"] = {"state": "done", "bytes": dest.stat().st_size,
                         "sha256": hashlib.sha256(dest.read_bytes()).hexdigest()}
        self.save()
        return dest

    def merge(self, sid, head_path, dest):
        """다듬은 머리를 고정 몸통에 얹어 최종 GLB 를 만든다."""
        body = body_glb()
        if body is None:
            raise PipelineFailure("HEAD_BODY_GLB 가 설정되지 않았습니다")
        partial = dest.with_suffix(".glb.part")
        run_blender("head_body_merge.py", body, head_path, partial)
        if not partial.is_file() or partial.read_bytes()[:4] != b"glTF":
            raise PipelineFailure("합치기 결과가 GLB 가 아닙니다")
        if partial.stat().st_size > 100 * 1024 * 1024:
            raise PipelineFailure("모델 파일 크기 제한 초과")
        os.replace(partial, dest)
        return dest

    def run(self):
        sid = self.job["session_id"]
        path = storage.find_image(sid)
        if path is None:
            raise PipelineFailure("입력 이미지가 없습니다")
        fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
        steps = self.job["steps"]
        if "input" not in steps:
            mode, note = pipeline_mode()
            steps["input"] = {"sha256": fingerprint,
                              "pipeline": mode,
                              "pose": os.environ.get("TRIPO_POSE", "preset:sit").strip(),
                              "tpose": flag("TRIPO_TPOSE", "1"),
                              "transplant": flag("TRIPO_FACE_TRANSPLANT", "1")}
            # 머리 경로를 원했는데 못 쓴 이유는 반드시 남긴다. 조용히 옛 경로로
            # 내려가면 왜 얼굴이 그대로인지 나중에 알 방법이 없다(고정 GLB 때 겪었다).
            if note:
                steps["input"]["pipeline_note"] = note
            self.save()
        elif fingerprint != steps["input"]["sha256"]:
            raise PipelineFailure("입력 이미지가 변경되었습니다. 새 생성 작업이 필요합니다")
        dest = storage.session_dir(sid) / "model.glb"
        artifact = steps.get("artifact")
        if artifact and (not dest.is_file() or hashlib.sha256(dest.read_bytes()).hexdigest() != artifact["sha256"]):
            raise PipelineFailure("저장된 모델 파일이 없거나 변경되었습니다")
        if not artifact and fixture_glb() is not None:
            # 임시 우회: Tripo 를 부르지 않고 미리 준비한 GLB 를 그대로 전달한다.
            # MODEL_FIXTURE_GLB 를 비우면 평소 경로로 돌아간다. 사진과 무관한
            # 같은 모델이 나가므로 시연·배선 확인용으로만 쓴다.
            src = fixture_glb()
            blob = src.read_bytes()
            if blob[:4] != b"glTF":
                raise PipelineFailure("MODEL_FIXTURE_GLB 가 GLB 파일이 아닙니다")
            partial = dest.with_suffix(".glb.part")
            partial.write_bytes(blob)
            os.replace(partial, dest)
            digest = hashlib.sha256(blob).hexdigest()
            steps["fixture"] = {"file": src.name, "bytes": len(blob), "sha256": digest,
                                "note": "Tripo 호출 없음 · 준비된 모델 사용"}
            steps["artifact"] = {"sha256": digest, "bytes": len(blob)}
            self.save()
            artifact = steps["artifact"]
        if not artifact:
            mode = steps["input"].get("pipeline", "full")
            if mode == "head":
                # 머리만 만들어 미리 리깅된 몸통에 얹는다. T포즈를 거치지 않으므로
                # 원본 사진이 image_to_model 로 직행하고, Tripo 가 보는 얼굴이
                # 70x100px 에서 420x550px 로 올라간다.
                source = self.cutout(sid, path)
            else:
                source = self.tpose(sid, path) if steps["input"].get("tpose") else path
                if steps["input"].get("transplant"):
                    source = self.transplant(sid, source, path)
            # 생성 설정을 .env 로 돌릴 수 있게 둔다. 기본값은 지금 운영값 그대로다.
            # face_limit=auto 로 두면 Tripo 가 적응적으로 정하는데, 실측에서 195만
            # 삼각형·79MB 가 나왔다. Quest 에 그대로 못 올리므로 감축 도구를 서버에
            # 넣기 전에는 켜지 않는다.
            limit = os.environ.get("TRIPO_FACE_LIMIT", "50000").strip().lower()
            extra = {} if limit == "auto" else {"face_limit": int(limit)}
            extra["geometry_quality"] = os.environ.get(
                "TRIPO_GEOMETRY_QUALITY", "standard").strip()
            model_id, result = self.task(
                "model", lambda: self.client.submit_image_to_3d(source, **extra))
            if mode != "head":
                # 전신 경로에서만 Tripo 리깅을 부른다. 머리 경로는 몸통이 이미
                # 리깅되어 있어 prerigcheck/rig/retarget 이 통째로 빠진다 —
                # 운영 실패 8건 중 6건이 여기서 났고, 크레딧도 95에서 60으로 준다.
                pose = steps["input"]["pose"]
                if pose:
                    _, check = self.task("prerigcheck", lambda: self.client._submit({
                        "type": "animate_prerigcheck", "original_model_task_id": model_id}))
                    out = check.get("output") or {}
                    if not out.get("riggable"):
                        raise PipelineFailure("리깅할 수 없는 모델입니다. 입력 자세를 확인해 주세요")
                    rig_id, _ = self.task("rig", lambda: self.client.rig_model(
                        model_id, rig_type=out.get("rig_type") or "biped"))
                    _, result = self.task(
                        "animation", lambda: self.client.retarget_animation(rig_id, pose))
            output = result.get("output") or {}
            url = output.get("pbr_model") or output.get("model")
            if not isinstance(url, str) or not url:
                raise PipelineFailure("완성된 모델 다운로드 주소가 없습니다")
            if mode == "head":
                # 받은 것은 머리뿐이다. 옷을 잘라 내고 고정 몸통에 얹어야 model.glb 가 된다.
                raw = storage.session_dir(sid) / "head_raw.glb"
                if not raw.is_file():
                    partial = raw.with_suffix(".glb.part")
                    self.client.download_glb(url, partial)
                    self.check()
                    with partial.open("rb") as stream:
                        if stream.read(4) != b"glTF":
                            raise PipelineFailure("다운로드한 파일이 GLB가 아닙니다")
                    os.replace(partial, raw)
                self.merge(sid, self.trim(sid, raw), dest)
            else:
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
