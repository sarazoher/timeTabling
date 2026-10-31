"""The TimeTabling name, logo and browser-tab icon appear on every page."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from auth_support import AuthFixture  # noqa: E402

STATIC = Path(__file__).resolve().parents[1] / "timetable_frontend" / "static"
ICON_LINKS = ('href="/static/favicon.ico"', 'href="/static/favicon.svg"', 'href="/static/apple-touch-icon.png"')


class BrandTests(AuthFixture, unittest.TestCase):
    def test_icon_files_exist(self):
        for name in ("logo.svg", "favicon.svg", "favicon.ico", "apple-touch-icon.png"):
            self.assertTrue((STATIC / name).stat().st_size > 100, name)

    def test_signed_out_pages_have_the_icon(self):
        for url in ("/login", "/register", "/forgot-password"):
            page = self.client.get(url).get_data(as_text=True)
            for link in ICON_LINKS:
                self.assertIn(link, page, (url, link))

    def test_signed_in_pages_have_the_icon_and_the_logo(self):
        self.register()
        for url in ("/", "/setup", "/data", "/rules", "/calendar", "/lecturers"):
            page = self.client.get(url).get_data(as_text=True)
            for link in ICON_LINKS:
                self.assertIn(link, page, (url, link))
            self.assertIn('<img class="app-logo" src="/static/logo.svg" alt=""', page, url)
            self.assertIn('<span lang="en" dir="ltr">TimeTabling</span>', page, url)

    def test_root_favicon_without_signing_in(self):
        response = self.client.get("/favicon.ico")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[:4], b"\x00\x00\x01\x00")


if __name__ == "__main__":
    unittest.main()
