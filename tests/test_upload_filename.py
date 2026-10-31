import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from auth_support import AuthFixture, app  # noqa: E402

FIXTURE_CS = Path(__file__).resolve().parent / "fixtures" / "cs_courses.xlsx"
BACKEND = Path(__file__).resolve().parents[1] / "timetable_backend"


class UploadFilenameTests(AuthFixture, unittest.TestCase):
    """Hebrew file names must not be rejected by the extension check."""

    # Runs in the test account's own workspace (a temporary folder), which
    # starts with the CS sample as its current course list.
    def setUp(self):
        super().setUp()
        self.register()
        self.ws = self.workspace()
        import shutil
        shutil.copyfile(FIXTURE_CS, self.ws / "courses.xlsx")

    def _upload(self, filename, content):
        self.register()
        with self.client.session_transaction() as sess:
            token = sess["csrf_token"]
        return self.client.post(
            "/courses/upload",
            data={"csrf_token": token,
                  "courses_excel": (io.BytesIO(content), filename)},
            content_type="multipart/form-data",
            follow_redirects=True,
        )

    def test_hebrew_filename_is_accepted(self):
        workbook = (FIXTURE_CS).read_bytes()
        page = self._upload("קורסים ניהול מצבי חירום.xlsx", workbook).get_data(as_text=True)
        self.assertNotIn("Please upload an .xlsx", page)
        self.assertNotIn("xlsx או xlsm", page)

    def _workbook_with_sheet_title(self, title):
        from openpyxl import load_workbook
        wb = load_workbook(FIXTURE_CS)
        wb["גיליון2"].title = title
        buffer = io.BytesIO()
        wb.save(buffer)
        return buffer.getvalue()

    def test_other_sheet_name_is_accepted_and_normalized(self):
        from openpyxl import load_workbook
        page = self._upload("courses.xlsx", self._workbook_with_sheet_title("Sheet1")).get_data(as_text=True)
        self.assertNotIn("not found", page)
        self.assertNotIn("No worksheet", page)
        self.assertIn("גיליון2", load_workbook(self.ws / "courses.xlsx").sheetnames)

    def test_sheet_without_course_columns_opens_mapping_panel(self):
        # A department file with its own headers is no longer rejected: the
        # app proposes a column mapping, and confirming lists each field the
        # file really lacks on its own line.
        from openpyxl import Workbook
        wb = Workbook()
        wb.active.title = "קורסים בחוג"
        wb.active.append(["תחום", "מס.שעור", "שנה פדגוגית", "שם השיעור", 'נ"ז'])
        wb.active.append(["חובה", 6500001, "א", "קורס", 2])
        buffer = io.BytesIO()
        wb.save(buffer)
        before = (self.ws / "courses.xlsx").read_bytes()
        self._upload_raw("raw.xlsx", buffer.getvalue())
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="mappingTitle"', page)            # panel over the dashboard
        self.assertIn("נמצאו 4 מתוך 6 שדות חובה", page)
        with self.client.session_transaction() as sess:
            token = sess["csrf_token"]
        form = {"csrf_token": token, "action": "confirm",
                "field_0": "מס.שעור", "field_1": "שם השיעור", "field_4": "שנה פדגוגית", "field_6": 'נ"ז'}
        page = self.client.post("/courses/map", data=form, follow_redirects=True).get_data(as_text=True)
        self.assertIn("השדות האלה לא נמצאים בקובץ", page)
        self.assertIn("• דרישות קדם", page)                   # required fields still missing,
        self.assertIn("• קטגוריית קורס", page)                # each on its own line
        self.assertEqual((self.ws / "courses.xlsx").read_bytes(), before)   # nothing loaded
        # Saying "no prerequisites column" and picking the category column
        # loads it, with defaults for the rest.
        form["field_2"] = "__empty__"
        form["field_9"] = "תחום"
        self.client.post("/courses/map", data=form)
        self.assertNotEqual((self.ws / "courses.xlsx").read_bytes(), before)

    def _upload_raw(self, filename, content):
        self.register()
        with self.client.session_transaction() as sess:
            token = sess["csrf_token"]
        return self.client.post("/courses/upload", content_type="multipart/form-data",
                                data={"csrf_token": token, "courses_excel": (io.BytesIO(content), filename)})

    def test_non_excel_is_still_rejected(self):
        page = self._upload("קורסים.csv", b"a,b\n").get_data(as_text=True)
        self.assertTrue("Please upload an .xlsx" in page or "xlsm" in page)


    def test_upload_warns_about_malformed_cell(self):
        from openpyxl import load_workbook
        wb = load_workbook(FIXTURE_CS)
        ws = wb["גיליון2"]
        header = [cell.value for cell in ws[1]]
        ws.cell(row=2, column=header.index("תיאור ד.קדם") + 1, value="(מתמטיקה בדידה / חדו\"א 1")
        buffer = io.BytesIO()
        wb.save(buffer)
        self.register()
        with self.client.session_transaction() as sess:
            token = sess["csrf_token"]
        page = self.client.post("/courses/upload",
                                data={"csrf_token": token, "courses_excel": (io.BytesIO(buffer.getvalue()), "c.xlsx")},
                                content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
        self.assertIn('class="flash warning"', page)      # short summary after upload
        self.assertIn("חסר סוגר", self.client.get("/data").get_data(as_text=True))   # the cell itself, in the data check


if __name__ == "__main__":
    unittest.main()

