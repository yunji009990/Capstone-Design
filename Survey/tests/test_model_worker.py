"""Independent worker tests. Tripo and registration delivery are simulated."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import database, model_queue, storage
from core.model_pipeline import ModelPipeline, PipelineFailure, SubmissionUnknown


class FakeTripo:
    def __init__(self):
        self.submissions = []
        self.unknown = False
        self.riggable = True

    def _submit(self, body):
        self.submissions.append(body["type"])
        if self.unknown:
            raise TimeoutError("lost submission response")
        return body["type"]

    def submit_image_to_3d(self, path):
        return self._submit({"type": "model"})

    def rig_model(self, task, **kwargs):
        return self._submit({"type": "rig"})

    def retarget_animation(self, task, pose):
        return self._submit({"type": "animation"})

    def get_task(self, task):
        output = ({"riggable": self.riggable, "rig_type": "biped"}
                  if task == "animate_prerigcheck" else {"model": "https://fake.invalid/model"})
        return {"data": {"status": "success", "output": output, "consumed_credit": 0}}

    def download_glb(self, url, dest):
        dest.write_bytes(b"glTF-test-result")


class ModelWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch.object(database, "DB_PATH", root / "sessions.db"),
                        patch.object(storage, "ASSETS_ROOT", root / "assets"),
                        patch.dict(os.environ, {"TRIPO_POSE": "preset:sit"})]
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

    def test_duplicate_requests_have_one_owner_and_ready_requires_delivery(self):
        job = self.claim()
        self.assertEqual(model_queue.enqueue("person-a"), "running")
        self.assertIsNone(model_queue.claim("worker-2"))
        delivered = []
        ModelPipeline(job, "worker-1", self.client, lambda sid, path: delivered.append(path.read_bytes())).run()
        self.assertEqual(delivered, [b"glTF-test-result"])
        self.assertEqual(database.get_session("person-a")["model_status"], "ready")
        self.assertEqual(model_queue.enqueue("person-a"), "ready")
        self.assertEqual(len(self.client.submissions), 4)

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
        self.assertEqual(len(self.client.submissions), 4)
        self.assertEqual(model_queue.get_job("person-a")["state"], "ready")

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

    def test_rig_failure_is_not_published_as_an_animated_model(self):
        job = self.claim()
        self.client.riggable = False
        delivered = []
        with self.assertRaises(PipelineFailure):
            ModelPipeline(job, "worker-1", self.client, lambda *_: delivered.append(True)).run()
        self.assertEqual(delivered, [])
        self.assertNotEqual(database.get_session("person-a")["model_status"], "ready")

    def test_deleted_person_loses_job_lease_and_cannot_be_queued(self):
        job = self.claim()
        database.soft_delete("person-a")
        with self.assertRaisesRegex(RuntimeError, "lease"):
            model_queue.heartbeat("worker-1", "person-a")
        self.assertEqual(model_queue.enqueue("person-a"), "no-session")
        self.assertIsNone(model_queue.claim("worker-2"))


if __name__ == "__main__":
    unittest.main()
