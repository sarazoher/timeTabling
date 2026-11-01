"""The timetable reads right to left in Hebrew, and time ranges always read
left to right ("17:00–22:00", never "22:00–17:00")."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from auth_support import AuthFixture  # noqa: E402

FRONTEND = Path(__file__).resolve().parent.parent / "timetable_frontend"
RANGE = '<bdi dir="ltr" class="time-range">'


class CalendarDirectionTests(AuthFixture, unittest.TestCase):
    def test_semester_grid_follows_the_page_direction(self):
        css = (FRONTEND / "static" / "calendar.css").read_text(encoding="utf-8")
        block = re.search(r"\n\.calendar-grid \{(.*?)\}", css, re.S).group(1)
        self.assertNotIn("direction", block)
        for selector in (r"\.dynamic-days-grid", r"\.course-card:hover \.course-hover-details"):
            block = re.search(r"\n" + selector + r" \{(.*?)\}", css, re.S).group(1)
            self.assertNotIn("direction: rtl", block, selector)
            self.assertNotIn("text-align: right", block, selector)

    def test_side_by_side_classes_shift_with_the_page_direction(self):
        page = (FRONTEND / "templates" / "calendar.html").read_text(encoding="utf-8")
        self.assertIn('document.documentElement.dir === "rtl" ? -1 : 1', page)

    def test_every_time_range_in_the_templates_is_isolated(self):
        for name in ("calendar.html", "lecturer_availability.html"):
            text = (FRONTEND / "templates" / name).read_text(encoding="utf-8")
            for line in text.splitlines():
                if re.search(r"\}\}–\{\{", line):
                    self.assertTrue(RANGE in line or "⁦" in line, (name, line.strip()[:80]))

    def test_rendered_pages_wrap_their_ranges(self):
        self.register()
        self.assertIn(RANGE, self.client.get("/calendar").get_data(as_text=True))


class TimetableLookTests(AuthFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.register()
        import json, shutil
        fixtures = Path(__file__).resolve().parent / "fixtures"
        workspace = self.workspace()
        shutil.copyfile(fixtures / "cs_courses.xlsx", workspace / "courses.xlsx")
        config = json.loads((fixtures / "cs_schedule_config.json").read_text(encoding="utf-8"))
        (workspace / "schedule_config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        event = {"course_id": 1, "name": "A", "lecturer": "L1", "year": 1, "course_category": "חובה",
                 "semester": 1, "event_type": "Lecture", "day": "Monday", "start": "17:00", "end": "18:30",
                 "cohort_label": "A"}
        (workspace / "timetable.json").write_text(json.dumps([event], ensure_ascii=False), encoding="utf-8")
        self.config = config

    def test_timetable_page_is_in_the_light_look(self):
        page = self.client.get("/calendar").get_data(as_text=True)
        self.assertIn('<body class="is-light-page wk-cal-wide">', page)
        self.assertIn('href="/static/work.css"', page)
        self.assertIn('wk-legend">', page.split('class="controls')[1].split("</section>")[0])
        self.assertNotIn("loadingOverlay", page)

    def test_a_years_semesters_sit_in_one_row(self):
        page = self.client.get("/calendar").get_data(as_text=True)
        per_year = re.findall(r'class="dynamic-year-semesters"\s+style="--semester-count: (\d+);"', page)
        self.assertTrue(per_year)
        self.assertEqual(set(per_year), {str(self.config["semesters_per_year"])})
        css = (FRONTEND / "static" / "work.css").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: repeat(var(--semester-count), minmax(0, 1fr))", css)

    def test_hours_outside_each_day_are_shaded(self):
        page = self.client.get("/calendar").get_data(as_text=True)
        open_days = [day for day, values in self.config["days"].items() if values.get("enabled")]
        shaded = re.findall(r'class="day-closed" aria-hidden="true" style="grid-column: (\d+);', page)
        self.assertTrue(shaded)
        self.assertTrue(set(int(c) for c in shaded) <= set(range(2, len(open_days) + 2)))


if __name__ == "__main__":
    unittest.main()
