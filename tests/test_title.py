"""Ticket titles: whole-sentence fallback, model summaries, and shared caps."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ai_agents
import intake_agent
import title_agent
from agent_tools import make_title
from config import MAX_TITLE

USER = {"id": "u-1", "username": "2024001", "role": "student",
        "college": "shaw", "block": "东座 1 栋", "dorm": "A-301"}
SESSION = {"id": "s-1", "user_id": "u-1", "history": [], "image_ids": [],
           "stage": "follow_up", "active_route": ""}
SAMPLE_EN = ("The door handle in our dorm room is loose. The screws at the base "
             "have come undone, and the handle wobbles every time you turn it "
             "— the door won't close properly.")
SAMPLE_ZH = "你好。" + ("宿舍门把手松了，底部螺丝掉了，每次开门都在晃，" * 8)


class DeterministicTitleTests(unittest.TestCase):
    def test_short_input_is_kept_whole(self):
        self.assertEqual(make_title("门锁坏了"), "门锁坏了")
        self.assertEqual(make_title("椅子靠背松动"), "椅子靠背松动")

    def test_long_input_ends_on_a_whole_clause(self):
        title = make_title(SAMPLE_ZH)
        self.assertLessEqual(len(title), MAX_TITLE)
        self.assertTrue(title.endswith("…") or title.endswith("。"))
        self.assertFalse("你好" in title)

    def test_no_sentence_punctuation_still_ends_cleanly(self):
        title = make_title("这是一段没有标点但是非常非常长的故障描述" * 8)
        self.assertLessEqual(len(title), MAX_TITLE)
        self.assertTrue(title.endswith("…"))

    def test_empty_input_is_empty(self):
        self.assertEqual(make_title(""), "")
        self.assertEqual(make_title(None), "")


class ModelTitleTests(unittest.TestCase):
    def call(self, reply, lang="en", description=SAMPLE_EN):
        with patch.object(title_agent, "call_model", return_value=reply):
            return title_agent.summarize_title("key", "model", description, lang)

    def test_no_key_returns_empty_for_fallback(self):
        self.assertEqual(title_agent.summarize_title("", "", SAMPLE_EN, "en"), "")

    def test_en_summary(self):
        self.assertEqual(self.call('{"title":"Loose door handle"}', "en"),
                         "Loose door handle")

    def test_zh_summary(self):
        self.assertEqual(self.call('{"title":"门把手松动"}', "zh"), "门把手松动")

    def test_fenced_json_is_accepted(self):
        self.assertEqual(self.call('```json\n{"title":"Door handle loose"}\n```'),
                         "Door handle loose")

    def test_copied_description_is_rejected(self):
        self.assertEqual(self.call(json.dumps({"title": SAMPLE_EN})), "")

    def test_wrong_language_is_rejected(self):
        self.assertEqual(self.call('{"title":"门把手松动"}', "en"), "")

    def test_broken_reply_falls_back(self):
        for reply in ("not json", "", '{"title":""}', '{"title":"Hi"}'):
            with self.subTest(reply=reply):
                self.assertEqual(self.call(reply), "")

    def test_model_error_falls_back(self):
        with patch.object(title_agent, "call_model",
                          side_effect=ai_agents.AIError(502, "down")):
            self.assertEqual(title_agent.summarize_title("k", "m", SAMPLE_EN, "en"), "")


class IntakeTitleTests(unittest.TestCase):
    def draft_reply(self, lang):
        description = SAMPLE_ZH if lang == "zh" else SAMPLE_EN
        return {"reply": "draft ready", "stage": "draft", "complete": True,
                "draft": {"title": make_title(description),
                          "description": description,
                          "repair_type": "other", "priority": "normal"},
                "missing": [], "self_help": [], "safety_warning": ""}

    def test_auto_title_is_replaced_by_model_summary(self):
        session = copy.deepcopy(SESSION)
        session["history"] = [{"role": "user", "content": SAMPLE_EN, "lang": "en"}]
        result = self.draft_reply("en")
        with patch("intake_agent._respond", return_value=result), \
             patch.object(title_agent, "call_model",
                          return_value='{"title":"Loose door handle"}'):
            out = intake_agent.respond("key", "model", session, USER, "en",
                                       force_draft=True)
        self.assertEqual(out["draft"]["title"], "Loose door handle")

    def test_hand_edited_title_is_kept(self):
        session = copy.deepcopy(SESSION)
        result = self.draft_reply("en")
        result["draft"]["title"] = "My own title"
        with patch("intake_agent._respond", return_value=result), \
             patch.object(title_agent, "call_model",
                          return_value='{"title":"Loose door handle"}'):
            out = intake_agent.respond("key", "model", session, USER, "en",
                                       force_draft=True)
        self.assertEqual(out["draft"]["title"], "My own title")

    def test_no_key_keeps_deterministic_title(self):
        session = copy.deepcopy(SESSION)
        result = self.draft_reply("zh")
        with patch("intake_agent._respond", return_value=result):
            out = intake_agent.respond("", "", session, USER, "zh", force_draft=True)
        self.assertEqual(out["draft"]["title"], make_title(SAMPLE_ZH))


class LimitParityTests(unittest.TestCase):
    def test_html_inputs_match_shared_cap(self):
        report = (ROOT / "web" / "pages" / "report.html").read_text(encoding="utf-8")
        tickets = (ROOT / "web" / "pages" / "tickets.html").read_text(encoding="utf-8")
        self.assertIn('id="fTitle" maxlength="%d"' % MAX_TITLE, report)
        self.assertIn('id="adTitle" maxlength="%d"' % MAX_TITLE, tickets)

    def test_config_exposes_cap_to_the_browser(self):
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('"title_max": MAX_TITLE', app)


if __name__ == "__main__":
    unittest.main()
