"""There are no departments: a course file is read by its own columns only.
(Department profiles, their selector and their rules were removed; the CS
file now carries its own category column, nursing its current course names.)"""
import importlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "timetable_backend"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))


class ReaderTests(unittest.TestCase):
    def test_no_department_module(self):
        with self.assertRaises(ImportError):
            importlib.import_module("departments")
        self.assertFalse((BACKEND / "departments").exists())

    def test_cs_file_reads_its_own_categories(self):
        from excel_reader import load_courses
        issues = []
        courses = {c.course_id: c for c in load_courses(FIXTURES / "cs_courses.xlsx", issues)}
        self.assertEqual(len(courses), 38)
        counts = {}
        for c in courses.values():
            counts[c.course_category] = counts.get(c.course_category, 0) + 1
        self.assertEqual(counts, {"חובה": 21, "בחירה": 7, "סמינר": 5, "כללי": 3, "פרויקט": 2})
        self.assertEqual(courses[8500015].course_category, "חובה")       # "פרויקט" in its name, column says required
        self.assertEqual(issues, [])                                      # nothing guessed or skipped

    def test_prerequisite_names_are_matched_exactly(self):
        from excel_reader import load_courses
        from scheduler import _requirement_set
        courses = load_courses(FIXTURES / "cs_courses.xlsx")
        self.assertEqual(_requirement_set(courses, {}).unresolved, [])
        # An old name is no longer translated: it is reported, not guessed.
        renamed = [c for c in courses]
        algorithms = next(c for c in renamed if c.name == "אלגוריתמים")
        algorithms.prerequisites = "מבני נתונים + לינארית 1"
        unresolved = _requirement_set(renamed, {}).unresolved
        self.assertEqual([u["unresolved_name"] for u in unresolved], ["לינארית 1"])


class ColumnNamesTests(unittest.TestCase):
    def test_confirmed_names_are_one_shared_list(self):
        from column_mapping import EMPTY_CHOICE, load_saved_column_names, remember_column_names
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "column_names.json"
            self.assertEqual(load_saved_column_names(path), {})
            remember_column_names({"תיאור ד.קדם": "דרישת קדם", "מס.שעור": "מס.שעור",
                                   "תיאור ד.מקבילה": EMPTY_CHOICE}, path)
            remember_column_names({"תיאור ד.קדם": "דרישת קדם"}, path)        # no duplicates
            self.assertEqual(load_saved_column_names(path), {"תיאור ד.קדם": ["דרישת קדם"]})


class DashboardTests(unittest.TestCase):
    def setUp(self):
        from auth_support import AuthFixture, app
        self.app = app
        self.fixture = AuthFixture()
        self.fixture.setUp()
        self.client = self.fixture.client
        self.fixture.register()
        self.ws = self.fixture.workspace()
        shutil.copyfile(FIXTURES / "cs_courses.xlsx", self.ws / "courses.xlsx")
        # An old saved configuration may still name a department: it is ignored.
        config = json.loads((FIXTURES / "cs_schedule_config.json").read_text(encoding="utf-8"))
        config["department"] = "cs"
        (self.ws / "schedule_config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.fixture.tearDown()

    def test_dashboard_has_no_department(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertNotIn('id="department-rules"', page)
        self.assertNotIn("/department", page)
        self.assertIn('id="ai-assistant"', page)

    def test_department_route_is_gone(self):
        with self.client.session_transaction() as sess:
            token = sess["csrf_token"]
        self.assertEqual(self.client.post("/department", data={"csrf_token": token, "department": "cs"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
