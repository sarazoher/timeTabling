"""Plain-language pre-solve diagnosis (timetable_backend/diagnostics.py).

Each blocking rule is checked two ways: it fires on a case the solver proves
infeasible, and it stays silent on a feasible case. A blocking finding on a
feasible case would be a false alarm, which is the worst failure here.
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "timetable_backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from diagnostics import diagnose  # noqa: E402
from models import Course  # noqa: E402
from scheduler import plan_baseline_a  # noqa: E402


def course(i, hours=1.0, year=1, prereq=None, lecturer=None, category="חובה", tutorial=0.0, name=None):
    """hours = academic hours (45 min each) of the lecture."""
    return Course(i, name or f"Course {i}", prereq, None, year, lecturer or f"L{i}",
                  hours, hours / 2, "שעור", category, hours, tutorial, "OK")


def config(days, semesters=3, years=1, lecturers=None):
    return {"degree_years": years, "semesters_per_year": semesters,
            "days": {d: {"enabled": True, "start": s, "end": e} for d, (s, e) in days.items()},
            "lecturer_availability": lecturers or {}}


def blocking(findings):
    return [f.code for f in findings if f.severity == "blocking"]


def solvable(courses, cfg):
    try:
        plan_baseline_a(courses, cfg, 10)
        return True
    except RuntimeError:
        return False


class BlockingRuleTests(unittest.TestCase):
    def assert_rule(self, courses, cfg, code):
        findings = diagnose(courses, cfg)
        self.assertIn(code, blocking(findings), [f.en for f in findings])
        self.assertFalse(solvable(courses, cfg), "diagnosis says impossible, solver must agree")

    def test_meeting_longer_than_any_day(self):
        # 6 academic hours = 4:30 continuous; the only day is 4 h long
        self.assert_rule([course(1, hours=6)], config({"Monday": ("08:00", "12:00")}), "meeting_too_long")

    def test_lecturer_availability_can_make_a_meeting_too_long(self):
        cfg = config({"Monday": ("08:00", "14:00")},
                     lecturers={"L1": {"Monday": {"enabled": True, "start": "08:00", "end": "10:00"}}})
        self.assert_rule([course(1, hours=4)], cfg, "meeting_too_long")  # 3:00 > 2:00

    def test_prerequisite_chain_longer_than_the_year(self):
        chain = [course(1)] + [course(i, prereq=f"Course {i - 1}") for i in range(2, 5)]
        self.assert_rule(chain, config({"Monday": ("08:00", "12:00")}), "year_too_short")

    def test_or_chain_uses_the_quickest_alternative(self):
        # Course 4 needs (Course 3 OR Course 1). Via Course 1 it fits in 3 semesters.
        courses = [course(1), course(2, prereq="Course 1"), course(3, prereq="Course 2"),
                   course(4, prereq="Course 3 / Course 1")]
        cfg = config({"Monday": ("08:00", "12:00")})
        self.assertNotIn("year_too_short", blocking(diagnose(courses, cfg)))
        self.assertTrue(solvable(courses, cfg))

    def test_weekly_capacity(self):
        # 4 core courses x 1:30 = 6 h; one 1:30 day x 3 semesters = 4:30
        self.assert_rule([course(i, hours=2) for i in range(1, 5)],
                         config({"Monday": ("08:00", "09:30")}), "weekly_capacity")

    def test_two_long_meetings_forced_into_one_semester_on_one_long_day(self):
        # The nursing case: A->B->C chains, both B are 6 h and fit only on Friday.
        courses = [course(1), course(2, hours=8, prereq="Course 1"), course(3, prereq="Course 2"),
                   course(4), course(5, hours=8, prereq="Course 4"), course(6, prereq="Course 5")]
        cfg = config({"Monday": ("17:00", "22:00"), "Friday": ("08:00", "14:00")})
        self.assert_rule(courses, cfg, "long_days")
        # One more long day makes it possible, and the rule goes quiet.
        cfg["days"]["Sunday"] = {"enabled": True, "start": "08:00", "end": "14:00"}
        self.assertEqual(blocking(diagnose(courses, cfg)), [])
        self.assertTrue(solvable(courses, cfg))

    def test_degree_too_short(self):
        self.assertIn("degree_too_short",
                      blocking(diagnose([course(1, year=2)], config({"Monday": ("08:00", "12:00")}))))


class NoFalseAlarmTests(unittest.TestCase):
    def test_feasible_plan_has_no_blocking_findings(self):
        courses = [course(1), course(2, prereq="Course 1"), course(3, prereq="Course 2"),
                   course(4, hours=4), course(5, category="בחירה"), course(6, category="בחירה")]
        cfg = config({"Monday": ("08:00", "12:00"), "Tuesday": ("08:00", "12:00")})
        self.assertEqual(blocking(diagnose(courses, cfg)), [])
        self.assertTrue(solvable(courses, cfg))

    def test_cs_catalog_with_saved_config(self):
        from excel_reader import load_courses
        fixtures = Path(__file__).resolve().parent / "fixtures"
        cfg = json.loads((fixtures / "cs_schedule_config.json").read_text(encoding="utf-8"))
        courses = load_courses(fixtures / "cs_courses.xlsx")
        self.assertEqual(blocking(diagnose(courses, cfg)), [])


class WarningTests(unittest.TestCase):
    def test_unresolved_and_malformed_cells_are_warnings(self):
        courses = [course(1), course(2, prereq="Missing course"), course(3, prereq="(Course 1 / Course 2")]
        findings = diagnose(courses, config({"Monday": ("08:00", "12:00")}))
        codes = {f.code for f in findings if f.severity == "warning"}
        self.assertEqual(codes, {"unresolved_requirement", "malformed_requirement"})
        malformed = next(f for f in findings if f.code == "malformed_requirement")
        self.assertIn("חסר סוגר", malformed.he)


class FailureBoxTests(unittest.TestCase):
    """A failed build shows the diagnosis as readable sentences, with the raw
    log folded away (the report is read from disk, not the session cookie)."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "timetable_frontend"))
        from auth_support import AuthFixture
        self.fixture = AuthFixture()
        self.fixture.setUp()
        import app as app_module
        self.app = app_module
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [
            patch.object(app_module, "DIAGNOSIS_FILE", self.tmp / "diagnosis.json"),
            patch.object(app_module, "BUILD_FAILURE_FILE", self.tmp / "last_build_failure.json"),
            patch.object(app_module, "config_from_form", lambda: app_module.load_config()),
            patch.object(app_module, "save_config", lambda config: None),
            patch.object(app_module, "run_backend",
                         lambda: (False, "--- backend stdout ---\nDIAGNOSTIC STAGE_1 status=INFEASIBLE")),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.fixture.tearDown()
        shutil.rmtree(self.tmp)

    def build(self, findings):
        (self.tmp / "diagnosis.json").write_text(json.dumps(findings, ensure_ascii=False), encoding="utf-8")
        client = self.fixture.client
        self.fixture.register()
        with client.session_transaction() as sess:
            token = sess["csrf_token"]
        response = client.post("/generate", data={"csrf_token": token}, follow_redirects=True)
        return response.get_data(as_text=True)

    def test_blocking_reason_is_shown_in_plain_words(self):
        reason = "בשנה 2, סמסטר 2: שני קורסים ארוכים נכנסים רק ביום שישי."
        page = self.build([{"severity": "blocking", "code": "long_days", "he": reason, "en": "x",
                               "course_ids": []}])
        self.assertIn("build-failure", page)
        self.assertIn(reason, page)
        self.assertIn("לא ניתן לבנות מערכת שעות", page)   # definite headline, not "or out of time"
        self.assertIn('<pre dir="ltr">', page)               # raw log readable, left-to-right
        self.assertIn("status=INFEASIBLE", page)

    def test_without_diagnosis_the_generic_explanation_remains(self):
        page = self.build([])
        self.assertIn("לא נמצאה סיבה פשוטה", page)


if __name__ == "__main__":
    unittest.main()
