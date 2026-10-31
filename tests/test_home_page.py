"""Home in the new look: the result (checked against the timetable), where
things stand, the rules the app keeps, and one main button that follows the
account's state: new, file loaded, built, or rebuilt from a new file."""
import json
import os
import re
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from auth_support import AuthFixture, app  # noqa: E402


def main_button(page):
    match = re.search(r'<(?:a|button) class="home-main-button[^"]*"(?: href="([^"]+)")?[^>]*>([^<]+)</(?:a|button)>', page)
    return match.groups()


def done_rows(page):
    return len(re.findall(r'<li class="is-done">', page))


def event(**fields):
    base = {"course_id": 1, "name": "A", "lecturer": "L1", "year": 1, "course_category": "חובה",
            "semester": 1, "event_type": "Lecture", "day": "Monday", "start": "17:00", "end": "18:30",
            "cohort_label": "A"}
    base.update(fields)
    return base


class HomePageTests(AuthFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.register()

    def home(self):
        return self.client.get("/").get_data(as_text=True)

    def token(self):
        with self.client.session_transaction() as sess:
            return sess["csrf_token"]

    def load_sample(self):
        self.client.post("/setup/sample", data={"csrf_token": self.token()})

    def write_timetable(self, events):
        path = self.workspace() / "timetable.json"
        path.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
        later = time.time() + 5   # built after the file was loaded
        os.utime(path, (later, later))

    def test_new_account_shows_the_upload(self):
        page = self.home()
        self.assertEqual(main_button(page), ("/setup", "הגדרה שלב אחר שלב"))
        self.assertIn('id="homeUpload"', page)
        self.assertIn('name="courses_excel"', page)
        self.assertIn('<input type="hidden" name="return_to" value="setup">', page)
        self.assertIn('action="/setup/sample"', page)
        self.assertEqual(done_rows(page), 0)
        self.assertIn("עדיין לא הועלה", page)
        self.assertNotIn('action="/rebuild"', page)
        self.assertNotIn("hm-ledger", page)

    def test_loaded_file_continues_setup_at_the_settings(self):
        self.load_sample()
        page = self.home()
        self.assertEqual(main_button(page), ("/setup#gsStep2", "המשך להגדרה"))
        self.assertEqual(done_rows(page), 2)
        self.assertIn("<bdi>sample_courses.xlsx</bdi> · 38 קורסים", page)
        self.assertIn("שני, חמישי, שישי", page)
        self.assertIn("עדיין לא נבנתה", page)
        self.assertNotIn('id="homeUpload"', page)

    def test_built_timetable_shows_the_result_and_the_checks(self):
        self.load_sample()
        self.write_timetable([
            event(),
            event(event_type="Tutorial", start="18:30", end="19:30"),
            event(course_id=2, name="B", lecturer="L2", day="Thursday", start="19:00", end="21:00", course_category="בחירה"),
        ])
        page = self.home()
        self.assertEqual(main_button(page), ("/calendar", "פתיחת מערכת השעות"))
        self.assertEqual(done_rows(page), 3)
        self.assertIn('action="/rebuild"', page)
        self.assertIn('href="/export/timetable"', page)
        self.assertIn('מערכת השעות של <bdi class="hm-em">מדעי המחשב (דוגמה)</bdi> מוכנה.', page)
        self.assertIn('<span class="hm-n">2</span> קורסים ו־<span class="hm-n">3</span> מפגשים שבועיים', page)
        self.assertEqual(re.findall(r'<span class="hm-chip"><b>(\d+)</b>', page), ["0", "0", "0", "0"])
        self.assertEqual(len(re.findall(r'<span class="slot is-', page)), 3)

    def test_problems_in_the_timetable_are_counted_not_hidden(self):
        self.load_sample()
        self.write_timetable([
            event(),
            event(course_id=2, name="B", start="18:00", end="19:00"),        # same lecturer, overlaps, required
            event(course_id=3, name="C", lecturer="L3", day="Tuesday"),      # Tuesday is not a teaching day
            event(course_id=4, name="D", lecturer="L4", semester=12, day="Friday", start="09:00", end="10:00"),
        ])
        page = self.home()
        counts = dict(zip(["outside", "lecturers", "required", "pushed"],
                          re.findall(r'<span class="hm-chip(?: is-warn)?"><b>(\d+)</b>', page)))
        self.assertEqual(counts, {"outside": "1", "lecturers": "1", "required": "1", "pushed": "1"})
        self.assertEqual(page.count('class="hm-chip is-warn"'), 4)

    def test_a_new_file_marks_the_timetable_as_outdated(self):
        self.load_sample()
        self.write_timetable([event()])
        later = time.time() + 60
        os.utime(self.workspace() / "courses.xlsx", (later, later))
        page = self.home()
        self.assertIn("הקובץ הוחלף. מערכת השעות הנוכחית נבנתה מהקובץ הקודם.", page)
        self.assertEqual(main_button(page), (None, "בנייה עם הקובץ החדש"))
        self.assertIn('href="/calendar">פתיחת המערכת הקודמת</a>', page)
        self.assertEqual(page.count('<li class="is-warn">'), 2)
        self.assertNotIn("hm-ledger", page)

    def test_rules_show_as_three_two_and_two_two(self):
        rows = re.findall(r'<div class="hm-rrow" data-from="(start|end)">(.*?)</div>\s*</div>?', self.home(), re.S)
        page = self.home()
        counts = [len(re.findall(r'class="hm-rule ', block)) for block in
                  re.split(r'<div class="hm-rrow"', page)[1:]]
        self.assertEqual(counts, [3, 2, 2, 2])
        self.assertEqual(re.findall(r'class="hm-rrow" data-from="(\w+)"', page), ["start", "end", "start", "end"])
        self.assertTrue(rows)

    def test_greets_by_name_in_both_languages(self):
        self.assertRegex(self.home(), r'<p class="hm-hello">שלום, <bdi>[^<]+</bdi></p>')
        self.client.get("/language/en")
        page = self.home()
        self.assertRegex(page, r'<p class="hm-hello">Hello, <bdi>[^<]+</bdi></p>')
        self.assertIn("What the app keeps for you", page)

    def test_assistant_is_the_shared_bubble_not_a_section(self):
        page = self.home()
        self.assertIn('class="as-bubble"', page)
        self.assertIn('id="ai-assistant"', page)               # the drawer
        self.assertIn("העוזר לא מחובר במחשב הזה", page)
        self.assertNotIn("hm-bubble", page)
        self.assertNotIn("hm-assistant", page)
        self.assertNotIn("לדוגמה: פרופ׳ סלע", page)

if __name__ == "__main__":
    unittest.main()
