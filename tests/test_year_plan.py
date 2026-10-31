"""Study-year setting: the fewest year moves, the suggest -> approve flow,
automatic mode, and strict mode (year_plan.py, main.py, app.py)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "timetable_backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from excel_reader import load_courses  # noqa: E402
from year_plan import apply_year_overrides, find_year_moves, override_findings, year_mode  # noqa: E402

COLUMNS = ["מס.שעור", "שם השיעור", "תיאור ד.קדם", "תיאור ד.מקבילה", "שנת לימודים",
           "שם המרצה", "נ.זיכוי", 'ש"ש', "סוג שיעור", "קטגוריית קורס"]
CONFIG = {"degree_years": 3, "semesters_per_year": 3}


def row(cid, name, year, prereq=None):
    return dict(zip(COLUMNS, [cid, name, prereq, None, year, f"L{cid}", 2, 1, "שעור", "חובה"]))


# A 4-course chain inside year 2, which has 3 semesters, plus a course that
# depends on the chain's end in the same year.
CHAIN = [row(1, "A", 2), row(2, "B", 2, "A"), row(3, "C", 2, "B"), row(4, "D", 2, "C"),
         row(5, "E", 2, "D"), row(6, "F", 1)]


def write_catalog(rows, path):
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="גיליון2", index=False)


def courses_from(rows):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "courses.xlsx"
        write_catalog(rows, path)
        return load_courses(path, [])


class FindMovesTests(unittest.TestCase):
    def test_nothing_moves_when_everything_fits(self):
        self.assertEqual(find_year_moves(courses_from([row(1, "A", 1), row(2, "B", 1, "A")]), CONFIG), [])

    def test_fewest_moves_and_reasons(self):
        moves = {m.name: m for m in find_year_moves(courses_from(CHAIN), CONFIG)}
        self.assertEqual(set(moves), {"D", "E"})              # A, B, C stay; F untouched
        self.assertEqual((moves["D"].from_year, moves["D"].to_year), (2, 3))
        self.assertIn("needs 4 semesters", moves["D"].reason_en)
        self.assertIn('prerequisite "D" also moves', moves["E"].reason_en)
        self.assertIn("צריכה 4 סמסטרים", moves["D"].reason_he)

    def test_or_alternative_in_an_earlier_year_needs_no_move(self):
        rows = [row(1, "A", 2), row(2, "B", 2, "A"), row(3, "C", 2, "B"), row(7, "Old", 1),
                row(4, "D", 2, "(C / Old)")]
        self.assertEqual(find_year_moves(courses_from(rows), CONFIG), [])

    def test_no_later_year_left_returns_none(self):
        rows = [row(1, "A", 3), row(2, "B", 3, "A"), row(3, "C", 3, "B"), row(4, "D", 3, "C")]
        self.assertIsNone(find_year_moves(courses_from(rows), CONFIG))

    def test_overrides_change_the_year_and_are_reported(self):
        courses = courses_from(CHAIN)
        records = apply_year_overrides(courses, {"4": 3, "5": 3, "999": 2})
        by_name = {c.name: c for c in courses}
        self.assertEqual((by_name["D"].year, by_name["D"].original_year), (3, 2))
        self.assertEqual(find_year_moves(courses, CONFIG), [])     # nothing left to move
        codes = {f["code"]: f for f in override_findings(records)}
        self.assertIn('"D" from year 2 to year 3', codes["year_override"]["en"])
        self.assertIn("999", codes["year_override_stale"]["en"])

    def test_unknown_mode_falls_back_to_suggest(self):
        self.assertEqual(year_mode({"year_mode": "whatever"}), "suggest")
        self.assertEqual(year_mode({}), "suggest")


class BackendStopTests(unittest.TestCase):
    """main.py: suggest mode stops before solving; strict mode does not suggest."""

    def run_main(self, mode, rows=CHAIN, **extra):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name)
        write_catalog(rows, work / "courses.xlsx")
        # The chain file only reaches year 2; a 3-year degree is set by hand.
        config = {**CONFIG, "year_mode": mode, "degree_years_auto": False,
                  "days": {"Sunday": {"enabled": True, "start": "08:00", "end": "20:00"}}, **extra}
        (work / "config.json").write_text(json.dumps(config), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(ROOT / "timetable_backend" / "main.py"), "courses.xlsx",
             "--config", "config.json", "--time-limit", "5"],
            cwd=work, capture_output=True, text=True, timeout=120,
            env={**os.environ, "PYTHONPATH": str(ROOT / "timetable_backend")})
        moves_file = work / "year_moves.json"
        return result, (json.loads(moves_file.read_text(encoding="utf-8")) if moves_file.exists() else None)

    def test_suggest_mode_stops_with_the_moves(self):
        result, report = self.run_main("suggest")
        self.assertEqual(result.returncode, 3, result.stderr[-2000:])
        self.assertIn("YEAR_MOVES_SUGGESTED", result.stdout)
        self.assertNotIn("Stage 1", result.stdout)                # stopped before solving
        self.assertEqual({m["name"] for m in report["moves"]}, {"D", "E"})

    def test_strict_mode_suggests_nothing(self):
        result, report = self.run_main("strict")
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(report)
        self.assertIn("needs 4 semesters", result.stdout)    # the diagnosis explains it


class DegreeYearsTests(unittest.TestCase):
    """The number of degree years follows the course file unless set by hand."""

    def test_helpers(self):
        from year_plan import apply_degree_years, degree_years_auto, file_degree_years
        courses = courses_from([row(1, "A", 1), row(2, "B", 4)])
        self.assertTrue(degree_years_auto({}))                        # default: from the file
        config = {"degree_years": 3}
        self.assertTrue(apply_degree_years(config, courses))
        self.assertEqual(config["degree_years"], 4)
        manual = {"degree_years": 3, "degree_years_auto": False}
        self.assertFalse(apply_degree_years(manual, courses))
        self.assertEqual(manual["degree_years"], 3)
        apply_year_overrides(courses, {"1": 2})                       # an approved move is not the file
        self.assertEqual(file_degree_years(courses), 4)

    def test_build_uses_the_file_years(self):
        rows = [row(1, "A", 1), row(2, "B", 3, "A")]
        result, _ = BackendStopTests.run_main(self, "suggest", rows, degree_years=2, degree_years_auto=True)
        self.assertIn("DEGREE_YEARS 3 (from the course file)", result.stdout)
        self.assertNotIn("is a year-3 course but the degree is set to 2 years", result.stdout)

    def test_manual_years_are_kept(self):
        rows = [row(1, "A", 1), row(2, "B", 3, "A")]
        result, _ = BackendStopTests.run_main(self, "suggest", rows, degree_years=2)
        self.assertIn("DEGREE_YEARS 2 (set in Settings)", result.stdout)
        self.assertIn("year-3 course but the degree is set to 2 years", result.stdout)


class CatalogRefreshYearsTests(unittest.TestCase):
    """Reading a course file updates the saved number of years (automatic mode)."""

    def test_refresh_sets_and_respects_manual(self):
        sys.path.insert(0, str(ROOT / "timetable_frontend"))
        import app as app_module
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_catalog([row(1, "A", 1), row(2, "B", 4)], tmp / "courses.xlsx")
            config_file = tmp / "schedule_config.json"
            with patch.object(app_module, "CONFIG_FILE", config_file), \
                 patch.object(app_module, "COURSES_EXCEL_FILE", tmp / "courses.xlsx"), \
                 patch.object(app_module, "COURSES_FILE", tmp / "courses.json"), \
                 patch.object(app_module, "workspace_dir", lambda: tmp), \
                 patch.object(app_module, "UNRESOLVED_FILE", tmp / "unresolved_requirements.json"), \
                 patch.object(app_module, "DATA_CHECK_FILE", tmp / "data_check.json"):
                config_file.write_text(json.dumps({**app_module.DEFAULT_CONFIG, "degree_years": 3}), encoding="utf-8")
                app_module._refresh_course_catalog_only()
                self.assertEqual(json.loads(config_file.read_text(encoding="utf-8"))["degree_years"], 4)
                config_file.write_text(json.dumps({**app_module.DEFAULT_CONFIG, "degree_years": 3,
                                                   "degree_years_auto": False}), encoding="utf-8")
                app_module._refresh_course_catalog_only()
                saved = json.loads(config_file.read_text(encoding="utf-8"))
                self.assertEqual(saved["degree_years"], 3)
                codes = {f["code"] for f in json.loads((tmp / "data_check.json").read_text(encoding="utf-8"))}
                self.assertIn("degree_too_short", codes)                 # manual 3 < file's 4


class DashboardFlowTests(unittest.TestCase):
    """app.py: panel over the dashboard, approve -> overrides + build, dismiss,
    remove, settings save."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "timetable_frontend"))
        from auth_support import AuthFixture
        import app as app_module
        self.app = app_module
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.config_file = tmp / "schedule_config.json"
        self.courses_file = tmp / "courses.xlsx"          # the Data page needs a course file to exist
        self.courses_file.write_bytes(b"")
        base = dict(app_module.DEFAULT_CONFIG)
        self.config_file.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
        self.moves_file = tmp / "year_moves.json"
        self.builds = []
        self.patches = [
            patch.object(app_module, "CONFIG_FILE", self.config_file),
            patch.object(app_module, "YEAR_MOVES_FILE", self.moves_file),
            patch.object(app_module, "BUILD_FAILURE_FILE", tmp / "last_build_failure.json"),
            patch.object(app_module, "DATA_CHECK_FILE", tmp / "data_check.json"),
            patch.object(app_module, "COURSES_EXCEL_FILE", self.courses_file),
            patch.object(app_module, "load_course_catalog",
                         lambda: [{"course_id": 4, "name": "D", "year": 2}, {"course_id": 5, "name": "E", "year": 2}]),
            patch.object(app_module, "_refresh_course_catalog_only", lambda: None),
            patch.object(app_module, "run_backend", self.fake_backend),
        ]
        for p in self.patches:
            p.start()
        self.fixture = AuthFixture()
        self.fixture.setUp()
        self.fixture.register()
        self.client = self.fixture.client

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.fixture.tearDown()
        self.tmp.cleanup()

    def fake_backend(self):
        config = json.loads(self.config_file.read_text(encoding="utf-8"))
        self.builds.append(config)
        if config.get("year_mode") == "suggest" and not config.get("year_overrides"):
            self.moves_file.write_text(json.dumps({"mode": "suggest", "moves": [
                {"course_id": 4, "name": "D", "from_year": 2, "to_year": 3,
                 "reason_he": "שרשרת ארוכה", "reason_en": "long chain"},
                {"course_id": 5, "name": "E", "from_year": 2, "to_year": 3,
                 "reason_he": "קדם עובר", "reason_en": "prerequisite moves"}]}), encoding="utf-8")
            return False, "YEAR_MOVES_SUGGESTED"
        return True, ""

    def token(self):
        with self.client.session_transaction() as sess:
            return sess["csrf_token"]

    def settings_form(self, **extra):
        form = {"csrf_token": self.token(), "degree_years": "3", "semesters_per_year": "3",
                "enabled_Monday": "on", "start_Monday": "08:00", "end_Monday": "14:00"}
        form.update(extra)
        return form

    def config(self):
        return json.loads(self.config_file.read_text(encoding="utf-8"))

    def test_build_shows_panel_then_approve_saves_and_builds(self):
        response = self.client.post("/generate", data=self.settings_form(year_mode="suggest"))
        self.assertTrue(response.headers["Location"].endswith("/"))
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="yearReviewTitle"', page)              # panel over the dashboard
        self.assertIn("behind-panel", page)
        self.assertIn("שרשרת ארוכה", page)                   # the reason, in the page language
        response = self.client.post("/years/approve", data={"csrf_token": self.token()})
        self.assertIn("/calendar", response.headers["Location"])
        self.assertEqual(self.config()["year_overrides"], {"4": 3, "5": 3})
        self.assertEqual(len(self.builds), 2)                     # approve runs the build
        self.assertNotIn('id="yearReviewTitle"', self.client.get("/").get_data(as_text=True))

    def test_dismiss_changes_nothing(self):
        self.client.post("/generate", data=self.settings_form(year_mode="suggest"))
        self.client.post("/years/dismiss", data={"csrf_token": self.token()})
        self.assertEqual(self.config()["year_overrides"], {})
        page = self.client.get("/").get_data(as_text=True)
        self.assertNotIn('id="yearReviewTitle"', page)

    def test_program_name_is_saved_and_kept(self):
        self.client.post("/settings", data=self.settings_form(program_name="  מדעי   המחשב "))
        self.assertEqual(self.config()["program_name"], "מדעי המחשב")
        # A form without the field (the days page) keeps it.
        self.client.post("/settings", data=self.settings_form())
        self.assertEqual(self.config()["program_name"], "מדעי המחשב")
        self.assertIn('id="programName" maxlength="80" value="מדעי המחשב"', self.client.get("/data").get_data(as_text=True))

    def test_settings_years_from_file_checkbox(self):
        self.client.post("/settings", data=self.settings_form(degree_years_auto_shown="1", degree_years="5"))
        self.assertEqual((self.config()["degree_years_auto"], self.config()["degree_years"]), (False, 5))
        self.client.post("/settings", data=self.settings_form(degree_years_auto_shown="1", degree_years_auto="on"))
        self.assertTrue(self.config()["degree_years_auto"])
        # A form without the checkbox (the days page) keeps the saved choice.
        self.client.post("/settings", data=self.settings_form())
        self.assertTrue(self.config()["degree_years_auto"])
        page = self.client.get("/data").get_data(as_text=True)
        self.assertIn('id="degreeYearsAuto" name="degree_years_auto" checked', page)
        self.assertIn('data-file-years="2"', page)                  # highest year in the catalog

    def test_settings_save_without_building_and_remove_override(self):
        self.client.post("/settings", data=self.settings_form(year_mode="strict", degree_years="4"))
        self.assertEqual((self.config()["year_mode"], self.config()["degree_years"]), ("strict", 4))
        self.assertEqual(self.builds, [])
        config = self.config()
        config["year_overrides"] = {"4": 3}
        self.config_file.write_text(json.dumps(config), encoding="utf-8")
        page = self.client.get("/data").get_data(as_text=True)
        self.assertIn("<bdi>D</bdi>: שנה 2 ← 3", page)
        self.client.post("/years/override/remove", data={"csrf_token": self.token(), "course_id": "4"})
        self.assertEqual(self.config()["year_overrides"], {})

    def test_settings_section_offers_the_three_rules(self):
        page = self.client.get("/data").get_data(as_text=True)
        for mode in ("strict", "suggest", "auto"):
            self.assertIn(f'name="year_mode" value="{mode}"', page)
        self.assertIn('value="suggest" checked', page)            # default


if __name__ == "__main__":
    unittest.main()
