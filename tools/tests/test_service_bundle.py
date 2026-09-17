"""배포 묶음 목록이 실제 파일을 가리키고 비밀·참여자 자료를 담지 않는지 확인한다."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service_bundle import ROOT, files_for


class ServiceBundleTests(unittest.TestCase):
    def test_every_listed_file_exists_and_is_inside_the_repository(self):
        for area in ("platform", "dialogue"):
            paths = files_for(area)
            self.assertTrue(paths)
            for path, name in paths:
                with self.subTest(area=area, name=name):
                    self.assertTrue(path.is_file(), name)
                    self.assertTrue(path.resolve().is_relative_to(ROOT))

    def test_platform_bundle_carries_the_survey_v2_sources(self):
        names = {name for _, name in files_for("platform")}
        for required in ("Web/app.py", "Web/survey_v2.py", "Web/static/index.html",
                         "Web/static/admin.html", "Web/static/presets_v2.json"):
            self.assertIn(required, names)
        self.assertNotIn("Web/persona.py", names)
        self.assertNotIn("Web/registration_input.py", names)

    def test_no_secret_or_participant_material_is_listed(self):
        for area in ("platform", "dialogue"):
            for _, name in files_for(area):
                with self.subTest(area=area, name=name):
                    self.assertFalse(name.endswith(".env"), name)
                    self.assertNotIn("Survey/data", name)
                    self.assertNotIn("workspace", name)


if __name__ == "__main__":
    unittest.main()
