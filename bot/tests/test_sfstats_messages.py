"""Plain-language requests: which messages SF Stats claims from Bub, and what is sent.

Imports only ``sfstats_messages`` (no discord.py), so it runs anywhere aiohttp is installed:
``cd bot && PYTHONPATH=. python -m unittest tests.test_sfstats_messages``.
"""

import asyncio
import os
import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

from bubbot.runtime import sfstats_messages
from bubbot.runtime.sfstats_messages import MessageConfig, addressed, forward_payload, messages_url, snapshot


BUB = 555555555555555555
APP = 523456789012345678
SFSTATS_USER = "444444444444444444"
GUILD = 1345474576439836802
URL = "https://dev.sfstats.app/internal/discord/messages"
CONFIG = MessageConfig(enabled=True, url=URL, mention_ids=frozenset({SFSTATS_USER}))
ROUTING = MessageConfig(enabled=True, url=URL, mention_ids=frozenset({SFSTATS_USER}), route_bub_mentions=True)
REPORTS = f"{sfstats_messages.DISCORD_API}/channels/{sfstats_messages.DEFAULT_REPORT_CHANNEL}/messages"


@dataclass(frozen=True)
class Bridge:
    secret: str = "s" * 40
    basic_auth: str = "matar:pw"
    enabled: bool = True


def message(content, *, mentions=(), roles=(), reply_to=None, reference=False, bot=False, webhook=None, type=0, guild=GUILD):
    """A stand-in for discord.Message with the attributes routing reads."""
    resolved = SimpleNamespace(author=SimpleNamespace(id=reply_to)) if reply_to else None
    return SimpleNamespace(
        id=923456789012345678, channel=SimpleNamespace(id=823456789012345678),
        guild=SimpleNamespace(id=guild) if guild else None, author=SimpleNamespace(id=323456789012345678, bot=bot),
        content=content, type=SimpleNamespace(value=type), webhook_id=webhook,
        raw_mentions=[int(m) for m in mentions], raw_role_mentions=[int(r) for r in roles],
        reference=SimpleNamespace(resolved=resolved) if (reply_to or reference) else None,
    )


def kind(msg, config=CONFIG):
    return addressed(snapshot(msg, BUB), bot_user_id=BUB, mention_ids=config.mention_ids)


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status = status
        self._body = body if body is not None else {"route": "sfstats", "handled": True, "intent": "leaderboard"}

    async def json(self, content_type=None):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@dataclass
class FakeSession:
    response: object = field(default_factory=FakeResponse)
    calls: list = field(default_factory=list)

    def post(self, url, json=None, headers=None, auth=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers or {}, "auth": auth, "timeout": getattr(timeout, "total", None)})
        if url.startswith(sfstats_messages.DISCORD_API):
            return FakeResponse(200, {})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def claim(msg, session, config=ROUTING, guilds=frozenset({GUILD}), bridge=Bridge()):
    return asyncio.run(sfstats_messages.claim(msg, bot_user_id=BUB, application_id=APP, session=session, bridge=bridge,
                                              config=config, allowed_guild_ids=guilds))


class AddressingTests(unittest.TestCase):
    def test_sfstats_is_addressed_by_its_prefix_or_its_mentions(self):
        for content in ("sfstats leaderboard", "SF Stats my rank", "@sfstats whats teh leadeboard", "!sfstats help", "sf-stats queue"):
            self.assertEqual(kind(message(content)), "sfstats", content)
        self.assertEqual(kind(message(f"<@{SFSTATS_USER}> best ryu", mentions=[SFSTATS_USER])), "sfstats")
        self.assertEqual(kind(message("best ryu", roles=[SFSTATS_USER])), "sfstats", "a role works too")

    def test_mentions_of_bub_are_bubs_to_route(self):
        self.assertEqual(kind(message(f"<@{BUB}> japan leaderbaord ryu", mentions=[BUB])), "bub")
        self.assertIsNone(kind(message(f"<@{BUB}>", mentions=[BUB])), "a bare mention opens Bub's menu")
        self.assertIsNone(kind(message("which button?", mentions=[], reply_to=BUB)), "a reply's automatic ping is not a mention")
        self.assertIsNone(kind(message(f"<@{BUB}> the second one", mentions=[BUB], reply_to=BUB)), "answers to Bub's prompts stay Bub's")
        self.assertIsNone(kind(message(f"<@{BUB}> ryu 5hp", mentions=[BUB], reference=True)), "unresolved replies stay Bub's too")

    def test_unaddressed_chat_bots_webhooks_and_system_messages_stay_put(self):
        for content in ("ryu 5hp framedata", "what are sfstats", "the sf stats site is cool", "leaderboard"):
            self.assertIsNone(kind(message(content)), content)
        self.assertIsNone(kind(message("sfstats help", bot=True)))
        self.assertIsNone(kind(message("sfstats help", webhook=1)))
        self.assertIsNone(kind(message("sfstats help", type=7)))
        self.assertEqual(kind(message("sfstats help", type=19)), "sfstats", "replies count")

    def test_messages_url_follows_the_interactions_url(self):
        self.assertEqual(messages_url("https://dev.sfstats.app/internal/discord/interactions"), URL)
        self.assertEqual(messages_url("https://dev.sfstats.app/internal/discord/interactions/"), URL)
        self.assertEqual(messages_url("https://example.com/other"), "")

    def test_only_what_the_web_app_needs_is_sent(self):
        payload = forward_payload(snapshot(message("sfstats " + "x" * 5000), BUB), application_id=APP, bot_user_id=BUB,
                                  mention_ids=CONFIG.mention_ids, kind="sfstats")
        self.assertEqual(set(payload), {"id", "channel_id", "guild_id", "application_id", "author", "content", "addressed", "ignore_user_ids"})
        self.assertEqual(payload["application_id"], str(APP))
        self.assertEqual(payload["ignore_user_ids"], sorted({SFSTATS_USER, str(BUB)}))
        self.assertEqual(len(payload["content"]), sfstats_messages.MAX_CONTENT)


class ClaimTests(unittest.TestCase):
    def setUp(self):
        sfstats_messages._last_report.clear()

    def test_sfstats_requests_are_forwarded_and_kept_from_bub(self):
        session = FakeSession()
        self.assertTrue(claim(message("sfstats best ryu"), session))
        call = session.calls[0]
        self.assertEqual(call["url"], URL)
        self.assertEqual(call["json"]["addressed"], "sfstats")
        self.assertEqual(call["headers"]["x-sfstats-internal-secret"], "s" * 40)
        self.assertEqual((call["auth"].login, call["auth"].password), ("matar", "pw"))
        self.assertEqual(call["timeout"], 5.0)

    def test_requests_to_sfstats_the_web_app_hands_back_are_bubs(self):
        mention = message(f"<@{SFSTATS_USER}> ryu 5hp on block", mentions=[SFSTATS_USER])
        self.assertFalse(claim(mention, FakeSession(FakeResponse(200, {"route": "bub", "handled": False, "reason": "routed"}))))
        self.assertTrue(claim(mention, FakeSession(FakeResponse(200, {"route": "sfstats", "handled": True}))))
        self.assertTrue(claim(mention, FakeSession(FakeResponse(200, {"handled": False, "reason": "throttled"}))),
                        "only an explicit hand-back")

    def test_bub_mentions_go_to_whichever_bot_the_web_app_routes_them_to(self):
        mention = message(f"<@{BUB}> japan leaderbaord ryu", mentions=[BUB])
        session = FakeSession(FakeResponse(200, {"route": "sfstats", "handled": True}))
        self.assertTrue(claim(mention, session))
        self.assertEqual(session.calls[0]["json"]["addressed"], "bub")
        self.assertEqual(session.calls[0]["timeout"], 2.5, "Bub's mentions don't wait long")
        self.assertFalse(claim(mention, FakeSession(FakeResponse(200, {"route": "bub", "handled": False}))))

    def test_a_player_and_a_character_go_to_sf_stats_as_routed(self):
        """"my viper", "@friend this guys viper": decided locally (no model call), sent to SF Stats as routed."""
        friend = "222222222222222222"
        decide = patch.object(sfstats_messages.decisions, "decide", side_effect=AssertionError("asked the model"))
        with patch.object(sfstats_messages.decisions, "enabled", return_value=True), decide:
            for msg in (message(f"<@{BUB}> My Viper", mentions=[BUB]),
                        message(f"<@{SFSTATS_USER}> <@{friend}> this guys viper", mentions=[SFSTATS_USER, friend])):
                session = FakeSession(FakeResponse(200, {"route": "sfstats", "handled": True}))
                with self.subTest(content=msg.content):
                    self.assertTrue(claim(msg, session, config=ROUTING))
                    self.assertEqual(session.calls[0]["json"]["routed"], "sfstats")

    def test_bub_mentions_stay_with_bub_unless_routing_is_on(self):
        session = FakeSession()
        self.assertFalse(claim(message(f"<@{BUB}> best ryu", mentions=[BUB]), session, config=CONFIG))
        self.assertEqual(session.calls, [], "nothing leaves the bot")

    def test_when_the_web_app_fails_bub_answers_its_mentions_and_the_outage_is_reported(self):
        with patch.dict(os.environ, {"DISCORD_TOKEN": "bot-token"}):
            for response in (RuntimeError("down"), FakeResponse(502), FakeResponse(200, "not json")):
                sfstats_messages._last_report.clear()
                session = FakeSession(response)
                self.assertFalse(claim(message(f"<@{BUB}> ryu 5hp", mentions=[BUB]), session))
            sfstats_messages._last_report.clear()
            session = FakeSession(RuntimeError("down"))
            self.assertTrue(claim(message("sfstats <@1> @everyone best ryu"), session), "SF Stats requests never fall to Bub")
            report = session.calls[-1]
            self.assertEqual(report["url"], REPORTS)
            self.assertEqual(report["headers"]["Authorization"], "Bot bot-token")
            self.assertEqual(report["json"]["allowed_mentions"], {"parse": []}, "a quoted question never pings")
            self.assertIn("> sfstats <@1> @everyone best ryu", report["json"]["content"])

    def test_scope_and_switches(self):
        sf = message("sfstats best ryu")
        self.assertFalse(claim(message("sfstats best ryu", guild=1), FakeSession()), "test guilds only while porting")
        self.assertFalse(claim(message("sfstats best ryu", guild=None), FakeSession()), "no DMs while porting")
        self.assertTrue(claim(message("sfstats best ryu", guild=None), FakeSession(), guilds=None), "DMs once global")
        self.assertFalse(claim(sf, FakeSession(), config=MessageConfig()), "dormant by default")
        self.assertFalse(claim(sf, FakeSession(), bridge=Bridge(enabled=False)))
        self.assertFalse(asyncio.run(sfstats_messages.claim(sf, bot_user_id=None, application_id=APP, session=FakeSession(),
                                                            bridge=Bridge(), config=ROUTING)), "not before the bot is ready")

    def test_outage_reports_are_rate_limited_and_configurable(self):
        session = FakeSession()
        send = lambda now, kind="message": asyncio.run(sfstats_messages.report_unavailable(session, kind, "x", token="t", now=now))
        self.assertTrue(send(100.0))
        self.assertFalse(send(130.0), "one report a minute per kind")
        self.assertTrue(send(130.0, "interaction"))
        self.assertTrue(send(161.0))
        with patch.dict(os.environ, {"SFSTATS_DISCORD_REPORT_CHANNEL_ID": "", "BUB_OWNER_NOTIFY_CHANNEL_ID": "777777777777777777"}):
            self.assertEqual(sfstats_messages.report_channel(), "777777777777777777", "Bub's channel setting is shared")
        with patch.dict(os.environ, {"SFSTATS_DISCORD_REPORT_CHANNEL_ID": "off"}):
            sfstats_messages._last_report.clear()
            self.assertFalse(send(500.0))
        with patch.dict(os.environ, {"SFSTATS_DISCORD_REPORT_CHANNEL_ID": "", "BUB_OWNER_NOTIFY_CHANNEL_ID": ""}):
            self.assertEqual(sfstats_messages.report_channel(), sfstats_messages.DEFAULT_REPORT_CHANNEL)
        self.assertFalse(asyncio.run(sfstats_messages.report_unavailable(session, "other", "x", token="")), "no token, no report")


if __name__ == "__main__":
    unittest.main()


class HandOverTests(unittest.TestCase):
    """Bub and the frame resolver both failed: SF Stats answers messages addressed to it."""

    def hand_over(self, msg, session, config=ROUTING):
        return asyncio.run(sfstats_messages.hand_over(msg, bot_user_id=BUB, application_id=APP, session=session, bridge=Bridge(),
                                                      config=config))

    def test_a_message_to_sf_stats_goes_to_the_web_app_as_routed(self):
        session = FakeSession(FakeResponse(200, {"route": "sfstats", "handled": True, "intent": "custom"}))
        self.assertTrue(self.hand_over(message(f"<@{SFSTATS_USER}> who's my nemesis", mentions=[SFSTATS_USER]), session))
        self.assertEqual(session.calls[0]["json"]["routed"], "sfstats")

    def test_unanswered_failing_or_bub_messages_stay_with_bub(self):
        to_sfstats = message(f"<@{SFSTATS_USER}> most indian character", mentions=[SFSTATS_USER])
        self.assertFalse(self.hand_over(to_sfstats, FakeSession(FakeResponse(200, {"handled": False, "reason": "seen"}))))
        self.assertFalse(self.hand_over(to_sfstats, FakeSession(RuntimeError("down"))))
        session = FakeSession()
        self.assertFalse(self.hand_over(message(f"<@{BUB}> most indian character", mentions=[BUB]), session))
        self.assertEqual(session.calls, [], "Bub's own mentions never leave the bot")

