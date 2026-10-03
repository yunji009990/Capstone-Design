"""운영 프로세스를 건드리지 않고 재시작 도구의 종료 범위·구성 검사를 확인한다."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("project_runtime", Path(__file__).resolve().parents[1] / "project_runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class RuntimeTests(unittest.TestCase):
    def test_unrelated_port_or_environment_does_not_match(self):
        home = Path("/srv/person")
        item = {"argv": [str(home / "venv/dialogue/bin/python"), "-m", "uvicorn", "dialogue_server:app",
                         "--app-dir", str(home / "capstone-server"), "--port", "8002"]}
        self.assertTrue(runtime.matches("dialogue", item, home))
        item["argv"][-1] = "18002"
        self.assertFalse(runtime.matches("dialogue", item, home))
        item["argv"][-1] = "8002"
        item["argv"][0] = "/other/venv/dialogue/bin/python"
        self.assertFalse(runtime.matches("dialogue", item, home))

    def test_duplicate_processes_refuse_action(self):
        with patch.object(runtime, "matches", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "multiple matching"):
                runtime.find("web", {1: {}, 2: {}}, Path("/srv/person"))

    def test_descendants_exclude_other_gpu_processes(self):
        table = {pid: {"pid": pid, "ppid": parent} for pid, parent in [(10, 1), (11, 10), (12, 11), (20, 1)]}
        self.assertEqual({10, 11, 12}, {p["pid"] for p in runtime.descendants(table[10], table)})

    def test_reused_pid_is_not_signalled(self):
        old = {"pid": 10, "start": 100, "argv": ["old"]}
        new = {"pid": 10, "ppid": 1, "start": 200, "argv": ["new"]}
        child = {"pid": 11, "ppid": 10, "start": 201, "argv": ["unrelated child"]}
        with patch.object(runtime, "read_process", return_value=new), patch.object(runtime, "processes", return_value={10: new, 11: child}), patch.object(runtime, "send_term") as kill:
            runtime.terminate(old)
            kill.assert_not_called()

    def test_busy_dialogue_blocks_shutdown(self):
        with patch.object(runtime, "find", side_effect=lambda name, *args: {} if name == "web" else ({"pid": 1} if name == "dialogue" else None)), patch.object(runtime, "get_json", return_value={"connections": 1}):
            with self.assertRaisesRegex(RuntimeError, "connections active"):
                runtime.idle_guard({}, Path("/srv/person"))

    def test_changed_configuration_blocks_start(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "settings.env").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "configuration changed"):
                runtime.validate_start({"configs": {"settings.env": "old-hash"}, "services": {}}, home)

    def test_stop_order_releases_frontends_before_dependencies(self):
        visited = []
        with patch.object(runtime, "processes", return_value={}), patch.object(runtime, "idle_guard"), patch.object(runtime, "find", side_effect=lambda name, *args: {"service": name}), patch.object(runtime, "terminate", side_effect=lambda item: visited.append(item["service"])), patch.object(runtime, "update_pidfile"), patch("builtins.print"):
            runtime.stop(Path("/srv/person"))
        self.assertEqual(["web", "tripo", "dialogue", "tts", "llm", "registration"], visited)

    def test_foreign_listener_blocks_all_launches(self):
        with patch.object(runtime, "validate_start"), patch.object(runtime, "processes", return_value={}), patch.object(runtime, "listening", return_value=True), patch.object(runtime.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(RuntimeError, "port is occupied"):
                runtime.start({"services": {"registration": {}}}, Path("/srv/person"), Path("unused"))
            launch.assert_not_called()

    def test_pidfile_for_different_process_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / runtime.PIDFILES["web"]
            path.parent.mkdir(parents=True)
            path.write_text("999\n")
            runtime.update_pidfile("web", {"pid": 123}, home, remove=True)
            self.assertEqual("999\n", path.read_text())

    def test_launch_waits_for_command_line_to_settle(self):
        transient = {"pid": 10, "start": 1, "argv": []}
        settled = dict(transient, argv=["expected"])
        process = Mock(pid=10)
        process.poll.return_value = None
        with patch.object(runtime, "read_process", side_effect=[transient, settled]), patch.object(runtime.time, "sleep"), patch.object(runtime, "terminate") as terminate:
            self.assertEqual(settled, runtime.launched_process(process, settled))
            terminate.assert_not_called()

    def test_command_title_change_does_not_lose_process_identity(self):
        with patch.object(runtime, "read_process", return_value={"start": 1, "argv": ["updated"]}):
            self.assertTrue(runtime.same_process({"pid": 10, "start": 1, "argv": ["initial"]}))

    def test_rollback_stops_only_services_launched_by_this_call(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            existing = {"pid": 10, "start": 1, "argv": ["existing"]}
            created = {"pid": 11, "start": 2, "argv": ["new"]}
            profile = {"services": {"registration": existing, "llm": dict(created, cwd=str(folder), env={})}}
            with patch.dict(runtime.SERVICES, {"registration": (8000, "", None, None), "llm": (8001, "", None, None)}, clear=True), patch.object(runtime, "validate_start"), patch.object(runtime, "processes", return_value={}), patch.object(runtime, "find", side_effect=lambda name, *args: existing if name == "registration" else None), patch.object(runtime, "listening", return_value=False), patch.object(runtime.subprocess, "Popen") as launch, patch.object(runtime, "read_process", return_value=created), patch.object(runtime, "matches", return_value=True), patch.object(runtime, "same_process", return_value=True), patch.object(runtime, "ready", side_effect=[True, RuntimeError("failed health")]), patch.object(runtime, "terminate") as terminate, patch.object(runtime, "update_pidfile"), patch("builtins.print"):
                launch.return_value.pid = 11
                with self.assertRaisesRegex(RuntimeError, "failed health"):
                    runtime.start(profile, folder, folder)
                terminate.assert_called_once_with(created)
                self.assertEqual(["new"], launch.call_args.args[0])
                self.assertEqual({}, launch.call_args.kwargs["env"])


if __name__ == "__main__":
    unittest.main()
