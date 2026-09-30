"""Independent worker tests. Tripo, Blender and the cutout are simulated.

파이프라인은 이제 한 가지 경로뿐이다 — 사진에서 머리만 오려 image_to_model 로
머리를 만들고, 지어낸 옷을 잘라 낸 뒤 미리 리깅된 고정 몸통에 얹는다. 예전의
전신 경로(T포즈 생성 → 3D 생성 → 리깅 검사 → 리깅 → 앉기)는 걷어냈으므로
FakeTripo 에도 그 메서드를 두지 않는다. 실수로 부르면 AttributeError 로 드러난다.
"""
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import database, model_queue, storage
from core.head_cutout import CutoutFailed, CutoutUnavailable
from core.model_pipeline import ModelPipeline, PipelineFailure, SubmissionUnknown
from core.tripo import TransientError, _classify

HEAD_PNG = b"\x89PNG\r\n\x1a\nfake-head-cutout"


class FakeTripo:
    def __init__(self):
        self.submissions = []
        self.unknown = False
        # 앞에서부터 하나씩 꺼내 get_task·download_glb 대신 던진다. 비면 정상 동작.
        self.get_faults = []
        self.download_faults = []
        self.polls = 0

    def _submit(self, body):
        self.submissions.append(body["type"])
        if self.unknown:
            raise TimeoutError("lost submission response")
        return body["type"]

    def submit_image_to_3d(self, path, **options):
        # 실제 클라이언트는 face_limit·geometry_quality 를 받는다. 파이프라인이
        # .env 로 그 값을 넘기므로 여기서도 받아 두고 무엇이 왔는지 기록한다.
        self.model_input = (path.name, path.read_bytes())
        self.model_options = options
        return self._submit({"type": "model"})

    def get_task(self, task):
        self.polls += 1
        if self.get_faults:
            raise self.get_faults.pop(0)
        return {"data": {"status": "success", "consumed_credit": 0,
                         "output": {"model": "https://fake.invalid/model"}}}

    def download_glb(self, url, dest):
        if self.download_faults:
            raise self.download_faults.pop(0)
        dest.write_bytes(b"glTF-head-from-tripo")


def fake_blender(calls):
    """Blender 를 부르는 대신 출력 파일만 만들어 둔다.

    두 스크립트 모두 「입력들… 출력 [옵션들]」 순서다. 옵션 앞의 마지막 위치 인자가
    출력이다 — 첫 .glb 를 잡으면 입력에 덮어쓰게 된다.

    진짜 Blender 의 glTF 내보내기는 경로가 .glb 로 끝나지 않으면 확장자를 붙인다.
    그대로 흉내 내지 않으면 임시 파일 이름을 잘못 지어도 검사가 통과해 버린다 —
    실제로 model.glb.part 로 주는 바람에 운영에서 합치기가 통째로 실패했다."""
    def run(script, *args, **kwargs):
        calls.append(script)
        positional = []
        for value in args:
            if str(value).startswith("--"):
                break
            positional.append(value)
        target = Path(positional[-1])
        if target.suffix.lower() != ".glb":
            target = target.with_name(target.name + ".glb")
        target.write_bytes(b"glTF-merged")
        return ""
    return run


def fake_cutout(photo, dest, **kwargs):
    dest.write_bytes(HEAD_PNG)
    return {"file": dest.name}


class ModelWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.body = root / "body.glb"
        self.body.write_bytes(b"glTF-body")
        self.blender_calls = []
        # BLENDER_BIN 은 존재하는 파일이기만 하면 된다. 실제로 부르지는 않고
        # run_blender 를 바꿔 끼운다.
        self.patches = [
            patch.object(database, "DB_PATH", root / "sessions.db"),
            patch.object(storage, "ASSETS_ROOT", root / "assets"),
            patch.dict(os.environ, {"HEAD_BODY_GLB": str(self.body),
                                    "BLENDER_BIN": sys.executable}),
            patch("core.model_pipeline.run_blender", fake_blender(self.blender_calls)),
            patch("core.head_cutout.cutout", fake_cutout),
        ]
        for item in self.patches:
            item.start()
        database.insert_session("person-a", {}, True, True, True, 1, True, True)
        (storage.session_dir("person-a") / "front.png").write_bytes(b"test-image")
        self.client = FakeTripo()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def claim(self, owner="worker-1"):
        model_queue.enqueue("person-a")
        return model_queue.claim(owner)

    def another(self, name="person-b"):
        database.insert_session(name, {}, True, True, True, 1, True, True)
        (storage.session_dir(name) / "front.png").write_bytes(b"test-image")
        model_queue.enqueue(name)

    # ── 기본 흐름 ──────────────────────────────────────────────
    def test_duplicate_requests_have_one_owner_and_ready_requires_delivery(self):
        job = self.claim()
        self.assertEqual(model_queue.enqueue("person-a"), "running")
        self.assertIsNone(model_queue.claim("worker-2"))
        delivered = []
        ModelPipeline(job, "worker-1", self.client,
                      lambda sid, path: delivered.append(path.read_bytes())).run()
        self.assertEqual(delivered, [b"glTF-merged"])
        self.assertEqual(database.get_session("person-a")["model_status"], "ready")
        self.assertEqual(model_queue.enqueue("person-a"), "ready")

    def test_tripo_is_called_once_and_gets_the_cutout_not_the_photo(self):
        """Tripo 호출은 image_to_model 한 번뿐이다. T포즈·리깅이 빠지면서 다섯 번이
        한 번이 됐고(크레딧 95 → 60), 리깅 거부로 실패하던 경로가 사라졌다.

        생성 입력은 원본 사진이 아니라 오려낸 머리여야 한다. 어깨가 남으면 흉상이
        만들어져 고정 몸통과 겹친다."""
        job = self.claim()
        ModelPipeline(job, "worker-1", self.client, lambda *_: None).run()
        self.assertEqual(self.client.submissions, ["model"])
        self.assertEqual(self.client.model_input, ("head_input.png", HEAD_PNG))
        self.assertEqual(self.blender_calls, ["head_trim.py", "head_body_merge.py"])
        self.assertEqual(job["steps"]["input"]["pipeline"], "head")

    def test_delivery_failure_resumes_without_any_new_tripo_request(self):
        job = self.claim()

        def failed_delivery(*args):
            raise PipelineFailure("registration offline")
        with self.assertRaises(PipelineFailure):
            ModelPipeline(job, "worker-1", self.client, failed_delivery).run()
        model_queue.save(job, "worker-1", state="failed", error="delivery failed")
        self.assertNotEqual(database.get_session("person-a")["model_status"], "ready")
        restored = self.claim("worker-2")
        ModelPipeline(restored, "worker-2", self.client, lambda *_: None).run()
        self.assertEqual(self.client.submissions, ["model"])
        self.assertEqual(model_queue.get_job("person-a")["state"], "ready")

    def test_finished_model_is_not_rebuilt_when_only_delivery_failed(self):
        """전달만 실패했으면 모델은 이미 완성돼 있다. 다시 만들지 않고 같은 바이트를
        올리기만 한다 — Blender 두 단계가 건당 수십 초라 그냥 두면 재시작이 느려진다."""
        job = self.claim()

        def offline(*args):
            raise PipelineFailure("registration offline")
        with self.assertRaises(PipelineFailure):
            ModelPipeline(job, "worker-1", self.client, offline).run()
        model_queue.save(job, "worker-1", state="failed", error="delivery failed")
        self.assertEqual((storage.session_dir("person-a") / "head_input.png").read_bytes(),
                         HEAD_PNG)
        self.assertEqual(job["steps"]["cutout"]["file"], "head_input.png")

        restored = self.claim("worker-2")
        ModelPipeline(restored, "worker-2", self.client, lambda *_: None).run()
        # 첫 회차에서 두 번 부르고 끝이다. 두 번째 회차는 저장된 model.glb 를 그대로 올린다.
        self.assertEqual(self.blender_calls, ["head_trim.py", "head_body_merge.py"])
        self.assertEqual(self.client.submissions, ["model"])

    def test_generation_options_come_from_the_environment(self):
        """face_limit=auto 는 필드를 빼서 Tripo 가 정하게 한다. 실측 195만면·79MB 라
        감축 도구를 서버에 넣기 전에는 켜면 안 되고, 그래서 기본값은 숫자다."""
        job = self.claim()
        ModelPipeline(job, "worker-1", self.client, lambda *_: None).run()
        self.assertEqual(self.client.model_options,
                         {"face_limit": 50000, "geometry_quality": "standard"})

        self.another()
        tuned = FakeTripo()
        with patch.dict(os.environ, {"TRIPO_FACE_LIMIT": "auto",
                                     "TRIPO_GEOMETRY_QUALITY": "detailed"}):
            ModelPipeline(model_queue.claim("worker-2"), "worker-2", tuned,
                          lambda *_: None).run()
        self.assertEqual(tuned.model_options, {"geometry_quality": "detailed"})

    def test_submission_response_loss_is_never_automatically_resubmitted(self):
        job = self.claim()
        self.client.unknown = True
        with self.assertRaises(SubmissionUnknown):
            ModelPipeline(job, "worker-1", self.client, lambda *_: None).run()
        with model_queue.connect() as conn:
            conn.execute("UPDATE model_jobs SET lease_until=0")
        restored = model_queue.claim("worker-2")
        self.client.unknown = False
        with self.assertRaises(SubmissionUnknown):
            ModelPipeline(restored, "worker-2", self.client, lambda *_: None).run()
        self.assertEqual(len(self.client.submissions), 1)

    # ── 준비물이 없을 때 ────────────────────────────────────────
    def test_missing_body_asset_stops_the_job_before_spending_credits(self):
        """대신할 경로가 없다. 예전에는 전신 경로로 내려갔지만 그것을 걷어냈으므로
        여기서 멈춘다 — 설정이 빠진 채로 도는 것보다 낫다."""
        job = self.claim()
        with patch.dict(os.environ, {"HEAD_BODY_GLB": ""}):
            with self.assertRaises(PipelineFailure):
                ModelPipeline(job, "worker-1", self.client, lambda *_: None).run()
        self.assertEqual(self.client.submissions, [])

    def test_missing_blender_stops_the_job_before_spending_credits(self):
        job = self.claim()
        with patch.dict(os.environ, {"BLENDER_BIN": "/nonexistent/blender"}):
            with self.assertRaises(PipelineFailure):
                ModelPipeline(job, "worker-1", self.client, lambda *_: None).run()
        self.assertEqual(self.client.submissions, [])

    def test_cutout_failure_stops_the_job(self):
        """오려내기는 이 경로의 전제다. 건너뛰고 계속할 방법이 없으므로 작업을 세운다 —
        어깨가 남으면 몸통과 겹친 흉상이 나간다."""
        for error in (CutoutFailed("얼굴 없음"), CutoutUnavailable("mediapipe 없음")):
            job = self.claim()
            with patch("core.head_cutout.cutout", side_effect=error):
                with self.assertRaises(PipelineFailure):
                    ModelPipeline(job, "worker-1", self.client, lambda *_: None).run()
            model_queue.save(job, "worker-1", state="failed", error="cutout failed")
        self.assertEqual(self.client.submissions, [])

    def test_job_accepted_by_the_old_pipeline_is_refused(self):
        """예전 방식(T포즈 → 전신 생성 → 리깅)으로 접수된 작업은 이어서 돌 수 없다.
        저장된 task_id 를 그대로 쓰면 전신 모델을 머리인 줄 알고 자르게 된다."""
        job = self.claim()
        job["steps"]["input"] = {"sha256": hashlib.sha256(b"test-image").hexdigest(),
                                 "pipeline": "full", "tpose": True}
        with self.assertRaises(PipelineFailure):
            ModelPipeline(job, "worker-1", self.client, lambda *_: None).run()
        self.assertEqual(self.client.submissions, [])

    # ── 일시적 통신 오류 ───────────────────────────────────────
    #
    # 2026-09-30 운영에서 같은 사진이 세 번 성공하고 두 번 죽었다. 죽은 쪽의
    # 오류는 Tripo 게이트웨이의 502 였고, 그 두 작업은 Tripo 쪽에서 success 로
    # 끝나 있었다(각 40 크레딧). 30분을 기다리기로 해 놓고 한 번 흔들렸다고
    # 값을 치른 작업을 버린 것이라, 그 구조를 여기서 붙잡아 둔다.

    def test_gateway_hiccup_while_polling_does_not_lose_a_paid_task(self):
        naps = []
        self.client.get_faults = [TransientError("Tripo GET 실패 [502]"),
                                  TransientError("Tripo GET 실패 [503]")]
        job = self.claim()
        delivered = []
        ModelPipeline(job, "worker-1", self.client,
                      lambda sid, path: delivered.append(path.read_bytes()),
                      sleep=naps.append).run()
        self.assertEqual(delivered, [b"glTF-merged"])
        # 제출은 한 번뿐이어야 한다. 다시 냈으면 크레딧을 또 쓴 것이다.
        self.assertEqual(self.client.submissions, ["model"])
        self.assertEqual(self.client.polls, 3)
        # 3초 간격으로 들이받지 않고 점점 뜸하게 물어본다.
        self.assertEqual(naps[:2], [5, 10])

    def test_a_real_api_error_while_polling_fails_at_once(self):
        """401·400 은 몇 번을 물어도 같은 답이 온다. 재시도하면 고장을 30분 감춘다."""
        naps = []
        self.client.get_faults = [RuntimeError("Tripo GET 실패 [401]: bad key")]
        job = self.claim()
        with self.assertRaisesRegex(RuntimeError, "401"):
            ModelPipeline(job, "worker-1", self.client, lambda *_: None,
                          sleep=naps.append).run()
        self.assertEqual(self.client.polls, 1)
        self.assertEqual(naps, [])

    def test_download_hiccup_is_retried_without_resubmitting(self):
        """CDN 은 API 와 다른 호스트라 따로 흔들린다. 다 만들어 놓고 받는 길만
        막혀 작업을 버리면 크레딧을 그대로 버리는 것이다."""
        naps = []
        self.client.download_faults = [TransientError("Tripo 다운로드 통신 실패")]
        job = self.claim()
        delivered = []
        ModelPipeline(job, "worker-1", self.client,
                      lambda sid, path: delivered.append(path.read_bytes()),
                      sleep=naps.append).run()
        self.assertEqual(delivered, [b"glTF-merged"])
        self.assertEqual(self.client.submissions, ["model"])

    def test_persistent_outage_keeps_the_task_id_for_a_later_retry(self):
        """상대가 끝내 돌아오지 않아도 task_id 는 남아야 한다. 남아 있어야 나중에
        같은 작업을 이어받고, 크레딧을 다시 쓰지 않는다."""
        self.client.get_faults = [TransientError("Tripo GET 실패 [502]")] * 400
        job = self.claim()
        # 쉬는 동안 시계가 도는 것까지 흉내 내야 대기 한도(30분)에 닿는다.
        clock = [0.0]
        with patch("core.model_pipeline.time.monotonic", lambda: clock[0]):
            with self.assertRaisesRegex(PipelineFailure, "작업 ID를 유지"):
                ModelPipeline(job, "worker-1", self.client, lambda *_: None,
                              sleep=lambda s: clock.__setitem__(0, clock[0] + s)).run()
        self.assertEqual(job["steps"]["model"]["task_id"], "model")
        self.assertEqual(self.client.submissions, ["model"])

    def test_http_codes_are_split_into_retry_and_give_up(self):
        import urllib.error

        def error(code):
            return urllib.error.HTTPError("u", code, "m", None, None)
        for code in (408, 429, 500, 502, 503, 504):
            self.assertIsInstance(_classify(error(code), "x"), TransientError, code)
        for code in (400, 401, 403, 404):
            self.assertNotIsInstance(_classify(error(code), "x"), TransientError, code)
        # HTTPError 가 아닌 끊김(DNS·TCP·TLS)은 다시 걸어 볼 값어치가 있다.
        self.assertIsInstance(_classify(urllib.error.URLError("reset"), "x"), TransientError)


    def test_deleted_person_loses_job_lease_and_cannot_be_queued(self):
        job = self.claim()
        database.soft_delete("person-a")
        with self.assertRaisesRegex(RuntimeError, "lease"):
            model_queue.heartbeat("worker-1", "person-a")
        self.assertEqual(model_queue.enqueue("person-a"), "no-session")
        self.assertIsNone(model_queue.claim("worker-2"))


if __name__ == "__main__":
    unittest.main()
