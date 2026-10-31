"""Users never see solver jargon or department wording, in either language."""
import html
import io
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from auth_support import AuthFixture, app  # noqa: E402

FIXTURE_CS = Path(__file__).resolve().parent / "fixtures" / "cs_courses.xlsx"
BANNED = re.compile(r"פותר|solver|DEBUG JSON|מחלק|department|infeasib|CP-SAT", re.I)
PAGES = ["/", "/setup", "/data", "/rules", "/calendar", "/lecturers"]


def visible_text(page: str) -> str:
    page = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S | re.I)
    page = re.sub(r"<!--.*?-->", " ", page, flags=re.S)
    attributes = " ".join(re.findall(r'(?:title|aria-label|placeholder|alt)="([^"]*)"', page))
    return html.unescape(re.sub(r"<[^>]+>", " ", page) + " " + attributes)


class PlainWordingTests(AuthFixture, unittest.TestCase):
    def check(self, client, urls):
        for lang in ("he", "en"):
            client.get(f"/language/{lang}")
            for url in urls:
                text = visible_text(client.get(url).get_data(as_text=True))
                found = sorted({m.group(0) for m in BANNED.finditer(text)})
                self.assertEqual(found, [], f"{lang} {url}")

    def test_signed_out_pages(self):
        self.check(self.client, ["/login", "/register"])

    def test_signed_in_pages_with_a_course_file(self):
        self.register()
        with self.client.session_transaction() as sess:
            token = sess["csrf_token"]
        self.client.post("/courses/upload", content_type="multipart/form-data", data={
            "csrf_token": token, "courses_excel": (io.BytesIO(FIXTURE_CS.read_bytes()), "cs.xlsx")})
        self.check(self.client, PAGES)

    def test_the_year_moves_panel(self):
        self.register()
        with self.client.session_transaction() as sess:
            token = sess["csrf_token"]
        self.client.post("/setup/sample", data={"csrf_token": token})
        (self.workspace() / "year_moves.json").write_text(json.dumps({"mode": "suggest", "moves": [
            {"course_id": 4, "name": "D", "from_year": 2, "to_year": 3,
             "reason_he": "שרשרת ארוכה", "reason_en": "long chain"}]}), encoding="utf-8")
        page = self.client.get("/data").get_data(as_text=True)
        self.assertIn('id="yearReviewTitle"', page)
        self.check(self.client, ["/", "/data", "/rules", "/setup"])


if __name__ == "__main__":
    unittest.main()
