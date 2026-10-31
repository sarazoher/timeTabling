"""The sign-in and sign-up pages show what the app does, in both languages,
while Home after sign-in stays a status page without it."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from auth_support import AuthFixture  # noqa: E402


class SignInShowcaseTests(AuthFixture, unittest.TestCase):
    def page(self, url):
        return self.client.get(url).get_data(as_text=True)

    def test_four_features_and_a_sample_week_in_hebrew(self):
        for url in ("/login", "/register"):
            page = self.page(url)
            self.assertIn("המערכת הבאה שלכם מתחילה כאן.", page)
            self.assertEqual(len(re.findall(r'<span class="auth-feat-icon"', page)), 4, url)
            for title in ("קובץ אחד נכנס, מערכת שלמה יוצאת", "הכללים נשמרים בשבילכם",
                          "משנים במילים פשוטות", "מורידים ומשתפים"):
                self.assertIn(title, page)
            self.assertEqual(len(re.findall(r'class="auth-week-block slot is-', page)), 6)
            self.assertIn('class="auth-week tt-week" aria-hidden="true"', page)
            self.assertIn('href="/static/world.css"', page)
            self.assertIn('class="tt-sky is-dark"', page)
            self.assertNotIn("auth-eyebrow", page)
            self.assertNotIn('class="auth-features"', page)

    def test_english(self):
        self.client.get("/language/en")
        page = self.page("/login")
        self.assertIn("Your next timetable starts here.", page)
        self.assertIn("A whole degree’s timetable, from one Excel file.", page)
        self.assertIn("No lecturer booked twice at once".lower(), page.lower())
        self.assertIn('<a href="/language/en" lang="en" aria-current="true">', page)

    def test_sign_in_form_still_works_beside_it(self):
        page = self.page("/login")
        self.assertIn('action="/login"', page)
        self.assertLess(page.index('class="auth-intro"'), page.index('class="auth-panel"'))

    def test_home_after_sign_in_has_no_showcase(self):
        self.register()
        self.assertNotIn("auth-feat", self.page("/"))


if __name__ == "__main__":
    unittest.main()
