"""The assistant is one bubble and drawer on every signed-in page. Sending a
message comes back to the page it was sent from, with the drawer open."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from auth_support import AuthFixture, app  # noqa: E402


class AssistantDrawerTests(AuthFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.register()
        self.client.post("/setup/sample", data={"csrf_token": self.token()})

    def token(self):
        with self.client.session_transaction() as sess:
            return sess["csrf_token"]

    def config(self):
        return json.loads((self.workspace() / "schedule_config.json").read_text(encoding="utf-8"))

    def test_every_page_has_the_bubble_and_no_menu_button(self):
        for path in ("/", "/data", "/rules", "/calendar", "/setup", "/lecturers"):
            page = self.client.get(path).get_data(as_text=True)
            self.assertEqual(page.count('class="as-bubble"'), 1, path)
            self.assertIn('<aside class="as-drawer" id="ai-assistant"', page, path)
            self.assertNotIn("app-assistant", page, path)
            self.assertNotIn("לדוגמה", page.split('id="ai-assistant"')[1].split("</aside>")[0], path)

    def test_signed_out_pages_have_no_assistant(self):
        self.client.post("/logout", data={"csrf_token": self.token()})
        page = self.client.get("/login").get_data(as_text=True)
        self.assertNotIn("as-bubble", page)

    def test_without_a_key_the_drawer_opens_but_cannot_send(self):
        with patch.object(app, "claude_is_configured", return_value=False):
            page = self.client.get("/data").get_data(as_text=True)
        drawer = page.split('id="ai-assistant"')[1].split("</aside>")[0]
        self.assertIn("disabled", drawer.split('id="assistantText"')[1].split(">")[0])
        self.assertIn("העוזר לא מחובר במחשב הזה", drawer)
        self.assertIn('placeholder="כתבו כאן מה תרצו לשנות…"', drawer)

    def test_the_drawer_remembers_the_page_it_is_on(self):
        page = self.client.get("/rules").get_data(as_text=True)
        self.assertIn('<input type="hidden" name="next" value="/rules">', page)

    def test_sending_returns_to_the_page_with_the_drawer_open(self):
        reply = {"assistant_message": "אוקיי", "rules": [{"description": "כלל", "hard": True}]}
        with patch.object(app, "interpret_with_claude", return_value=reply):
            response = self.client.post("/assistant/apply", data={
                "csrf_token": self.token(), "assistant_text": "משהו", "next": "/data"})
        self.assertTrue(response.headers["Location"].endswith("/data#assistant"))
        self.assertEqual(self.config()["ai_pending"]["rules"][0]["description"], "כלל")
        page = self.client.get("/data").get_data(as_text=True)
        drawer = page.split('id="ai-assistant"')[1].split("</aside>")[0]
        self.assertIn("<bdi>כלל</bdi>", drawer)
        self.assertIn('<input type="hidden" name="return_to" value="data">', drawer)

    def test_next_never_leaves_the_app(self):
        with patch.object(app, "interpret_with_claude", return_value={"assistant_message": "x"}):
            for target in ("//evil.example/x", "https://evil.example/", ""):
                response = self.client.post("/assistant/apply", data={
                    "csrf_token": self.token(), "assistant_text": "משהו", "next": target})
                location = urlsplit(response.headers["Location"])
                self.assertIn(location.netloc, ("", "localhost"), target)
                self.assertEqual((location.path, location.fragment), ("/", "assistant"), target)


if __name__ == "__main__":
    unittest.main()
