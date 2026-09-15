"""Exercise transfer to a separate clone; never modify the real project or credentials."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tripo_handoff import apply, build


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.git(self.source, "init", "-q")
        self.git(self.source, "config", "core.autocrlf", "false")
        self.put("Web/app.py", "print('base')\n")
        self.put("Assets/Scripts/Raon/Legacy.cs", "// retired\n")
        self.put("Assets/Scenes/Scene_2.unity", "untouched scene\n")
        self.git(self.source, "add", ".")
        self.git(self.source, "-c", "user.name=Handoff Test", "-c", "user.email=test@example.invalid",
                 "commit", "-qm", "baseline")
        self.clone = self.root / "clone"
        self.git(self.root, "clone", "-q", str(self.source), str(self.clone))
        self.put("Web/app.py", "print('current')\n")
        self.put("Survey/model_worker.py", "print('worker')\n")
        (self.source / "Assets/Scripts/Raon/Legacy.cs").unlink()
        self.put("Web/.env", "TRIPO_API_KEY=private-sentinel-not-for-export\n")
        self.put("Survey/data/sessions/private/front.png", "private-photo-sentinel")
        self.put("tools/_work/tripo_trial_private/animated.glb", "private-model-sentinel")
        self.bundle = self.root / "handoff.zip"

    def git(self, repo, *args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True).stdout

    def put(self, name, text):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())

    def test_roundtrip_keeps_unrelated_files_and_index_and_excludes_private_data(self):
        index_before = self.git(self.source, "write-tree")
        build(self.source, self.bundle)
        self.assertEqual(self.git(self.source, "write-tree"), index_before)
        with zipfile.ZipFile(self.bundle) as archive:
            exported = b"".join(archive.read(name) for name in archive.namelist())
            self.assertNotIn(b"private-sentinel", exported)
            self.assertNotIn(b"private-photo-sentinel", exported)
            self.assertNotIn(b"private-model-sentinel", exported)
            self.assertNotIn("code/Assets/Scenes/Scene_2.unity", archive.namelist())
        checked = apply(self.clone, self.bundle)
        self.assertEqual(checked["changes"], 3)
        self.assertEqual((self.clone / "Web/app.py").read_text(), "print('base')\n")
        apply(self.clone, self.bundle, write=True)
        self.assertEqual((self.clone / "Web/app.py").read_text(), "print('current')\n")
        self.assertTrue((self.clone / "Survey/model_worker.py").is_file())
        self.assertFalse((self.clone / "Assets/Scripts/Raon/Legacy.cs").exists())
        self.assertEqual((self.clone / "Assets/Scenes/Scene_2.unity").read_text(), "untouched scene\n")
        self.assertEqual(apply(self.clone, self.bundle, write=True)["changes"], 0)

    def test_common_harness_transfers_without_local_settings(self):
        shared = {"AGENTS.md": "shared rules\n", "CLAUDE.md": "@AGENTS.md\n",
                  "tools/check.py": "# shared runner\n", ".claude/settings.json": "{}\n",
                  ".github/workflows/checks.yml": "name: checks\n",
                  "Web/registration_input.py": "# web registration contract\n",
                  "Web/tests/test_registration_flow.py": "# web API checks\n",
                  "Web/tests/test_registration_flow.cjs": "// browser script checks\n",
                  "docs/웹_등록_흐름_개선.md": "registration workflow\n"}
        for name, content in shared.items():
            self.put(name, content)
        self.put(".claude/settings.local.json", "private-settings-sentinel")
        build(self.source, self.bundle)
        with zipfile.ZipFile(self.bundle) as archive:
            self.assertNotIn("code/.claude/settings.local.json", archive.namelist())
        apply(self.clone, self.bundle, write=True)
        for name, content in shared.items():
            self.assertEqual((self.clone / name).read_text(), content)

    def test_local_edit_aborts_before_any_file_is_written_or_removed(self):
        build(self.source, self.bundle)
        (self.clone / "Web/app.py").write_text("local work\n")
        with self.assertRaisesRegex(ValueError, "Local edits"):
            apply(self.clone, self.bundle, write=True)
        self.assertTrue((self.clone / "Assets/Scripts/Raon/Legacy.cs").exists())
        self.assertFalse((self.clone / "Survey/model_worker.py").exists())

    def test_windows_checkout_line_endings_are_accepted(self):
        build(self.source, self.bundle)
        (self.clone / "Web/app.py").write_bytes(b"print('base')\r\n")
        apply(self.clone, self.bundle, write=True)
        self.assertEqual((self.clone / "Web/app.py").read_text(), "print('current')\n")

    def test_modified_archive_payload_aborts_before_writing(self):
        build(self.source, self.bundle)
        damaged = self.root / "damaged.zip"
        with zipfile.ZipFile(self.bundle) as source, zipfile.ZipFile(damaged, "w") as dest:
            for name in source.namelist():
                dest.writestr(name, b"tampered" if name == "code/Web/app.py" else source.read(name))
        with self.assertRaisesRegex(ValueError, "checksum"):
            apply(self.clone, damaged, write=True)
        self.assertTrue((self.clone / "Assets/Scripts/Raon/Legacy.cs").exists())

    def test_path_traversal_is_rejected(self):
        build(self.source, self.bundle)
        damaged = self.root / "traversal.zip"
        with zipfile.ZipFile(self.bundle) as source, zipfile.ZipFile(damaged, "w") as dest:
            for name in source.namelist():
                raw = source.read(name)
                if name == "manifest.json":
                    manifest = json.loads(raw)
                    manifest["files"][0]["path"] = "../outside.py"
                    raw = json.dumps(manifest).encode()
                dest.writestr(name, raw)
        with self.assertRaisesRegex(ValueError, "scope"):
            apply(self.clone, damaged, write=True)
        self.assertFalse((self.root / "outside.py").exists())


if __name__ == "__main__":
    unittest.main()
