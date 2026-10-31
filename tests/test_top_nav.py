"""The top menu: one shared menu on every signed-in page, replacing the
per-page language buttons, back links and account bar."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from auth_support import AuthFixture  # noqa: E402

PAGES = {"/": "home", "/calendar": "timetable", "/days": None, "/lecturers": None}


def nav_of(page):
    match = re.search(r'<nav class="app-nav".*?</nav>', page, re.S)
    return match.group(0) if match else ""


class TopNavTests(AuthFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.register()

    def get(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.get_data(as_text=True)

    def test_every_page_has_one_menu_and_a_skip_link(self):
        for url in PAGES:
            page = self.get(url)
            self.assertEqual(page.count('class="app-topbar"'), 1, url)
            self.assertIn('href="#main-content"', page, url)
            self.assertIn('id="main-content"', page, url)

    def test_current_page_is_marked(self):
        for url, active in PAGES.items():
            nav = nav_of(self.get(url))
            current = re.findall(r'<a class="app-nav-item" href="([^"]+)" aria-current="page"', nav)
            if active is None:
                self.assertEqual(current, [], url)
            else:
                self.assertEqual(current, ["/" if active == "home" else "/calendar"], url)

    def test_data_and_rules_are_greyed_with_a_reason(self):
        nav = nav_of(self.get("/"))
        for label in ("נתונים", "כללים"):
            item = re.search(r'<span class="app-nav-item is-disabled"[^>]*>' + label, nav)
            self.assertIsNotNone(item, label)
            self.assertIn('aria-disabled="true"', item.group(0))
        self.assertEqual(nav.count('role="tooltip"'), 2)

    def test_old_navigation_is_gone(self):
        for url in PAGES:
            page = self.get(url)
            for old in ('class="account-bar"', 'class="lang-switcher', 'class="back-link"',
                        "לוח ניהול</a>", "Step 2 of 2", "שלב 2 מתוך 2", "צפייה במערכת"):
                self.assertNotIn(old, page, (url, old))

    def test_language_switch_points_to_the_other_language(self):
        self.assertIn('href="/language/en"', self.get("/"))
        self.client.get("/language/en")
        page = self.get("/")
        self.assertIn('href="/language/he"', page)
        self.assertIn(">Home<", page)

    def test_sign_out_is_in_the_account_menu(self):
        page = self.get("/")
        menu = re.search(r'<details class="app-account">.*?</details>', page, re.S).group(0)
        self.assertIn('action="/logout"', menu)


if __name__ == "__main__":
    unittest.main()
