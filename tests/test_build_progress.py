"""Builds run in the background with a progress page, real stages, a bar
that never goes backwards, and a cancel button."""
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from auth_support import AuthFixture, app  # noqa: E402

FIXTURE_CS = Path(__file__).resolve().parent / "fixtures" / "cs_courses.xlsx"


def fake_build(script: str):
    """A stand-in for main.py: prints the stage markers the real build prints."""
    return lambda: [sys.executable, "-c", script]


SUCCESS = (
    "import time,sys\n"
    "print('DIAGNOSTIC entering Stage 1 semester planning', flush=True)\n"
    "time.sleep(0.3)\n"
    "print('DIAGNOSTIC Stage 1 FEASIBLE; entering weekly solver', flush=True)\n"
    "time.sleep(0.3)\n"
    "print('DIAGNOSTIC weekly solver FEASIBLE', flush=True)\n"
    "print('Created: timetable.json', flush=True)\n"
)
FAILURE = "import sys\nprint('DIAGNOSTIC entering Stage 1', flush=True)\nprint('No feasible timetable', flush=True)\nsys.exit(1)\n"
SLOW = "import time\nprint('DIAGNOSTIC entering Stage 1', flush=True)\ntime.sleep(60)\n"


class BuildProgressTests(AuthFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.background = patch.dict(app.app.config, {"BACKGROUND_BUILDS": True})
        self.background.start()
        self.register()
        self.client.post("/setup/sample", data={"csrf_token": self.token()})

    def tearDown(self):
        for process in list(app._RUNNING_BUILDS.values()):
            if process.poll() is None:
                process.kill()
        app._RUNNING_BUILDS.clear()
        self.background.stop()
        super().tearDown()

    def token(self):
        with self.client.session_transaction() as sess:
            return sess["csrf_token"]

    def build_form(self):
        config = json.loads((self.workspace() / "schedule_config.json").read_text(encoding="utf-8"))
        form = {"csrf_token": self.token(), "return_to": "setup", "degree_years": "3",
                "semesters_per_year": "3", "degree_years_auto_shown": "1", "degree_years_auto": "on",
                "year_mode": "suggest", "program_name": "מדעי המחשב"}
        for day, values in config["days"].items():
            if values["enabled"]:
                form[f"enabled_{day}"] = "on"
            form[f"start_{day}"], form[f"end_{day}"] = values["start"], values["end"]
        return form

    def wait_for_result(self, seconds=20):
        deadline = time.time() + seconds
        seen = []
        while time.time() < deadline:
            data = self.client.get("/build/status").get_json()
            if "url" in data:
                return data["url"], seen
            seen.append(data["percent"])
            time.sleep(0.2)
        self.fail("the build did not finish")

    def test_build_shows_progress_then_opens_the_timetable(self):
        with patch.object(app, "_backend_command", fake_build(SUCCESS)):
            response = self.client.post("/generate", data=self.build_form())
            self.assertTrue(response.headers["Location"].endswith("/building"))
            page = self.client.get("/building").get_data(as_text=True)
            self.assertIn('role="progressbar"', page)
            self.assertIn("משבצים ימים ושעות", page)
            self.assertNotIn("מחזור ב׳", page)                      # sample has no semester-B intake
            self.assertIn("ביטול הבנייה", page)
            url, seen = self.wait_for_result()
        self.assertTrue(url.endswith("/calendar"))
        self.assertEqual(seen, sorted(seen))                         # the bar never goes back
        self.assertIn("מערכת השעות נבנתה בהצלחה", self.client.get("/calendar").get_data(as_text=True))

    def test_failed_build_returns_to_setup_with_the_reason(self):
        with patch.object(app, "_backend_command", fake_build(FAILURE)):
            self.client.post("/generate", data=self.build_form())
            url, _seen = self.wait_for_result()
        self.assertTrue(url.endswith("/setup"))
        self.assertIn("build-failure", self.client.get("/setup").get_data(as_text=True))

    def test_cancel_stops_the_build_and_keeps_everything(self):
        timetable = self.workspace() / "timetable.json"
        timetable.write_text("[]", encoding="utf-8")
        with patch.object(app, "_backend_command", fake_build(SLOW)):
            self.client.post("/generate", data=self.build_form())
            process = next(iter(app._RUNNING_BUILDS.values()))
            response = self.client.post("/build/cancel", data={"csrf_token": self.token()})
        self.assertTrue(response.headers["Location"].endswith("/setup"))
        self.assertIsNotNone(process.poll())                         # the solver process was stopped
        self.assertEqual(timetable.read_text(encoding="utf-8"), "[]")
        self.assertIn("הבנייה בוטלה", self.client.get("/setup").get_data(as_text=True))
        self.assertTrue(self.client.get("/building").headers["Location"].endswith("/setup"))

    def test_progress_follows_the_stage_markers(self):
        state = {"started": 100.0, "semester_b": False, "marks": {}, "percent": 0}
        stage, first = app._build_progress(state, "", 101.0)
        self.assertEqual(stage, "check")
        stage, second = app._build_progress(state, "entering Stage 1\nentering weekly solver", 102.0)
        self.assertEqual(stage, "place")
        stage, third = app._build_progress(state, "entering Stage 1\nentering weekly solver", 200.0)
        self.assertLess(third, 72)                                   # a stage never fills past its share
        self.assertLessEqual(first, second)
        self.assertLessEqual(second, third)

    def test_semester_b_adds_its_stage(self):
        keys = [s[0] for s in app._build_stages(True)]
        self.assertIn("intake", keys)
        self.assertNotIn("intake", [s[0] for s in app._build_stages(False)])


if __name__ == "__main__":
    unittest.main()
