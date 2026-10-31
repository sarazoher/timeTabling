"""Uploading a course file whose headers differ from the app's own names:
match them (synonyms / typos / values), confirm on a page, remember them."""
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "timetable_backend"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from column_mapping import EMPTY_CHOICE, apply_mapping, propose_for_frame  # noqa: E402

RENAMES = {"מס.שעור": "מספר קורס", "שם השיעור": "שם הקורס", "תיאור ד.קדם": "דרישת קדם",
           "תיאור ד.מקבילה": "דרישות מקבילות", "שנת לימודים": "שנה פדגוגית", "שם המרצה": "מרצה",
           "נ.זיכוי": 'נ"ז', 'ש"ש': "שעות שבועיות", "סוג שיעור": "סוג השעור"}


def proposal(result, canonical):
    return next(p for p in result.proposals if p.canonical == canonical)


class MatcherTests(unittest.TestCase):
    def test_department_names_typos_and_values(self):
        frame = pd.DataFrame({
            "מס.שעור": [6500032, 6500059], "שם השיעור": ["א", "ב"], "דירשות קדם": [None, "א"],
            "שנה פדגוגית": ["א", "ב"], 'נ"ז': [2, 3], "תחום": ["בחירה", "חובה"]})
        r = propose_for_frame(frame, "s")
        self.assertEqual((proposal(r, "תיאור ד.קדם").column, proposal(r, "תיאור ד.קדם").method),
                         ("דירשות קדם", "typo"))
        self.assertEqual(proposal(r, "שנת לימודים").method, "synonym")
        self.assertEqual(proposal(r, "נ.זיכוי").column, 'נ"ז')
        self.assertEqual((proposal(r, "קטגוריית קורס").column, proposal(r, "קטגוריית קורס").method),
                         ("תחום", "values"))
        self.assertEqual(r.missing_required, [])      # lecturer/hours/type/coreq have defaults

    def test_division_column_is_not_taken_for_category(self):
        frame = pd.DataFrame({"תחום": ["חטיבת מדעי היסוד", "מדעי החברה"], "שם הקורס": ["א", "ב"]})
        self.assertIsNone(proposal(propose_for_frame(frame, "s"), "קטגוריית קורס").column)

    def test_a_column_serves_one_field_only(self):
        frame = pd.DataFrame({"שנה": [1, 2], "שנת לימודים": [1, 2]})
        r = propose_for_frame(frame, "s")
        used = [p.column for p in r.proposals if p.column]
        self.assertEqual(len(used), len(set(used)))
        self.assertEqual(proposal(r, "שנת לימודים").column, "שנת לימודים")   # exact beats synonym

    def test_saved_name_is_used(self):
        frame = pd.DataFrame({"המרצה האחראי": ["x", "y"]})
        r = propose_for_frame(frame, "s", {"שם המרצה": ["המרצה האחראי"]})
        self.assertEqual((proposal(r, "שם המרצה").column, proposal(r, "שם המרצה").method),
                         ("המרצה האחראי", "saved"))

    def test_app_format_is_identity(self):
        frame = pd.read_excel(FIXTURES / "cs_courses.xlsx")
        self.assertTrue(propose_for_frame(frame, "גיליון2").is_identity)


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "src.xlsx"
        pd.DataFrame({"קוד": [1], "שם": ["A"], "תחום": ["x"]}).to_excel(self.src, sheet_name="S", index=False)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_renames_and_creates_empty_requirement_columns(self):
        out = self.tmp / "out.xlsx"
        apply_mapping(self.src, "S", {"מס.שעור": "קוד", "שם השיעור": "שם", "תיאור ד.קדם": EMPTY_CHOICE},
                      out, "גיליון2")
        frame = pd.read_excel(out, sheet_name="גיליון2")
        self.assertEqual(list(frame.columns)[:3], ["מס.שעור", "שם השיעור", "תיאור ד.קדם"])
        self.assertIn("תחום", frame.columns)        # unmapped column kept (data check lists it)

    def test_rejects_same_column_twice_and_empty_lecturer(self):
        with self.assertRaises(ValueError):
            apply_mapping(self.src, "S", {"מס.שעור": "קוד", "שם השיעור": "קוד"}, self.tmp / "o.xlsx", "g")
        with self.assertRaises(ValueError):
            apply_mapping(self.src, "S", {"שם המרצה": EMPTY_CHOICE}, self.tmp / "o.xlsx", "g")


PANEL = 'id="mappingTitle"'   # the column-check panel on the dashboard


class UploadFlowTests(unittest.TestCase):
    """Runs in the test account's own workspace (a temporary folder)."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "timetable_frontend"))
        from auth_support import AuthFixture
        import app as app_module
        self.app = app_module
        self.fixture = AuthFixture()
        self.fixture.setUp()
        self.fixture.register()
        self.client = self.fixture.client
        self.ws = self.fixture.workspace()
        shutil.copyfile(FIXTURES / "cs_courses.xlsx", self.ws / "courses.xlsx")
        config = json.loads((FIXTURES / "cs_schedule_config.json").read_text(encoding="utf-8"))
        (self.ws / "schedule_config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        frame = pd.read_excel(FIXTURES / "cs_courses.xlsx").rename(columns=RENAMES)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer) as writer:
            frame.to_excel(writer, sheet_name="קורסים", index=False)
        self.renamed = buffer.getvalue()

    def tearDown(self):
        self.fixture.tearDown()

    def token(self):
        with self.client.session_transaction() as sess:
            return sess["csrf_token"]

    def dashboard(self):
        return self.client.get("/").get_data(as_text=True)

    def upload(self, content):
        return self.client.post("/courses/upload", content_type="multipart/form-data",
                                data={"csrf_token": self.token(), "courses_excel": (io.BytesIO(content), "f.xlsx")})

    def confirm(self, **extra):
        pending = json.loads((self.ws / "_pending_mapping.json").read_text(encoding="utf-8"))
        form = {"csrf_token": self.token(), "action": "confirm"}
        for i, p in enumerate(pending["proposals"]):
            form[f"field_{i}"] = p["column"] or ""
        form.update(extra)
        return self.client.post("/courses/map", data=form, follow_redirects=True).get_data(as_text=True)

    def test_renamed_file_is_confirmed_loaded_and_remembered(self):
        self.upload(self.renamed)
        page = self.dashboard()
        self.assertIn(PANEL, page)                          # a panel over the dashboard
        self.assertIn("בדיקת שמות העמודות", page)
        self.assertIn("נמצאו 6 מתוך 6 שדות חובה", page)
        self.assertIn("שם מוכר", page)                      # how it was matched
        self.confirm(remember="on")
        frame = pd.read_excel(self.ws / "courses.xlsx", sheet_name="גיליון2")
        self.assertIn("תיאור ד.קדם", frame.columns)
        self.assertEqual(len(json.loads((self.ws / "courses.json").read_text(encoding="utf-8"))), 38)
        saved = json.loads((self.ws / "column_names.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["תיאור ד.קדם"], ["דרישת קדם"])     # one shared list, no department
        # Same file again: matched automatically, no confirmation panel.
        self.upload(self.renamed)
        page = self.dashboard()
        self.assertNotIn(PANEL, page)
        self.assertIn("הותאמו לפי שמות שאישרתם בעבר", page)

    def test_missing_required_field_blocks_and_keeps_current_courses(self):
        frame = pd.read_excel(FIXTURES / "cs_courses.xlsx").rename(columns=RENAMES).drop(columns=['נ"ז'])
        buffer = io.BytesIO()
        frame.to_excel(buffer, index=False)
        before = (self.ws / "courses.xlsx").read_bytes()
        self.upload(buffer.getvalue())
        page = self.confirm()
        self.assertIn("השדות האלה לא נמצאים בקובץ", page)
        self.assertIn("• נקודות זכות", page)
        self.assertEqual((self.ws / "courses.xlsx").read_bytes(), before)

    def test_missing_optional_fields_load_with_defaults(self):
        frame = pd.read_excel(FIXTURES / "cs_courses.xlsx").rename(columns=RENAMES).drop(
            columns=["מרצה", "שעות שבועיות", "סוג השעור", "דרישות מקבילות"])
        buffer = io.BytesIO()
        frame.to_excel(buffer, index=False)
        self.upload(buffer.getvalue())
        page = self.dashboard()
        self.assertIn("ברירת מחדל: מרצה נפרד לכל קורס", page)
        self.assertIn("נמצאו 6 מתוך 6 שדות חובה", page)
        self.confirm()
        stored = pd.read_excel(self.ws / "courses.xlsx", sheet_name="גיליון2")
        self.assertIn("שם המרצה", stored.columns)              # column added...
        self.assertTrue(stored["שם המרצה"].isna().all())       # ...but left empty, never filled
        catalog = json.loads((self.ws / "courses.json").read_text(encoding="utf-8"))
        self.assertEqual(len(catalog), 38)
        self.assertTrue(all(c["lecturer"] == f"מרצה {c['course_id']}" for c in catalog))
        codes = {f["code"] for f in json.loads((self.ws / "data_check.json").read_text(encoding="utf-8"))}
        self.assertTrue({"lecturer_default", "hours_default"} <= codes)
        self.assertNotIn("lesson_type_default", codes)          # removed on request

    def test_lecturer_column_under_another_name_is_never_replaced_by_default(self):
        frame = pd.read_excel(FIXTURES / "cs_courses.xlsx").rename(columns={"שם המרצה": "מרצה"})
        buffer = io.BytesIO()
        frame.to_excel(buffer, sheet_name="גיליון2", index=False)
        self.upload(buffer.getvalue())
        self.assertIn(PANEL, self.dashboard())   # asks, does not guess
        self.confirm()
        catalog = json.loads((self.ws / "courses.json").read_text(encoding="utf-8"))
        self.assertNotIn("מרצה 8500013", {c["lecturer"] for c in catalog})

    def test_other_sheets_are_named(self):
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer) as writer:
            pd.read_excel(FIXTURES / "cs_courses.xlsx").rename(columns=RENAMES).to_excel(
                writer, sheet_name="מחלקה א", index=False)
            pd.DataFrame({"x": [1]}).to_excel(writer, sheet_name="מחלקה ב", index=False)
        self.upload(buffer.getvalue())
        self.assertIn("גיליונות נוספים בקובץ לא ייטענו", self.dashboard())

    def test_same_column_twice_is_rejected(self):
        self.upload(self.renamed)
        pending = json.loads((self.ws / "_pending_mapping.json").read_text(encoding="utf-8"))
        index = next(i for i, p in enumerate(pending["proposals"]) if p["canonical"] == "שם המרצה")
        page = self.confirm(**{f"field_{index}": "שם הקורס"})
        self.assertIn("אותה עמודה נבחרה ליותר משדה אחד", page)

    def test_cancel_leaves_everything_as_it_was(self):
        before = (self.ws / "courses.xlsx").read_bytes()
        self.upload(self.renamed)
        self.client.post("/courses/map", data={"csrf_token": self.token(), "action": "cancel"})
        self.assertFalse((self.ws / "_pending_upload.xlsx").exists())
        self.assertNotIn(PANEL, self.dashboard())
        self.assertEqual((self.ws / "courses.xlsx").read_bytes(), before)

    def test_app_format_still_uploads_directly(self):
        self.upload((FIXTURES / "cs_courses.xlsx").read_bytes())
        self.assertNotIn(PANEL, self.dashboard())

    def test_old_page_address_leads_to_the_panel(self):
        self.upload(self.renamed)
        response = self.client.get("/courses/map")
        self.assertEqual(response.status_code, 302)
        self.assertIn(PANEL, self.dashboard())


if __name__ == "__main__":
    unittest.main()
