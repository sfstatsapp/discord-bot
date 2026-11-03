import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

import discord
from discord import app_commands

import bot  # noqa: F401  (loads .env and wires the router before the bridge is inspected)

from bubbot.runtime import message_router, sfstats_bridge, sfstats_messages, slash_commands
from bubbot.runtime.sfstats_bridge import DISCORD_API, BridgeConfig


ENABLED = BridgeConfig(url="https://dev.sfstats.app/internal/discord/interactions", secret="s" * 40, basic_auth="matar:pw")
GLOBAL = BridgeConfig(url=ENABLED.url, secret=ENABLED.secret, global_commands=True)
DISABLED = BridgeConfig()
INTERACTION = {"id": "923456789012345678", "token": "interaction-token", "application_id": "123", "type": 2,
               "data": {"name": "help", "type": 1}}


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status = status
        self._body = body

    async def json(self, content_type=None):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    closed = False

    def __init__(self, web_app=None):
        self.web_app = web_app if web_app is not None else FakeResponse(200, {"type": 5, "data": {"flags": 64}})
        self.calls = []

    def post(self, url, json=None, headers=None, auth=None, timeout=None):
        self.calls.append({"method": "POST", "url": url, "json": json, "headers": headers or {}, "auth": auth})
        if url.startswith(DISCORD_API):
            return FakeResponse(204)
        if isinstance(self.web_app, Exception):
            raise self.web_app
        return self.web_app

    def put(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"method": "PUT", "url": url, "json": json, "headers": headers or {}})
        return FakeResponse(200)


class CommandDefinitionTests(unittest.TestCase):
    def test_definitions_exported_from_the_web_app_load(self):
        names = {command["name"] for command in sfstats_bridge.COMMANDS}
        self.assertEqual(19, len(sfstats_bridge.COMMANDS))
        self.assertTrue({"profile", "vs", "leaderboard", "setup", "SF Stats profile", "Compare with me"} <= names)

    def test_bub_and_sfstats_command_names_do_not_collide(self):
        bub = {(command.name, 1) for command in message_router.tree.get_commands()}
        sfstats = {(command["name"], int(command.get("type", 1))) for command in sfstats_bridge.COMMANDS}
        self.assertEqual(set(), bub & sfstats)


class RoutingTests(unittest.TestCase):
    def test_sfstats_commands_menus_autocomplete_and_components_are_forwarded(self):
        forwarded = [
            {"type": 2, "data": {"name": "profile", "type": 1}},
            {"type": 2, "data": {"name": "SF Stats profile", "type": 2}},
            {"type": 4, "data": {"name": "vs", "type": 1}},
            {"type": 3, "data": {"custom_id": "s2:p:323456789012345678:123:m"}},
            {"type": 3, "data": {"custom_id": "sfplayer:panel:323456789012345678:123:habits"}},
            {"type": 5, "data": {"custom_id": "s2:lkm:323456789012345678"}},
        ]
        for payload in forwarded:
            with self.subTest(payload=payload):
                self.assertTrue(sfstats_bridge.is_sfstats_interaction(payload))

    def test_bub_interactions_stay_with_bub(self):
        kept = [
            {"type": 2, "data": {"name": "sf6", "type": 1}},
            {"type": 2, "data": {"name": "profile", "type": 2}},  # a user menu with a slash command's name
            {"type": 4, "data": {"name": "ggst", "type": 1}},
            {"type": 3, "data": {"custom_id": "sf6:frame:1"}},
            {"type": 3, "data": {"custom_id": "menu:game"}},
            {"type": 3, "data": {"custom_id": "quiz:answer:2"}},
            {"type": 1},
            None,
        ]
        for payload in kept:
            with self.subTest(payload=payload):
                self.assertFalse(sfstats_bridge.is_sfstats_interaction(payload))


class ForwardTests(unittest.IsolatedAsyncioTestCase):
    async def test_acknowledgement_is_relayed_to_discord(self):
        session = FakeSession()
        ack = await sfstats_bridge.forward(INTERACTION, session=session, config=ENABLED)
        self.assertEqual({"type": 5, "data": {"flags": 64}}, ack)
        web_app, callback = session.calls
        self.assertEqual(ENABLED.url, web_app["url"])
        self.assertEqual(INTERACTION, web_app["json"], "the raw interaction is forwarded unchanged")
        self.assertEqual(ENABLED.secret, web_app["headers"]["x-sfstats-internal-secret"])
        self.assertEqual(("matar", "pw"), (web_app["auth"].login, web_app["auth"].password))
        self.assertEqual(f"{DISCORD_API}/interactions/923456789012345678/interaction-token/callback", callback["url"])
        self.assertEqual(ack, callback["json"])

    async def test_failures_fall_back_to_a_private_notice(self):
        report_env = {"DISCORD_TOKEN": "bot-token", "SFSTATS_DISCORD_REPORT_CHANNEL_ID": "1563301451969724567"}
        for web_app in (FakeResponse(500, {"error": "x"}), FakeResponse(200, None), asyncio.TimeoutError(), OSError("down")):
            with self.subTest(web_app=type(web_app).__name__), patch.dict(os.environ, report_env):
                sfstats_messages._last_report.clear()
                session = FakeSession(web_app)
                ack = await sfstats_bridge.forward(INTERACTION, session=session, config=ENABLED)
                self.assertEqual(4, ack["type"])
                self.assertEqual(64, ack["data"]["flags"])
                self.assertEqual(sfstats_bridge.UNAVAILABLE_NOTICE, ack["data"]["content"])
                callback = next(call for call in session.calls if call["url"].endswith("/callback"))
                self.assertEqual(ack, callback["json"], "Discord still gets an answer in time")
                report = session.calls[-1]
                self.assertLess(session.calls.index(callback), session.calls.index(report), "the report waits for the acknowledgement")
                self.assertEqual(f"{DISCORD_API}/channels/1563301451969724567/messages", report["url"])
                self.assertIn("/help", report["json"]["content"])
                self.assertEqual({"parse": []}, report["json"]["allowed_mentions"])

    async def test_autocomplete_falls_back_to_no_choices(self):
        session = FakeSession(OSError("down"))
        ack = await sfstats_bridge.forward({**INTERACTION, "type": 4}, session=session, config=ENABLED)
        self.assertEqual({"type": 8, "data": {"choices": []}}, ack)

    async def test_no_basic_auth_when_not_configured(self):
        session = FakeSession()
        await sfstats_bridge.forward(INTERACTION, session=session, config=GLOBAL)
        self.assertIsNone(session.calls[0]["auth"])


class RawGatewayEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_sfstats_interactions_are_forwarded_and_only_when_enabled(self):
        with patch.object(sfstats_bridge, "forward", AsyncMock(return_value={})) as forward:
            self.assertIsNone(await sfstats_bridge.handle_raw_gateway_event({"op": 0, "t": "MESSAGE_CREATE", "d": {}}, config=ENABLED))
            self.assertIsNone(await sfstats_bridge.handle_raw_gateway_event(
                {"op": 0, "t": "INTERACTION_CREATE", "d": {"type": 2, "data": {"name": "sf6"}}}, config=ENABLED))
            self.assertIsNone(await sfstats_bridge.handle_raw_gateway_event({"op": 0, "t": "INTERACTION_CREATE", "d": INTERACTION}, config=DISABLED))
            task = await sfstats_bridge.handle_raw_gateway_event({"op": 0, "t": "INTERACTION_CREATE", "d": INTERACTION}, config=ENABLED)
            await task
            forward.assert_awaited_once()
            self.assertEqual(INTERACTION, forward.await_args.args[0])

    async def test_string_payloads_are_parsed(self):
        with patch.object(sfstats_bridge, "forward", AsyncMock(return_value={})) as forward:
            await (await sfstats_bridge.handle_raw_gateway_event(
                '{"op":0,"t":"INTERACTION_CREATE","d":{"id":"1","token":"t","type":2,"data":{"name":"help","type":1}}}', config=ENABLED))
            forward.assert_awaited_once()
            self.assertIsNone(await sfstats_bridge.handle_raw_gateway_event('{"op":11}', config=ENABLED))


class TreeErrorFilterTests(unittest.IsolatedAsyncioTestCase):
    def _tree(self):
        tree = type("Tree", (), {})()
        tree.on_error = AsyncMock()
        default = tree.on_error
        sfstats_bridge.install_tree_error_filter(tree)
        return tree, default

    async def test_unknown_sfstats_commands_are_not_logged_as_errors(self):
        tree, default = self._tree()
        with patch.object(sfstats_bridge, "CONFIG", ENABLED):
            await tree.on_error(None, app_commands.CommandNotFound("profile", []))
            await tree.on_error(None, app_commands.CommandNotFound("SF Stats profile", [], discord.AppCommandType.user))
            default.assert_not_awaited()
            await tree.on_error(None, app_commands.CommandNotFound("not-a-command", []))
            default.assert_awaited_once()

    async def test_errors_pass_through_when_the_bridge_is_off(self):
        tree, default = self._tree()
        with patch.object(sfstats_bridge, "CONFIG", DISABLED):
            await tree.on_error(None, app_commands.CommandNotFound("profile", []))
        default.assert_awaited_once()

    def test_router_installs_the_filter_and_raw_hook(self):
        self.assertIn("install_tree_error_filter", message_router.tree.on_error.__qualname__)
        self.assertTrue(callable(getattr(message_router, "on_socket_raw_receive", None)))
        self.assertEqual(sfstats_bridge.enabled(), message_router.client._enable_debug_events)


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    def test_guild_payload_drops_global_only_fields(self):
        payload = sfstats_bridge.guild_command_payload()
        self.assertEqual(19, len(payload))
        self.assertFalse(any("integration_types" in c or "contexts" in c for c in payload))
        setup = next(c for c in payload if c["name"] == "setup")
        self.assertEqual("32", setup["default_member_permissions"])

    def test_global_merge_only_at_cutover_and_never_overrides_bub(self):
        bub = [{"name": "sf6", "type": 1}, {"name": "help", "type": 1}]
        self.assertEqual(bub, sfstats_bridge.merge_global_commands(bub, config=ENABLED))
        merged = sfstats_bridge.merge_global_commands(bub, config=GLOBAL)
        self.assertEqual(bub[0], merged[0])
        self.assertEqual(1, sum(1 for c in merged if c["name"] == "help"), "a colliding SF Stats command is skipped")
        self.assertEqual(len(bub) + 18, len(merged))

    async def test_test_guilds_get_commands_while_porting_and_none_otherwise(self):
        for config, expected_count in ((ENABLED, 19), (GLOBAL, 0), (DISABLED, 0)):
            with self.subTest(global_commands=config.global_commands, enabled=config.enabled):
                session = FakeSession()
                results = await sfstats_bridge.sync_guild_commands("123", "bot-token", session=session, config=config)
                self.assertEqual(set(config.test_guild_ids), set(results))
                for call in session.calls:
                    self.assertEqual("PUT", call["method"])
                    self.assertRegex(call["url"], r"/applications/123/guilds/\d+/commands$")
                    self.assertEqual("Bot bot-token", call["headers"]["Authorization"])
                    self.assertEqual(expected_count, len(call["json"]))
        self.assertEqual({}, await sfstats_bridge.sync_guild_commands("123", None, session=FakeSession(), config=ENABLED))

    async def test_startup_sync_registers_test_guild_commands_after_bubs_sync(self):
        order = []
        with (
            patch.object(slash_commands, "_sync_bub_slash_commands", AsyncMock(side_effect=lambda *a: order.append("bub") or ["synced"])),
            patch.object(sfstats_bridge, "sync_guild_commands", AsyncMock(side_effect=lambda *a, **k: order.append("sfstats") or {})) as guilds,
            patch.dict(os.environ, {"DISCORD_TOKEN": "bot-token"}),
        ):
            client = type("Client", (), {"application_id": 123, "user": None})()
            self.assertEqual(["synced"], await slash_commands.sync_public_slash_commands(client, object()))
        self.assertEqual(["bub", "sfstats"], order)
        self.assertEqual((123, "bot-token"), guilds.await_args.args)

    async def test_rejected_activity_entry_retries_without_it_and_never_drops_sfstats_commands(self):
        tree = type("Tree", (), {"get_commands": lambda self: [], "sync": AsyncMock(return_value=[])})()
        client = type("Client", (), {"application_id": 123, "user": None})()
        synced = [{"name": "sf6", "type": 1}]
        for results, expected in (([None, synced], synced), ([None, None], None)):
            with self.subTest(results=results):
                with (
                    patch.object(slash_commands, "_bulk_sync_global_commands_with_activity_entry", AsyncMock(side_effect=results)) as bulk,
                    patch.object(sfstats_bridge, "CONFIG", GLOBAL),
                    patch.dict(os.environ, {"DISCORD_TOKEN": "bot-token"}),
                ):
                    self.assertEqual(expected, await slash_commands._sync_bub_slash_commands(client, tree))
                self.assertEqual([True, False], [call.args[3] for call in bulk.await_args_list])
        tree.sync.assert_not_awaited()

    async def test_activity_entry_can_be_left_out(self):
        with patch.dict(os.environ, {"STREETFIGHTERDLE_ACTIVITY_ENTRY": "false"}):
            self.assertFalse(slash_commands._activity_entry_enabled())
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STREETFIGHTERDLE_ACTIVITY_ENTRY", None)
            self.assertTrue(slash_commands._activity_entry_enabled())


class FrameFallbackTests(unittest.IsolatedAsyncioTestCase):
    """Bub's "missing scrolls" and invalid-query replies give the Decisions fallback one try first."""

    def message(self):
        replies = []

        async def reply(content=None, **kwargs):
            replies.append(content)
        return type("Message", (), {"content": "<@1> ryus medium punch", "reply": staticmethod(reply)})(), replies

    async def test_a_resolved_request_replaces_the_reply(self):
        msg, replies = self.message()
        with (
            patch.object(message_router, "message_directly_mentions_bot", lambda m: True),
            patch.object(message_router.decisions, "enabled", lambda config=None: True),
            patch.object(message_router.frame_resolver, "resolve_and_rerun", AsyncMock(return_value=True)) as rerun,
        ):
            self.assertIsNone(await message_router._reply_and_log_response(msg, message_router.MISSING_SCROLLS_TEXT, "missing_scrolls"))
        self.assertEqual([], replies)
        self.assertEqual("ryus medium punch", rerun.await_args.args[1].strip())

    async def test_inside_the_try_a_miss_stays_silent(self):
        msg, replies = self.message()
        token = message_router.frame_resolver._RESOLVING.set(True)
        try:
            self.assertIsNone(await message_router._reply_and_log_response(msg, message_router.MISSING_SCROLLS_TEXT, "missing_scrolls"))
        finally:
            message_router.frame_resolver._RESOLVING.reset(token)
        self.assertEqual([], replies)

    async def test_an_unread_request_gets_the_plain_card_without_buttons(self):
        sent = []

        async def reply(content=None, **kwargs):
            sent.append((content, kwargs))
        msg = type("Message", (), {"content": "<@1> jp jumping grab", "reply": staticmethod(reply)})()
        with (
            patch.object(message_router.decisions, "enabled", lambda config=None: False),
            patch.object(message_router.sfstats_bridge, "hand_over_message", AsyncMock(return_value=False)),
            patch.object(message_router, "notify_owner", AsyncMock()),
        ):
            await message_router._reply_and_log_response(msg, message_router.MISSING_SCROLLS_TEXT, "missing_scrolls")
        [(content, kwargs)] = sent
        self.assertIsNone(content)
        card = kwargs["view"]
        self.assertIsInstance(card, discord.ui.LayoutView)
        [container] = card.children
        self.assertEqual(0x0B0B0B, container.accent_colour.value)
        text = container.children[0].content
        self.assertTrue(text.startswith("I couldn't find that move.\n-# Try a character and a move"), text)
        self.assertNotIn("Report Issue", text)
        self.assertFalse([item for item in card.walk_children() if isinstance(item, discord.ui.Button)], "no buttons")

    async def test_other_replies_never_ask(self):
        with patch.object(message_router.frame_resolver, "resolve_and_rerun", AsyncMock(return_value=True)) as rerun:
            self.assertFalse(await message_router._resolve_with_decisions(type("M", (), {"content": "x", "mentions": [], "raw_mentions": []})()))
        rerun.assert_not_awaited()


class ConfigTests(unittest.TestCase):
    def test_environment_parsing(self):
        env = {"SFSTATS_BRIDGE_URL": " https://x/internal/discord/interactions ", "SFSTATS_INTERNAL_SECRET": "abc",
               "SFSTATS_BRIDGE_BASIC_AUTH": "u:p", "SFSTATS_BRIDGE_TIMEOUT_SECONDS": "9", "SFSTATS_COMMANDS_GLOBAL": "true",
               "SFSTATS_BRIDGE_GUILD_IDS": "1, 2,x"}
        with patch.dict(os.environ, env):
            config = sfstats_bridge.load_config()
        self.assertTrue(config.enabled)
        self.assertEqual("https://x/internal/discord/interactions", config.url)
        self.assertEqual(2.5, config.timeout_seconds, "capped below Discord's 3-second deadline")
        self.assertTrue(config.global_commands)
        self.assertEqual((1, 2), config.test_guild_ids)

    def test_dormant_without_url_and_secret(self):
        keys = ("SFSTATS_BRIDGE_URL", "SFSTATS_INTERNAL_SECRET", "SFSTATS_BRIDGE_GUILD_IDS")
        with patch.dict(os.environ, {k: "" for k in keys}):
            config = sfstats_bridge.load_config()
        self.assertFalse(config.enabled)
        self.assertEqual(sfstats_bridge.DEFAULT_TEST_GUILD_IDS, config.test_guild_ids)


if __name__ == "__main__":
    unittest.main()
