"""Data check: nothing in the course file is skipped, ignored or replaced
without a visible message (excel_reader issues + diagnostics.catalog_findings)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "timetable_backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from diagnostics import diagnose  # noqa: E402
from excel_reader import load_courses  # noqa: E402

COLUMNS = ["מס.שעור", "שם השיעור", "תיאור ד.קדם", "תיאור ד.מקבילה", "שנת לימודים",
           "שם המרצה", "נ.זיכוי", 'ש"ש', "סוג שיעור", "קטגוריית קורס"]


def row(cid, name, year=1, lecturer="L", credits=2, weekly=1, kind="שעור", prereq=None, category="חובה", **extra):
    data = dict(zip(COLUMNS, [cid, name, prereq, None, year, lecturer, credits, weekly, kind, category]))
    data.update(extra)
    return data


CONFIG = {"degree_years": 3, "semesters_per_year": 3,
          "days": {"Monday": {"enabled": True, "start": "08:00", "end": "14:00"}},
          "lecturer_availability": {}}


class DataCheckTests(unittest.TestCase):
    def check(self, rows, config=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "courses.xlsx"
            with pd.ExcelWriter(path) as writer:
                pd.DataFrame(rows).to_excel(writer, sheet_name="גיליון2", index=False)
            issues = []
            courses = load_courses(path, issues)
        findings = diagnose(courses, {**CONFIG, **(config or {})}, issues)
        return courses, {f.code: f for f in findings}

    def test_row_with_missing_value_is_reported_not_silent(self):
        courses, found = self.check([row(1, "A"), row(2, "B", credits=None)])
        self.assertEqual([c.course_id for c in courses], [1])
        self.assertEqual(found["row_skipped_missing"].severity, "warning")
        self.assertIn('"B"', found["row_skipped_missing"].he)
        self.assertIn("נקודות זכות", found["row_skipped_missing"].he)

    def test_completely_empty_row_is_not_a_course(self):
        courses, found = self.check([row(1, "A"), {c: None for c in COLUMNS}])
        self.assertEqual(len(courses), 1)
        self.assertNotIn("row_skipped_missing", found)

    def test_invalid_number_is_reported_instead_of_crashing(self):
        courses, found = self.check([row(1, "A"), row(2, "B", credits="שתיים")])
        self.assertEqual(len(courses), 1)
        self.assertIn("שתיים", found["row_skipped_invalid"].he)

    def test_hebrew_letter_years_are_read(self):
        courses, found = self.check([row(1, "A", year="א"), row(2, "B", year="ג'")])
        self.assertEqual([c.year for c in courses], [1, 3])
        self.assertNotIn("year_letters", found)                   # not reported (by request)

    def test_minor_value_notices_are_not_reported(self):
        # Removed on request: the values are still handled the same way.
        courses, found = self.check([
            row(1, "A", **{"גמישות": "maybe"}),
            row(2, "B", credits=3, weekly=2, kind="הרצאה ותרגולים"),
            row(3, "C", credits=4, weekly=2, kind="שעור ותרגול"),   # 0 tutorial hours
        ])
        self.assertEqual(courses[0].course_category, "חובה")
        self.assertEqual(courses[1].tutorial_hours, 0)
        self.assertEqual(courses[2].tutorial_hours, 0)
        for code in ("category_invalid", "flexibility_invalid", "lesson_type_unrecognized", "tutorial_zero"):
            self.assertNotIn(code, found)

    def test_unknown_column_is_reported(self):
        _, found = self.check([row(1, "A", **{"תחום": "חטיבה", "קטגוריה": "בחירה"})])
        self.assertIn("'תחום'", found["unknown_columns"].he)
        self.assertIn("'קטגוריה'", found["unknown_columns"].he)

    def test_duplicate_id_blocks_and_duplicate_name_is_silent(self):
        _, found = self.check([row(1, "A"), row(1, "B"), row(2, "C"), row(3, "C")])
        self.assertEqual(found["duplicate_id"].severity, "blocking")
        self.assertIn("rows 2 and 3", found["duplicate_id"].en)
        self.assertNotIn("duplicate_name", found)

    def test_self_requirement_is_not_reported(self):
        _, found = self.check([row(1, "A", prereq="A")])
        self.assertNotIn("self_requirement", found)

    def test_ignored_or_alternative_is_info(self):
        _, found = self.check([row(1, "A"), row(2, "B", prereq="A / Old name of A")])
        self.assertEqual(found["or_alternative_ignored"].severity, "info")
        self.assertIn("Old name of A", found["or_alternative_ignored"].en)

    def test_stale_availability_is_not_reported(self):
        _, found = self.check([row(1, "A")], {"lecturer_availability": {"Gone": {}}})
        self.assertNotIn("stale_lecturer_availability", found)

    def test_category_is_required_and_never_guessed(self):
        from excel_reader import MissingColumnsError
        # Without the column the file is not loaded at all.
        with self.assertRaises(MissingColumnsError) as caught:
            self.check([{k: v for k, v in row(1, "A").items() if k != "קטגוריית קורס"}])
        self.assertIn("קטגוריית קורס", caught.exception.missing)
        # An empty or unknown cell: that course is reported and not loaded,
        # even when its name says "סמינר".
        courses, found = self.check([row(1, "A"), row(2, "סמינר מחקר", category=None), row(3, "C", category="אולי")])
        self.assertEqual([c.name for c in courses], ["A"])
        self.assertIn("קטגוריית קורס", found["row_skipped_missing"].he)
        self.assertIn("חובה, בחירה, סמינר, פרויקט או כללי", found["row_skipped_invalid"].he)

    def test_category_spellings(self):
        courses, _ = self.check([row(1, "A", category=" Elective "), row(2, "B", category="קורס בחירה"),
                                 row(3, "C", category="Seminar"), row(4, "D", category="general")])
        self.assertEqual([c.course_category for c in courses], ["בחירה", "בחירה", "סמינר", "כללי"])

    def test_clean_file_has_no_findings(self):
        courses, found = self.check([row(1, "A"), row(2, "B", prereq="A", year=2)])
        self.assertEqual(len(courses), 2)
        self.assertEqual(found, {})


class DefaultsTests(DataCheckTests):
    """Optional columns: missing -> stated default, reported once, summarised."""

    def drop(self, rows, *columns):
        return [{k: v for k, v in r.items() if k not in columns} for r in rows]

    def test_missing_lecturer_gets_one_per_course(self):
        courses, found = self.check([row(1, "A", lecturer=None), row(2, "B", lecturer="Real")])
        self.assertEqual([c.lecturer for c in courses], ["מרצה 1", "Real"])
        self.assertEqual(found["lecturer_default"].severity, "warning")
        self.assertIn("ל-1 קורסים אין מרצה (A)", found["lecturer_default"].he)

    def test_no_lecturer_column_at_all(self):
        courses, found = self.check(self.drop([row(1, "A"), row(2, "B")], "שם המרצה"))
        self.assertEqual({c.lecturer for c in courses}, {"מרצה 1", "מרצה 2"})
        self.assertIn("אין מרצה בקובץ לאף קורס", found["lecturer_default"].he)

    def test_missing_lesson_type_is_a_lecture(self):
        courses, found = self.check(self.drop([row(1, "A")], "סוג שיעור"))
        self.assertEqual((courses[0].course_type, courses[0].tutorial_hours), ("שעור", 0))
        self.assertNotIn("lesson_type_default", found)

    def test_missing_weekly_hours_come_from_credits(self):
        courses, found = self.check(self.drop([row(1, "A", credits=3), row(2, "B", credits=2.5)], 'ש"ש'))
        self.assertEqual([c.lecture_hours for c in courses], [3, 3])     # 2.5 rounded up to 3
        self.assertEqual(found["hours_default"].severity, "warning")
        self.assertIn("B (2.5→3)", found["hours_default"].he)

    def test_lecture_tutorial_without_hours_is_lecture_only_and_said(self):
        courses, found = self.check([row(1, "A", credits=4, weekly=None, kind="שעור ותרגול")])
        self.assertEqual((courses[0].lecture_hours, courses[0].tutorial_hours), (4, 0))
        self.assertIn("שובצה הרצאה בלבד: A", found["hours_default"].he)
        self.assertNotIn("tutorial_zero", found)

    def test_no_corequisite_column(self):
        courses, found = self.check(self.drop([row(1, "A")], "תיאור ד.מקבילה"))
        self.assertIsNone(courses[0].parallel_requirement)
        self.assertNotIn("no_corequisite_column", found)

    def test_core_columns_are_still_required(self):
        from excel_reader import MissingColumnsError
        with self.assertRaises(MissingColumnsError):
            self.check(self.drop([row(1, "A")], "נ.זיכוי"))


class DuplicateIdStopsBuildTests(unittest.TestCase):
    """main.py must stop before solving: a duplicate ID would not fail on its own."""

    def test_build_stops(self):
        import os
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            with pd.ExcelWriter(work / "courses.xlsx") as writer:
                pd.DataFrame([row(1, "A"), row(1, "B")]).to_excel(writer, sheet_name="גיליון2", index=False)
            (work / "config.json").write_text(json.dumps(CONFIG), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "timetable_backend" / "main.py"), "courses.xlsx",
                 "--config", "config.json", "--time-limit", "5"],
                cwd=work, capture_output=True, text=True, timeout=120,
                env={**os.environ, "PYTHONPATH": str(ROOT / "timetable_backend")})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate course ID", result.stderr)
        self.assertNotIn("Stage 1 FEASIBLE", result.stdout)


class DashboardPanelTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / "timetable_frontend"))
        from auth_support import AuthFixture
        import app as app_module
        self.app = app_module
        self.fixture = AuthFixture()
        self.fixture.setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.check_file = Path(self.tmp.name) / "data_check.json"
        # The panel is shown once a course file exists in the account.
        (Path(self.tmp.name) / "courses.xlsx").write_bytes(b"")
        self.patches = [patch.object(app_module, "DATA_CHECK_FILE", self.check_file),
                        patch.object(app_module, "COURSES_EXCEL_FILE", Path(self.tmp.name) / "courses.xlsx"),
                        patch.object(app_module, "load_course_catalog", lambda: [])]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.fixture.tearDown()
        self.tmp.cleanup()

    def page(self, findings):
        self.check_file.write_text(json.dumps(findings, ensure_ascii=False), encoding="utf-8")
        self.fixture.register()
        return self.fixture.client.get("/").get_data(as_text=True)

    def test_panel_lists_findings_by_severity(self):
        html = self.page([
            {"severity": "blocking", "code": "x", "he": "בעיה חוסמת לדוגמה", "en": "b", "course_ids": []},
            {"severity": "warning", "code": "y", "he": "שורה 7 לא נטענה", "en": "w", "course_ids": []},
            {"severity": "info", "code": "z", "he": "פרט לידיעה", "en": "i", "course_ids": []},
        ])
        self.assertIn('id="data-check"', html)
        for text in ("בעיה חוסמת לדוגמה", "שורה 7 לא נטענה", "פרט לידיעה"):
            self.assertIn(text, html)
        self.assertIn("data-check-group blocking", html)

    def test_panel_says_so_when_everything_is_used(self):
        self.assertIn("לא נמצאו בעיות", self.page([]))


if __name__ == "__main__":
    unittest.main()
