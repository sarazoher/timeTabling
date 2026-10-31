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
        self.assertTrue(response.headers["Location"].endswith("/rules#ai-assistant"))
        self.assertEqual(self.config()["ai_constraints"], [])

    def test_lecturer_hours_are_summarised_on_the_rules_page(self):
        self.load_sample()
        config = self.config()
        config["lecturer_availability"] = {"ד\"ר נור איגור": {"Monday": {"enabled": True, "start": "17:00", "end": "20:00"}}}
        (self.workspace() / "schedule_config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        page = self.client.get("/rules").get_data(as_text=True)
        self.assertIn("נור איגור", page)
        self.assertIn('<bdi dir="ltr" class="time-range">17:00–20:00</bdi>', page)


if __name__ == "__main__":
    unittest.main()
