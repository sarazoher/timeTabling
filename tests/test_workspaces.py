"""Every account has its own workspace: a new account starts empty and
accounts never see each other's course file, settings or timetable."""
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from auth_support import AuthFixture, app  # noqa: E402

FIXTURE_CS = Path(__file__).resolve().parent / "fixtures" / "cs_courses.xlsx"


class WorkspaceTests(AuthFixture, unittest.TestCase):
    def upload(self, client):
        with client.session_transaction() as sess:
            token = sess["csrf_token"]
        return client.post("/courses/upload", content_type="multipart/form-data", follow_redirects=True,
                           data={"csrf_token": token, "courses_excel": (io.BytesIO(FIXTURE_CS.read_bytes()), "cs.xlsx")})

    def test_new_account_starts_empty(self):
        self.register()
        page = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("מבני נתונים", page)                       # no course from anyone's catalog
        self.assertFalse((self.workspace() / "courses.xlsx").exists())
        self.assertFalse((self.workspace() / "timetable.json").exists())

    def test_accounts_are_separate(self):
        self.register("first@example.com")
        self.upload(self.client)
        self.assertTrue((self.workspace("first@example.com") / "courses.xlsx").exists())
        self.assertIn("מבני נתונים", self.client.get("/").get_data(as_text=True))
        other = app.app.test_client()
        self.register("second@example.com", client=other)
        self.assertNotIn("מבני נתונים", other.get("/").get_data(as_text=True))
        self.assertFalse((self.workspace("second@example.com") / "courses.xlsx").exists())
        # Settings are per account too.
        with other.session_transaction() as sess:
            token = sess["csrf_token"]
        other.post("/settings", data={"csrf_token": token, "program_name": "סיעוד", "degree_years": "4",
                                      "semesters_per_year": "3", "enabled_Sunday": "on",
                                      "start_Sunday": "08:00", "end_Sunday": "14:00"})
        second = json.loads((self.workspace("second@example.com") / "schedule_config.json").read_text(encoding="utf-8"))
        self.assertEqual(second["program_name"], "סיעוד")
        first_config = self.workspace("first@example.com") / "schedule_config.json"
        if first_config.exists():
            self.assertNotEqual(json.loads(first_config.read_text(encoding="utf-8")).get("program_name"), "סיעוד")

    def test_solver_runs_in_the_account_folder(self):
        self.register()
        calls = []

        def fake_run(command, cwd, **kwargs):
            calls.append((command, cwd))
            class Result: returncode, stdout, stderr = 0, "", ""
            return Result()

        with app.app.test_request_context():
            from flask import g
            from auth import database
            with database(app.app) as db:
                g.user = dict(db.execute("SELECT id, name, email FROM users").fetchone())
            with patch.object(app.subprocess, "run", fake_run):
                ok, _ = app.run_backend()
        self.assertTrue(ok)
        command, cwd = calls[0]
        self.assertEqual(Path(cwd), self.workspace())
        self.assertTrue(command[1].endswith("main.py") and Path(command[1]).is_absolute())
        self.assertIn("courses.xlsx", command)

    def test_no_account_no_workspace(self):
        with app.app.test_request_context():
            with self.assertRaises(RuntimeError):
                app.workspace_dir()


if __name__ == "__main__":
    unittest.main()
