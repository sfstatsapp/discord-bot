"""Regression tests for SF6 comparison parsing and Report Issue buttons on every embed/result view."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import discord

import bot
from bubbot.features import fg_glossary, menu_system, quiz
from bubbot.frame_data import combo_data, frame_output
from bubbot.runtime import frame_rankings, frame_resolver, message_router


def _button_custom_ids(view):
    return [
        getattr(item, "custom_id", None)
        for item in getattr(view, "children", []) or []
        if isinstance(item, discord.ui.Button)
    ]


def _button_labels(view):
    return [
        getattr(item, "label", None)
        for item in getattr(view, "children", []) or []
        if isinstance(item, discord.ui.Button)
    ]


class SF6ComparisonRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not bot.FRAME_DATA:
            bot.load_frame_data()
        if not bot.FRAME_DATA:
            raise RuntimeError("Failed to load SF6 frame data")

    def test_single_and_two_char_comparisons_resolve_both_sides(self):
        cases = (
            ("jp regular vs od spike startup", [("Jp", "22P"), ("Jp", "22PP")]),
            ("jp normal vs od spike startup", [("Jp", "22P"), ("Jp", "22PP")]),
            ("jp od vs regular spike startup", [("Jp", "22PP"), ("Jp", "22P")]),
            ("jp spike vs od spike startup", [("Jp", "22P"), ("Jp", "22PP")]),
            ("jp regular spike vs od spike startup", [("Jp", "22P"), ("Jp", "22PP")]),
            ("jp 22p vs 22pp startup", [("Jp", "22P"), ("Jp", "22PP")]),
            ("compare startup of JP's 22P and JP's 22PP", [("Jp", "22P"), ("Jp", "22PP")]),
            ("jp regular vs od spike", [("Jp", "22P"), ("Jp", "22PP")]),
            ("jp 22k vs 22kk startup", [("Jp", "22K"), ("Jp", "22KK")]),
            ("jp regular vs od amnesia startup", [("Jp", "22K"), ("Jp", "22KK")]),
            ("ryu lp vs od fireball startup", [("Ryu", "236LP"), ("Ryu", "236PP")]),
            ("ryu od vs lp fireball startup", [("Ryu", "236PP"), ("Ryu", "236LP")]),
            ("ryu lp fireball vs od fireball startup", [("Ryu", "236LP"), ("Ryu", "236PP")]),
            ("ryu light vs heavy fireball startup", [("Ryu", "236LP"), ("Ryu", "236HP")]),
            ("ryu 236lp vs 236pp startup", [("Ryu", "236LP"), ("Ryu", "236PP")]),
            ("ken hp dp vs ryu od dp startup", [("Ken", "623HP"), ("Ryu", "623PP")]),
            ("ryu 5mp vs ken 5mp startup", [("Ryu", "5MP"), ("Ken", "5MP")]),
            ("ryu vs ken 5mp startup", [("Ryu", "5MP"), ("Ken", "5MP")]),
            ("ryu 5mp vs ken startup", [("Ryu", "5MP"), ("Ken", "5MP")]),
            ("ryu 5lp vs 2mk startup", [("Ryu", "5LP"), ("Ryu", "2MK")]),
            ("guile lp vs od boom startup", [("Guile", "46LP"), ("Guile", "46PP")]),
            ("cammy lk vs od spiral arrow startup", [("Cammy", "236LK"), ("Cammy", "236KK")]),
            ("chun stance lp vs alex stance hk startup", [("Chun-li", "214P > LP"), ("Alex", "2PP > 5HK")]),
        )
        for query, expected in cases:
            with self.subTest(query=query):
                payload = bot.find_moves_in_text(query)
                self.assertTrue(payload.get("wants_comparison"), query)
                got = [(r.get("char_name"), r.get("numCmd")) for r in payload.get("rows", [])]
                self.assertEqual(expected, got, query)

    def test_ambiguous_regular_vs_od_prompts_instead_of_dropping_regular_side(self):
        payload = bot.find_moves_in_text("ryu regular vs od fireball startup")
        self.assertEqual([], payload.get("rows", []))
        self.assertIn("Special Strength Options", payload.get("data", ""))

    def test_non_move_and_stats_comparisons_stay_intact(self):
        self.assertFalse(message_router._reads_as_frame_data("ken vs ryu"))
        walk_payload = bot.find_moves_in_text("ryu vs ken walk speed")
        self.assertEqual("stats", walk_payload.get("mode"))
        self.assertTrue(walk_payload.get("stats_only"))
        self.assertEqual([], walk_payload.get("rows", []))


class ReportIssueButtonCoverageTests(unittest.IsolatedAsyncioTestCase):
    async def test_glossary_view_includes_report_issue(self):
        view = fg_glossary.build_glossary_link_view("oki", prompt="what is oki")
        self.assertIn("bub_failed_prompt_report", _button_custom_ids(view))
        self.assertIn("Report Issue", _button_labels(view))

    async def test_quiz_question_view_includes_report_issue(self):
        row = {"char_name": "Ryu", "char_key": "ryu", "moveName": "Stand MP", "numCmd": "5MP", "startup": "6"}
        with patch.object(quiz, "clean_embed_value", lambda v, **_kw: str(v or "")):
            view = quiz.QuizQuestionView(row, mode="easy", game="sf6")
        self.assertIn("bub_failed_prompt_report", _button_custom_ids(view))
        self.assertIn("Report Issue", _button_labels(view))

    async def test_property_value_view_includes_report_issue_and_allows_non_owner_reports(self):
        row = {"char_name": "Jp", "char_key": "jp", "moveName": "Triglav", "numCmd": "22P", "startup": "22"}
        view = message_router.PropertyValueView(
            "sf6", "jp", [row], "startup", owner_id=10, content="Jp's Triglav (22P) startup is 22f."
        )
        self.assertIn("bub_failed_prompt_report", _button_custom_ids(view))
        self.assertIn("Report Issue", _button_labels(view))

        class _Resp:
            def __init__(self):
                self.calls = []

            async def send_message(self, *args, **kwargs):
                self.calls.append((args, kwargs))

        other_report = type("I", (), {
            "user": type("U", (), {"id": 99})(),
            "data": {"custom_id": "bub_failed_prompt_report"},
            "response": _Resp(),
        })()
        other_compare = type("I", (), {
            "user": type("U", (), {"id": 99})(),
            "data": {"custom_id": "other"},
            "response": _Resp(),
        })()
        self.assertTrue(await view.interaction_check(other_report))
        self.assertFalse(await view.interaction_check(other_compare))

    async def test_owner_locked_result_views_still_allow_non_owner_reports(self):
        view = menu_system.OwnedView(owner_id=10, menu_locked=True)
        other_report = type("I", (), {
            "user": type("U", (), {"id": 99})(),
            "data": {"custom_id": "bub_failed_prompt_report"},
            "response": type("R", (), {"send_message": AsyncMock()})(),
        })()
        self.assertTrue(await view.interaction_check(other_report))

    async def test_frame_ranking_reply_attaches_report_issue_view(self):
        sent_calls = []

        async def reply(*args, **kwargs):
            sent_calls.append(kwargs)
            return type("Sent", (), {"id": 42, "jump_url": "https://discord.com/channels/1/2/42"})()

        msg = type("Msg", (), {
            "content": "<@1> fastest normal",
            "id": 11,
            "jump_url": "https://discord.com/channels/1/2/11",
            "author": type("U", (), {"id": 5, "display_name": "Gino", "name": "gino"})(),
            "guild": None,
            "channel": None,
            "reply": staticmethod(reply),
        })()
        ranking = frame_rankings.Ranking(
            game=frame_resolver.Game("sf6", "Street Fighter 6", "sf6", {}),
            metric="startup",
            sort="asc",
            character=None,
            kind="normal",
            trait=None,
            count=1,
            entries=[("Ryu", "Stand LP (5LP)", 4.0, {"startup": "4", "onBlock": "-1"})],
        )
        with (
            patch.object(message_router.frame_rankings, "is_ranking", lambda text: True),
            patch.object(message_router.frame_rankings, "answer", AsyncMock(return_value=ranking)),
            patch.object(message_router.frame_resolver, "configure", lambda games: None),
            patch.object(message_router.frame_resolver, "games", lambda: []),
            patch.object(message_router.sfstats_bridge, "_get_session", lambda: None),
        ):
            self.assertTrue(await message_router._resolve_with_decisions_now(msg))
        self.assertEqual(1, len(sent_calls))
        self.assertIsNotNone(sent_calls[0].get("embed"))
        self.assertIn("bub_failed_prompt_report", _button_custom_ids(sent_calls[0].get("view")))

    async def test_ownerless_stats_response_still_attaches_report_issue_view(self):
        sent_calls = []

        class _Channel:
            async def send(self, *args, **kwargs):
                sent_calls.append(kwargs)
                return type("Sent", (), {"id": 77, "jump_url": ""})()

        with patch.object(frame_output, "FRAME_STATS", {"ryu": {"health": "10000"}}):
            await frame_output.send_character_stats_response(_Channel(), ["ryu"])
        self.assertEqual(1, len(sent_calls))
        self.assertIn("bub_failed_prompt_report", _button_custom_ids(sent_calls[0].get("view")))

    async def test_combo_list_view_includes_report_issue(self):
        page = discord.Embed(title="Ryu Combos", description="5MP > 2MK")
        view = combo_data.ComboListView("sf6", "ryu", [page], owner_id=1)
        self.assertIn("bub_failed_prompt_report", _button_custom_ids(view))


if __name__ == "__main__":
    unittest.main()
