"""The guided Setup page: course file, settings, check and build, with the
panels and messages coming back to it."""
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
from auth_support import AuthFixture, app  # noqa: E402

FIXTURE_CS = Path(__file__).resolve().parent / "fixtures" / "cs_courses.xlsx"


class GuidedSetupTests(AuthFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        response = self.register()
        self.signup_location = response.headers.get("Location", "")

    def token(self):
        with self.client.session_transaction() as sess:
            return sess["csrf_token"]

    def upload(self, data: bytes, name="courses.xlsx", return_to="setup"):
        return self.client.post("/courses/upload", content_type="multipart/form-data", data={
            "csrf_token": self.token(), "return_to": return_to,
            "courses_excel": (io.BytesIO(data), name)})

    def page(self):
        return self.client.get("/setup").get_data(as_text=True)

    def build_form(self, **changes):
        config = json.loads((self.workspace() / "schedule_config.json").read_text(encoding="utf-8"))
        form = {"csrf_token": self.token(), "return_to": "setup", "degree_years": "3",
                "semesters_per_year": "3", "degree_years_auto_shown": "1", "degree_years_auto": "on",
                "year_mode": "suggest", "program_name": "מדעי המחשב"}
        for day, values in config["days"].items():
            if values["enabled"]:
                form[f"enabled_{day}"] = "on"
            form[f"start_{day}"], form[f"end_{day}"] = values["start"], values["end"]
        form.update(changes)
        return form

    def test_sign_up_goes_to_setup(self):
        self.assertTrue(self.signup_location.endswith("/setup"), self.signup_location)

    def test_new_account_sees_only_the_upload_step(self):
        page = self.page()
        self.assertIn('id="uploadForm"', page)
        self.assertEqual(page.count("gs-step is-waiting"), 2)
        self.assertNotIn('id="buildForm"', page)
        self.assertIn("לנסות עם נתוני דוגמה", page)

    def test_upload_returns_to_setup_and_opens_settings(self):
        response = self.upload(FIXTURE_CS.read_bytes(), name="קורסים מדעי המחשב.xlsx")
        self.assertTrue(response.headers["Location"].endswith("/setup"))
        page = self.page()
        self.assertIn('id="buildForm"', page)
        self.assertIn("קורסים מדעי המחשב.xlsx", page)       # the file's own name is shown
        self.assertIn("38 קורסים", page)
        self.assertIn('name="start_Monday"', page)            # days and hours are part of Setup

    def test_upload_from_the_home_page_still_returns_there(self):
        response = self.upload(FIXTURE_CS.read_bytes(), return_to="")
        self.assertIn("#course-upload", response.headers["Location"])
        self.assertFalse(response.headers["Location"].endswith("/setup"))

    def test_column_check_opens_over_setup(self):
        workbook = load_workbook(FIXTURE_CS)
        sheet = workbook.worksheets[0]
        for cell in sheet[1]:
            if cell.value == "שם השיעור":
                cell.value = "עמודה לא מוכרת"
        buffer = io.BytesIO()
        workbook.save(buffer)
        response = self.upload(buffer.getvalue())
        self.assertTrue(response.headers["Location"].endswith("/setup"))
        page = self.page()
        self.assertIn('id="mappingTitle"', page)
        self.assertIn('name="return_to" value="setup"', page)

    def test_sample_data_loads_file_and_days(self):
        response = self.client.post("/setup/sample", data={"csrf_token": self.token()})
        self.assertTrue(response.headers["Location"].endswith("/setup"))
        self.assertTrue((self.workspace() / "courses.xlsx").exists())
        config = json.loads((self.workspace() / "schedule_config.json").read_text(encoding="utf-8"))
        enabled = sorted(day for day, values in config["days"].items() if values["enabled"])
        self.assertEqual(enabled, ["Friday", "Monday", "Thursday"])
        self.assertEqual(config["program_name"], "מדעי המחשב (דוגמה)")
        page = self.page()
        self.assertIn("gs-check-line", page)
        self.assertIn("מערכת שעות מדעי המחשב (דוגמה) — 3 שנים.html", page)

    def test_a_badly_written_time_is_explained(self):
        self.client.post("/setup/sample", data={"csrf_token": self.token()})
        response = self.client.post("/generate", data=self.build_form(start_Monday="7"))
        self.assertTrue(response.headers["Location"].endswith("/setup"))
        self.assertIn("שעה:דקות", self.page())

    def test_build_from_setup(self):
        self.client.post("/setup/sample", data={"csrf_token": self.token()})
        with patch.object(app, "run_backend", return_value=(True, "")):
            response = self.client.post("/generate", data=self.build_form())
        self.assertTrue(response.headers["Location"].endswith("/calendar"))
        config = json.loads((self.workspace() / "schedule_config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["program_name"], "מדעי המחשב")
        self.assertEqual(config["course_file_name"], "sample_courses.xlsx")   # kept through the save

    def test_failed_build_comes_back_to_setup(self):
        self.client.post("/setup/sample", data={"csrf_token": self.token()})
        with patch.object(app, "run_backend", return_value=(False, "No feasible timetable")):
            response = self.client.post("/generate", data=self.build_form())
        self.assertTrue(response.headers["Location"].endswith("/setup"))
        self.assertIn("build-failure", self.page())

    def test_a_blocking_problem_disables_build(self):
        self.client.post("/setup/sample", data={"csrf_token": self.token()})
        (self.workspace() / "data_check.json").write_text(json.dumps([{
            "severity": "blocking", "code": "duplicate_id", "course_ids": ["8500013"],
            "he": "מספר הקורס 8500013 מופיע פעמיים.", "en": "Course 8500013 appears twice."}]), encoding="utf-8")
        with patch.object(app, "load_course_catalog", return_value=[]):
            page = self.page()
        self.assertIn("is-blocking", page)
        self.assertIn("מספר הקורס 8500013 מופיע פעמיים.", page)
        self.assertRegex(page, r'class="gs-button gs-big" disabled')


if __name__ == "__main__":
    unittest.main()
