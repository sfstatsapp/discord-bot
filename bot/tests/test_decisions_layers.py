"""Layered Decisions: the route (frame data or SF Stats) in the bot, then game, character and move from Bub's tables.

Imports no discord.py: ``cd bot && PYTHONPATH=. python -m unittest tests.test_decisions_layers``.
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bubbot.runtime import decisions, frame_resolver, sfstats_messages
from bubbot.runtime.decisions import Config


ON = Config(key="k" * 20, url="https://decisions.test/alpha/decisions", model="m", timeout_seconds=1.0)
OFF = Config()


class Response:
    def __init__(self, status=200, body=None):
        self.status = status
        self._body = body

    async def json(self, content_type=None):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, auth=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers or {}})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def answers(**choices):
    return {"answers": {name: {"type": "choice", "choice": value, "confidence": confidence}
                        for name, (value, confidence) in choices.items()}}


class LocalRouteTests(unittest.TestCase):
    def test_wording_alone_settles_clear_requests(self):
        cases = {
            "<@1556366213784010792> ken 5mp": "framedata",
            "sfstats what's jp's 236lp on block": "framedata",
            "sf stats ryu cr.mk startup": "framedata",
            "<@1556366213784010792> ryus medium punch": "framedata",
            "kens crouching heavy kick": "framedata",
            "is ryu's fireball safe": "framedata",
            # From the channels: buttons, grabs and forward/back notation are moves; nemesis, hours and servers are stats.
            "jp air grab?": "framedata",
            "kens forward hk": "framedata",
            "terry longest range button": "framedata",
            "who's my nemesis": "sfstats",
            "what hours do i play the most": "sfstats",
            "server ladder pls": "sfstats",
            "chun lee best poke button": None,  # both: ask
            "<@1556366213784010792> my rank": "sfstats",
            # A player's stats as one character: the speaker's own, never Bub's character stats.
            "<@1556366213784010792> My Viper": "sfstats",
            "my ryu stats": "sfstats",
            "my viper 5mp": "framedata",  # a move: still frame data
            "top 10 ken players in japan": "sfstats",
            "best ryu combo": None,          # both: ask
            "ken vs ryu": None,              # neither: ask
            "hello": None,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, decisions.local_route(text))

    def test_a_mentioned_player_makes_it_sf_stats(self):
        self.assertEqual("sfstats", decisions.local_route("<@444444444444444444> <@222222222222222222> this guys viper", players=1))
        self.assertEqual("sfstats", decisions.local_route("<@444444444444444444> <@222222222222222222> viper", players=1))
        self.assertIsNone(decisions.local_route("<@444444444444444444> this guys viper"), "no player: ask")
        self.assertEqual("framedata", decisions.local_route("<@444444444444444444> <@222222222222222222> viper 5mp", players=1))

    def test_request_text_drops_markup_and_prefix(self):
        self.assertEqual("ken 5mp", decisions.request_text("<@1556366213784010792>  ken   5mp"))
        self.assertEqual("my rank", decisions.request_text("sfstats, my rank"))


class DecideTests(unittest.IsolatedAsyncioTestCase):
    QUESTIONS = {"route": decisions.choice(decisions.options(decisions.ROUTES, "neither", describe=decisions.ROUTES.get), "x")}

    async def test_only_listed_choices_are_kept(self):
        body = {"answers": {"route": {"type": "choice", "choice": "framedata", "probabilities": {"framedata": 0.9}},
                            "extra": {"type": "choice", "choice": "x", "confidence": 1}}}
        session = Session(Response(200, body))
        result = await decisions.decide("ken 5mp", self.QUESTIONS, session=session, config=ON)
        self.assertEqual({"route": ("framedata", 0.9)}, result)
        call = session.calls[0]
        self.assertEqual(ON.url, call["url"])
        self.assertEqual("Bearer " + ON.key, call["headers"]["Authorization"])
        self.assertEqual({"model": "m", "state": "ken 5mp", "questions": self.QUESTIONS}, call["json"])
        self.assertEqual("none", next(iter(call["json"]["questions"]["route"]["criteria"])))

    async def test_bigger_questions_may_wait_longer(self):
        class Timed(Session):
            def post(self, url, json=None, headers=None, auth=None, timeout=None):
                self.timeouts = getattr(self, "timeouts", []) + [timeout.total]
                return super().post(url, json=json, headers=headers)
        session = Timed(Response(200, {}), Response(200, {}))
        await decisions.decide("x", self.QUESTIONS, session=session, config=ON)
        await decisions.decide("x", self.QUESTIONS, session=session, config=ON, timeout_seconds=5.0)
        self.assertEqual([1.0, 5.0], session.timeouts)

    async def test_failures_and_unlisted_values_are_none(self):
        self.assertIsNone(await decisions.decide("x", self.QUESTIONS, session=Session(Response(500, {})), config=ON))
        self.assertIsNone(await decisions.decide("x", self.QUESTIONS, session=Session(asyncio.TimeoutError()), config=ON))
        unlisted = await decisions.decide("x", self.QUESTIONS, session=Session(Response(200, answers(route=("bub", 1.0)))), config=ON)
        self.assertEqual({}, unlisted)
        session = Session()
        self.assertIsNone(await decisions.decide("x", self.QUESTIONS, session=session, config=OFF))
        self.assertEqual([], session.calls)

    def test_sure_needs_confidence_and_a_real_value(self):
        self.assertEqual("sfstats", decisions.sure({"route": ("sfstats", 0.8)}, "route"))
        self.assertEqual("sfstats", decisions.sure({"route": ("sfstats", 0.45)}, "route"))
        self.assertIsNone(decisions.sure({"route": ("sfstats", 0.3)}, "route"))
        self.assertIsNone(decisions.sure({"route": ("none", 0.99)}, "route"))
        self.assertIsNone(decisions.sure(None, "route"))

    def test_options_cap_and_none_first(self):
        criteria = decisions.options([f"move {i}" for i in range(400)], "none of them")
        self.assertEqual(decisions.MAX_CHOICES, len(criteria))
        self.assertEqual(("none", "none of them"), next(iter(criteria.items())))

    async def test_route_asks_only_when_wording_is_unclear(self):
        session = Session()
        self.assertEqual("framedata", await decisions.route("ken 5mp", session=session, config=ON))
        self.assertEqual([], session.calls)
        session = Session(Response(200, answers(route=("sfstats", 0.92))))
        self.assertEqual("sfstats", await decisions.route("ken vs ryu", session=session, config=ON))
        self.assertEqual(1, len(session.calls))
        self.assertIsNone(await decisions.route("ken vs ryu", session=Session(), config=OFF))


ROWS = {
    "sol": [{"char_name": "Sol", "moveName": "Far Slash", "numCmd": "f.S"}, {"char_name": "Sol", "moveName": "Heavy Slash", "numCmd": "5H"}],
    "ky": [{"char_name": "Ky", "moveName": "Stun Edge", "numCmd": "236S"}],
}
SF6 = {"ken": [{"char_name": "Ken", "moveName": "Medium Punch", "numCmd": "5MP"}]}


def configure():
    frame_resolver.configure([("sf6", "Street Fighter 6", "sf6", SF6), ("ggst", "Guilty Gear Strive", "ggst", ROWS)])


def scripted(**by_label):
    """A stand-in for decisions.decide that answers each layer from by_label (None for a failed request)."""
    async def decide(text, questions, *, session, config=None, label="decide", timeout_seconds=None):
        value = by_label.get(label.replace("frame-", ""))
        return None if value is None else {next(iter(questions)): value}
    return AsyncMock(side_effect=decide)


class ResolveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        configure()

    async def test_a_named_game_character_and_move_become_a_query_bub_reads(self):
        decide = scripted(character=("Sol", 0.95), move=("Heavy Slash (5H)", 0.8))
        with patch.object(decisions, "decide", decide):
            result = await frame_resolver.resolve("strive sol's big slash", session=None, tagged=("ggst",), config=ON)
        self.assertEqual("ggst Sol 5H", result.query)
        self.assertEqual(["frame-character", "frame-move"], [c.kwargs["label"] for c in decide.await_args_list])
        moves = decide.await_args_list[1].args[1]["move"]["criteria"]
        self.assertEqual(["none", "Far Slash (f.S)", "Heavy Slash (5H)"], list(moves))

    async def test_street_fighter_6_unless_a_game_is_named(self):
        for tagged in ((), ("sf6",)):
            decide = scripted(character=("Ken", 0.9), move=("Medium Punch (5MP)", 0.9))
            with self.subTest(tagged=tagged), patch.object(decisions, "decide", decide):
                result = await frame_resolver.resolve("kens standing mp", session=None, tagged=tagged, config=ON)
            self.assertEqual("sf6 Ken 5MP", result.query)
            self.assertEqual(2, decide.await_count, "no game question")

    async def test_two_named_games_ask_only_between_them(self):
        games = [frame_resolver.Game("sf6", "Street Fighter 6", "sf6", SF6), frame_resolver.Game("ggst", "Guilty Gear Strive", "ggst", ROWS),
                 frame_resolver.Game("ggacr", "Guilty Gear XX Accent Core +R", "ggacr", ROWS)]
        decide = scripted(game=("Guilty Gear Strive", 0.9))
        with patch.object(decisions, "decide", decide):
            game = await decisions.pick_game("guilty gear sol", games, ("ggacr", "ggst"), session=None, config=ON, label="frame-game")
        self.assertEqual("ggst", game.key)
        self.assertEqual(["none", "Guilty Gear XX Accent Core +R", "Guilty Gear Strive"],
                         list(decide.await_args.args[1]["game"]["criteria"]))
        with patch.object(decisions, "decide", scripted(game=("Guilty Gear Strive", 0.3))):
            self.assertIsNone(await decisions.pick_game("guilty gear sol", games, ("ggacr", "ggst"), session=None, config=ON))

    async def test_any_unsure_layer_gives_up(self):
        for by_label in ({"game": ("Guilty Gear Strive", 0.3)},
                         {"game": ("Guilty Gear Strive", 0.9), "character": ("none", 0.99)},
                         {"game": ("Guilty Gear Strive", 0.9), "character": ("Sol", 0.9)},
                         {"game": ("Guilty Gear Strive", 0.9), "character": ("Sol", 0.9), "move": ("Stun Edge (236S)", 0.9)}):
            with self.subTest(by_label=by_label), patch.object(decisions, "decide", scripted(**by_label)):
                self.assertIsNone(await frame_resolver.resolve("?", session=None, config=ON))
        self.assertIsNone(await frame_resolver.resolve("?", session=None, config=OFF))


class Channel:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


def original():
    msg = SimpleNamespace(content="<@1> sols big slash", author=SimpleNamespace(id=7), channel=Channel(), replies=[])

    async def reply(content=None, **kwargs):
        msg.replies.append((content, kwargs))
    msg.reply = reply
    return msg


class RerunTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        configure()
        frame_resolver._recent.clear()
        self.decide = scripted(character=("Sol", 0.9), move=("Heavy Slash (5H)", 0.9))

    async def test_the_rewritten_request_answers_with_a_read_as_line(self):
        seen = {}

        async def rerun(proxy):
            seen["content"], seen["resolving"] = proxy.content, frame_resolver.resolving()
            await proxy.channel.send(embed="card")
            await proxy.channel.send(embed="second card")
        msg = original()
        with patch.object(decisions, "decide", self.decide):
            self.assertTrue(await frame_resolver.resolve_and_rerun(msg, "ggst sols big slash", rerun=rerun, bot_user_id=1,
                                                                   session=None, tagged=("ggst",), config=ON))
        self.assertEqual({"content": "<@1> ggst Sol 5H", "resolving": True}, seen)
        self.assertFalse(frame_resolver.resolving())
        self.assertEqual([(None, {"embed": "card"}), (None, {"embed": "second card"})], msg.channel.sent,
                         "Bub's own cards, without a \"Read as\" line")

    async def test_no_answer_leaves_the_notice_and_users_are_throttled(self):
        async def silent(proxy):
            return None
        with patch.object(decisions, "decide", self.decide):
            self.assertFalse(await frame_resolver.resolve_and_rerun(original(), "x", rerun=silent, bot_user_id=1, session=None,
                                                                    tagged=("ggst",), config=ON))
            # Same user again within the throttle window: no requests at all.
            self.assertFalse(await frame_resolver.resolve_and_rerun(original(), "x", rerun=silent, bot_user_id=1, session=None,
                                                                    tagged=("ggst",), config=ON))
        self.assertEqual(2, self.decide.await_count)


class ClaimRoutingTests(unittest.IsolatedAsyncioTestCase):
    BRIDGE = SimpleNamespace(secret="s" * 40, basic_auth="", enabled=True)
    CONFIG = sfstats_messages.MessageConfig(enabled=True, url="https://x/internal/discord/messages", mention_ids=frozenset({"444444444444444444"}))

    def message(self, content):
        return SimpleNamespace(id=923456789012345678, channel=SimpleNamespace(id=823456789012345678), guild=SimpleNamespace(id=1),
                               author=SimpleNamespace(id=323456789012345678, bot=False), content=content,
                               type=SimpleNamespace(value=0), webhook_id=None, raw_mentions=[444444444444444444],
                               raw_role_mentions=[], reference=None)

    async def claim(self, content, route):
        session = Session(Response(200, {"route": "sfstats", "handled": True}))
        with patch.object(decisions, "enabled", lambda config=None: True), \
                patch.object(decisions, "route", AsyncMock(return_value=route)):
            claimed = await sfstats_messages.claim(self.message(content), bot_user_id=555555555555555555, application_id=1,
                                                   session=session, bridge=self.BRIDGE, config=self.CONFIG)
        return claimed, session.calls

    async def test_frame_data_stays_with_bub_without_asking_the_web_app(self):
        claimed, calls = await self.claim("<@444444444444444444> ken 5mp", "framedata")
        self.assertFalse(claimed)
        self.assertEqual([], calls)

    async def test_sfstats_requests_are_forwarded_as_already_routed(self):
        claimed, calls = await self.claim("<@444444444444444444> my rank", "sfstats")
        self.assertTrue(claimed)
        self.assertEqual("sfstats", calls[0]["json"]["routed"])
        claimed, calls = await self.claim("<@444444444444444444> hmm", None)
        self.assertTrue(claimed)
        self.assertNotIn("routed", calls[0]["json"])


if __name__ == "__main__":
    unittest.main()
