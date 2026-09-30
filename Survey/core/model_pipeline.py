"""Restartable Tripo pipeline. All paid submissions are journaled before POST."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import head_cutout, model_queue, storage
from .tripo import TransientError, image_extension, image_url

# 재시도 간격. 상대가 죽어 있는 동안 3초마다 두드리면 복구를 돕기는커녕 방해가
# 되므로 점점 뜸하게 묻되, 30초를 넘기지는 않는다 — 대기 한도가 30분이라 너무
# 뜸하면 상대가 돌아온 뒤에도 한참을 못 알아챈다.
RETRY_STEPS = (5, 10, 20, 30)


def backoff(attempts: int) -> int:
    """연속 실패 횟수에 따라 다음에 다시 물어보기까지 쉴 시간(초)."""
    return RETRY_STEPS[min(attempts, len(RETRY_STEPS)) - 1]

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


def body_glb():
    """머리를 얹을 고정 몸통. HEAD_BODY_GLB 가 가리키는 리깅된 GLB.

    사람마다 전신을 새로 만들지 않고 이 하나를 돌려 쓴다. 없으면 머리 경로를 쓸 수 없다."""
    raw = os.environ.get("HEAD_BODY_GLB", "").strip().strip('"').strip("'")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def require_head_assets():
    """머리 경로에 필요한 것이 다 있는지 확인한다. 없으면 작업을 세운다.

    예전에는 없으면 전신 경로로 내려갔는데, 전신 경로 자체를 걷어냈으므로 이제는
    조용히 대신할 것이 없다. 설정이 빠진 채로 도는 것보다 여기서 멈추고 운영자가
    보는 편이 낫다 — 고정 GLB 우회 때 사진과 무관한 모델이 나가는 줄도 모르고
    한참을 보낸 적이 있다.
    """
    if body_glb() is None:
        raise PipelineFailure("몸통 자산이 설정되지 않았습니다. 운영자에게 알려 주세요")
    binary = blender_bin()
    if shutil.which(binary) is None and not Path(binary).is_file():
        raise PipelineFailure("모델 합치기 도구가 설치되지 않았습니다. 운영자에게 알려 주세요")


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

    def fetch(self, call, tries=5):
        """일시적 통신 오류면 쉬었다 다시 부른다. 그 밖의 오류는 그대로 올린다.

        값을 치르지 않는 호출(내려받기)에만 쓴다. 제출에 쓰면 같은 작업을 두 번
        만들 수 있다."""
        for attempt in range(1, tries + 1):
            self.check()
            try:
                return call()
            except TransientError:
                if attempt == tries:
                    raise
                self.sleep(backoff(attempt))

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
        trouble, attempts = None, 0
        while time.monotonic() < deadline:
            self.check()
            try:
                response = self.client.get_task(record["task_id"])
            except TransientError as exc:
                # 상대 게이트웨이가 잠깐 흔들린 것이다. 30분을 기다리기로 해 놓고
                # 502 한 번에 작업을 접으면, 이미 값을 치른 Tripo 작업을 버리게 된다.
                # 실측(2026-09-30)에서 그렇게 죽은 두 작업 모두 Tripo 쪽에서는
                # success 로 끝나 있었다.
                trouble, attempts = exc, attempts + 1
                self.sleep(backoff(attempts))
                continue
            trouble, attempts = None, 0
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
        if trouble is not None:
            # 마지막까지 말을 못 붙인 채 시간이 다 갔다. 작업 ID 는 그대로 두므로
            # 상대가 돌아오면 같은 작업을 이어받는다 — 크레딧이 더 들지 않는다.
            raise PipelineFailure(
                f"{name}: 생성 서버와 통신하지 못했습니다 · 작업 ID를 유지했습니다 · {trouble}")
        raise PipelineFailure(name + ": 대기 시간 초과 · 작업 ID를 유지했습니다")

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
        # 임시 파일 이름도 .glb 로 끝나야 한다. Blender 의 glTF 내보내기는 경로가
        # .glb 로 끝나지 않으면 확장자를 붙여 버려서, model.glb.part 로 주면
        # model.glb.part.glb 를 만들어 놓고 우리는 없는 파일을 찾게 된다(실제로 겪었다).
        partial = dest.with_name(dest.stem + ".part.glb")
        # 본 이름과 머리 비율은 몸통 자산마다 다르다. Tripo 리깅은 Head/NeckTwist01,
        # 사람이 만든 리그는 head.x/neck.x 처럼 규격이 제각각이라 설정으로 뺀다.
        options = ["--bone", os.environ.get("HEAD_BODY_BONE", "Head").strip() or "Head",
                   "--from", os.environ.get("HEAD_BODY_FROM", "NeckTwist01").strip()
                   or "NeckTwist01"]
        share = os.environ.get("HEAD_BODY_SHARE", "").strip()
        if share:
            # 머리 없이 만든 몸통은 옷깃 구멍이 목보다 좁아 굵기로 맞추면 머리가 작아진다.
            # 그럴 때는 머리 높이를 몸통 키 대비 비율로 지정한다(사람은 0.10~0.13).
            options += ["--head-share", share]
        yaw = os.environ.get("HEAD_BODY_YAW", "").strip()
        if yaw:
            # 머리와 몸통이 보는 방향이 다르면 옆을 보고 붙는다. 자산 쌍마다 다르다.
            options += ["--yaw", yaw]
        out_scale = os.environ.get("HEAD_BODY_OUT_SCALE", "").strip()
        if out_scale:
            # 씬에서 쓰는 크기 그대로 내보낸다. 유니티에서 매번 손으로 줄이지 않도록.
            options += ["--out-scale", out_scale]
        run_blender("head_body_merge.py", body, head_path, partial, *options)
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
            require_head_assets()
            steps["input"] = {"sha256": fingerprint, "pipeline": "head"}
            self.save()
        elif fingerprint != steps["input"]["sha256"]:
            raise PipelineFailure("입력 이미지가 변경되었습니다. 새 생성 작업이 필요합니다")
        elif steps["input"].get("pipeline") != "head":
            # 예전 방식(T포즈 → 전신 생성 → 리깅)으로 접수된 작업이다. 그 경로를
            # 걷어냈으므로 이어서 돌 수 없다. 저장된 task_id 를 그대로 쓰면 T포즈로
            # 만든 전신 모델을 머리인 줄 알고 자르게 된다.
            raise PipelineFailure("예전 방식으로 접수된 작업입니다. 다시 접수해 주세요")
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
            # 머리만 만들어 미리 리깅된 몸통에 얹는다. T포즈를 거치지 않으므로 원본
            # 사진이 image_to_model 로 직행하고, Tripo 가 보는 얼굴이 70x100px 에서
            # 420x550px 로 올라간다.
            source = self.cutout(sid, path)
            # 생성 설정을 .env 로 돌릴 수 있게 둔다. 기본값은 지금 운영값 그대로다.
            # face_limit=auto 로 두면 Tripo 가 적응적으로 정하는데, 실측에서 195만
            # 삼각형·79MB 가 나왔다. Quest 에 그대로 못 올리므로 감축 도구를 서버에
            # 넣기 전에는 켜지 않는다.
            limit = os.environ.get("TRIPO_FACE_LIMIT", "50000").strip().lower()
            extra = {} if limit == "auto" else {"face_limit": int(limit)}
            extra["geometry_quality"] = os.environ.get(
                "TRIPO_GEOMETRY_QUALITY", "standard").strip()
            _, result = self.task(
                "model", lambda: self.client.submit_image_to_3d(source, **extra))
            output = result.get("output") or {}
            url = output.get("pbr_model") or output.get("model")
            if not isinstance(url, str) or not url:
                raise PipelineFailure("완성된 모델 다운로드 주소가 없습니다")
            # 받은 것은 머리뿐이다. 옷을 잘라 내고 고정 몸통에 얹어야 model.glb 가 된다.
            raw = storage.session_dir(sid) / "head_raw.glb"
            if not raw.is_file():
                partial = raw.with_suffix(".glb.part")
                # 다 만들어 놓고 받는 길만 막혀 작업을 버리는 일이 없도록, 폴링과
                # 같은 규율로 다시 받는다. CDN 은 API 와 다른 호스트라 따로 흔들린다.
                self.fetch(lambda: self.client.download_glb(url, partial))
                self.check()
                with partial.open("rb") as stream:
                    if stream.read(4) != b"glTF":
                        raise PipelineFailure("다운로드한 파일이 GLB가 아닙니다")
                os.replace(partial, raw)
            self.merge(sid, self.trim(sid, raw), dest)
            steps["artifact"] = {"sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
                                 "bytes": dest.stat().st_size}
            self.save()
        self.check()
        # An interrupted delivery can safely upload the same immutable bytes again.
        self.deliver(sid, dest)
        steps["delivery"] = {"state": "succeeded", "sha256": steps["artifact"]["sha256"]}
        model_queue.save(self.job, self.owner, state="ready", model_path=str(dest.resolve()))
