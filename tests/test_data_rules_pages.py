"""The Data and Rules pages: what is on them, and that their forms come back to them."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from auth_support import AuthFixture, app  # noqa: E402


class DataRulesPagesTests(AuthFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.register()

    def token(self):
        with self.client.session_transaction() as sess:
            return sess["csrf_token"]

    def load_sample(self):
        self.client.post("/setup/sample", data={"csrf_token": self.token()})

    def config(self):
        return json.loads((self.workspace() / "schedule_config.json").read_text(encoding="utf-8"))

    def test_data_page_without_a_file_points_to_setup(self):
        page = self.client.get("/data").get_data(as_text=True)
        self.assertIn('href="/setup"', page)
        self.assertNotIn('id="courseTable"', page)

    def test_data_page_order_puts_the_course_list_last(self):
        self.load_sample()
        page = self.client.get("/data").get_data(as_text=True)
        order = [page.index(m) for m in ('id="replaceForm"', 'id="settings"', 'id="check"',
                                          'id="manual-course"', 'id="courseTable"')]
        self.assertEqual(order, sorted(order))
        self.assertIn("38", page)
        self.assertIn("שמירה ובנייה מחדש", page)
        self.assertIn('action="/rebuild"', page)

    def test_saving_settings_on_the_data_page_returns_there(self):
        self.load_sample()
        config = self.config()
        form = {"csrf_token": self.token(), "return_to": "data", "degree_years": "3", "semesters_per_year": "2",
                "degree_years_auto_shown": "1", "degree_years_auto": "on", "year_mode": "suggest",
                "program_name": "מדעי המחשב"}
        for day, values in config["days"].items():
            if values["enabled"]:
                form[f"enabled_{day}"] = "on"
            form[f"start_{day}"], form[f"end_{day}"] = values["start"], values["end"]
        response = self.client.post("/settings", data=form)
        self.assertTrue(response.headers["Location"].endswith("/data#settings"))
        self.assertEqual(self.config()["semesters_per_year"], 2)

    def test_rebuild_uses_the_saved_settings_and_returns_to_the_page(self):
        self.load_sample()
        with patch.object(app, "run_backend", return_value=(False, "No feasible timetable")):
            response = self.client.post("/rebuild", data={"csrf_token": self.token(), "return_to": "rules"})
        self.assertTrue(response.headers["Location"].endswith("/rules"))

    def test_rebuild_without_a_file_goes_to_setup(self):
        response = self.client.post("/rebuild", data={"csrf_token": self.token()})
        self.assertTrue(response.headers["Location"].endswith("/setup"))

    def test_rules_page_lists_own_rules_and_built_in_rules(self):
        self.load_sample()
        config = self.config()
        config["ai_constraints"] = [{"id": "r1", "description": "סלע לא מלמד בשלישי", "hard": False, "kind": "x"}]
        (self.workspace() / "schedule_config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        page = self.client.get("/rules").get_data(as_text=True)
        self.assertIn("סלע לא מלמד בשלישי", page)
        self.assertIn("מועדף", page)
        self.assertIn("window.confirm", page)                    # "remove all" asks first
        self.assertIn("קורס קדם לפני הקורס שתלוי בו", page)
        response = self.client.post("/assistant/remove/r1", data={"csrf_token": self.token(), "return_to": "rules"})
        self.assertTrue(response.headers["Location"].endswith("/rules#ownRules"))
        self.assertEqual(self.config()["ai_constraints"], [])

    def test_lecturer_hours_card_and_data_check_line_on_the_data_page(self):
        self.load_sample()
        page = self.client.get("/data").get_data(as_text=True)
        self.assertIn("כל מרצה זמין בכל שעות הלימוד", page)
        self.assertIn('href="/lecturers"', page)
        self.assertIn('class="wk-status dr-box" href="#check"', page)
        self.assertNotIn('id="drCheck"', page)            # the old third card is gone
        order = [page.index(m) for m in ('id="drLecturers"', 'class="wk-status', 'id="settings"', 'id="check"')]
        self.assertEqual(order, sorted(order))
        config = self.config()
        config["lecturer_availability"] = {"ד\"ר נור איגור": {"Monday": {"enabled": True, "start": "17:00", "end": "20:00"}}}
        (self.workspace() / "schedule_config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        page = self.client.get("/data").get_data(as_text=True)
        self.assertIn("נור איגור", page)
        self.assertIn('<bdi dir="ltr" class="time-range">17:00–20:00</bdi>', page)

    def test_data_page_is_in_the_light_look(self):
        self.load_sample()
        page = self.client.get("/data").get_data(as_text=True)
        self.assertIn('<body class="is-light-page">', page)
        self.assertIn('class="tt-sky is-light"', page)
        self.assertIn('href="/static/work.css"', page)

    def test_rules_page_is_light_and_leaves_lecturer_hours_to_data(self):
        self.load_sample()
        config = self.config()
        config["lecturer_availability"] = {"ד\"ר נור איגור": {"Monday": {"enabled": True, "start": "17:00", "end": "20:00"}}}
        (self.workspace() / "schedule_config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        page = self.client.get("/rules").get_data(as_text=True)
        self.assertIn('<body class="is-light-page">', page)
        self.assertNotIn("נור איגור", page)
        self.assertIn('<a href="/data">הנתונים</a>', page)
        self.assertIn('data-assistant-open aria-controls="ai-assistant"', page)
        groups = page.split('class="dr-box wk-group ')[1:]
        self.assertEqual([g.split('"')[0] for g in groups], ["is-must", "is-pref"])
        self.assertIn("קורס קדם לפני הקורס שתלוי בו", groups[0])
        self.assertIn("עומס מאוזן", groups[1])
        self.assertIn("עדיין אין כללים משלכם", page)

    def test_timetable_page_links_to_data(self):
        page = self.client.get("/calendar").get_data(as_text=True)
        self.assertIn('href="/data"', page.split('class="hero-actions"')[1] if 'class="hero-actions"' in page else page)
        self.assertIn("שינוי נתונים או הגדרות", page)
        self.assertIn('href="/setup"', page)                 # no timetable yet: the way to build one


if __name__ == "__main__":
    unittest.main()
