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


if __name__ == "__main__":
    unittest.main()
