"""Home: where things stand (file, settings, timetable) and one main button
whose label and target follow that state."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from auth_support import AuthFixture  # noqa: E402


def main_button(page):
    return re.search(r'<a class="home-main-button" href="([^"]+)">([^<]+)</a>', page).groups()


def done_rows(page):
    return len(re.findall(r'<li class="is-done">', page))


class HomePageTests(AuthFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.register()

    def home(self):
        return self.client.get("/").get_data(as_text=True)

    def load_sample(self):
        with self.client.session_transaction() as sess:
            token = sess["csrf_token"]
        self.client.post("/setup/sample", data={"csrf_token": token})

    def test_new_account_starts_setup(self):
        page = self.home()
        self.assertEqual(main_button(page), ("/setup", "התחלה"))
        self.assertEqual(done_rows(page), 0)
        self.assertIn("עדיין לא הועלה", page)
        self.assertNotIn('action="/rebuild"', page)

    def test_loaded_file_continues_setup_at_the_settings(self):
        self.load_sample()
        page = self.home()
        self.assertEqual(main_button(page), ("/setup#gsStep2", "המשך להגדרה"))
        self.assertEqual(done_rows(page), 2)
        self.assertIn("<bdi>sample_courses.xlsx</bdi> · 38 קורסים", page)
        self.assertIn("שני, חמישי, שישי", page)
        self.assertIn("עדיין לא נבנתה", page)

    def test_built_timetable_opens_it(self):
        self.load_sample()
        (self.workspace() / "timetable.json").write_text("[]", encoding="utf-8")
        page = self.home()
        self.assertEqual(main_button(page), ("/calendar", "פתיחת מערכת השעות"))
        self.assertEqual(done_rows(page), 3)
        self.assertIn('action="/rebuild"', page)
        self.assertIn('href="/export/timetable"', page)
        self.assertIn("מערכת השעות של <bdi>מדעי המחשב (דוגמה)</bdi> מוכנה.", page)

    def test_greets_by_name_in_both_languages(self):
        self.assertRegex(self.home(), r'<h1 class="home-title">שלום, <bdi>[^<]+</bdi></h1>')
        self.client.get("/language/en")
        page = self.home()
        self.assertRegex(page, r'<h1 class="home-title">Hello, <bdi>[^<]+</bdi></h1>')
        self.assertEqual(main_button(page), ("/setup", "Start"))

    def test_assistant_stays_on_home(self):
        self.assertIn('id="ai-assistant"', self.home())


if __name__ == "__main__":
    unittest.main()
