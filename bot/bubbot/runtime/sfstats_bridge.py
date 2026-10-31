"""Forward SF Stats interactions from the gateway to the SF Stats web app.

FrameBub receives every Discord interaction over its gateway connection. SF Stats
commands, buttons and forms are answered by the web app's existing handlers: this
module forwards the raw interaction to ``/internal/discord/interactions`` and relays
the web app's acknowledgement to Discord. The web app then does the slow work itself
through the interaction token, exactly as it does for interactions Discord sends it.

Everything is dormant until ``SFSTATS_BRIDGE_URL`` and ``SFSTATS_INTERNAL_SECRET`` are
set. Command definitions come from ``bubbot/data/sfstats_commands.json``, exported by
``scripts/export-discord-commands.mjs`` in the repository root.
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import aiohttp
from discord import app_commands

from bubbot.runtime import sfstats_messages


DISCORD_API = "https://discord.com/api/v10"
COMMANDS_FILE = Path(__file__).resolve().parents[1] / "data" / "sfstats_commands.json"
# Custom-id prefixes of SF Stats components (src/lib/discord/ids.ts), legacy cards included.
COMPONENT_PREFIXES = ("s2:", "sfplayer:", "sfboard:", "sfboard-disabled:")
# BUENAVISTA and the SF Stats test server.
DEFAULT_TEST_GUILD_IDS = (1345474576439836802, 1539390905503715528)
UNAVAILABLE_NOTICE = "SF Stats is temporarily unavailable. Try again in a moment."
# Fields only global commands use (install types and contexts).
_GLOBAL_ONLY_FIELDS = ("integration_types", "contexts")


def _env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _parse_guild_ids(raw):
    ids = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return tuple(ids)


@dataclass(frozen=True)
class BridgeConfig:
    url: str = ""
    secret: str = ""
    basic_auth: str = ""
    timeout_seconds: float = 2.0
    global_commands: bool = False
    test_guild_ids: tuple = DEFAULT_TEST_GUILD_IDS

    @property
    def enabled(self):
        return bool(self.url and self.secret)


def load_config():
    return BridgeConfig(
        url=os.getenv("SFSTATS_BRIDGE_URL", "").strip(),
        secret=os.getenv("SFSTATS_INTERNAL_SECRET", "").strip(),
        # "user:password" for a Caddy basic-auth login in front of the web app (dev.sfstats.app).
        basic_auth=os.getenv("SFSTATS_BRIDGE_BASIC_AUTH", "").strip(),
        # Discord needs the acknowledgement within 3 seconds, so leave room for the callback.
        timeout_seconds=max(0.5, min(2.5, _env_float("SFSTATS_BRIDGE_TIMEOUT_SECONDS", 2.0))),
        # Off while porting: commands are registered only in the test guilds.
        global_commands=_env_bool("SFSTATS_COMMANDS_GLOBAL", False),
        test_guild_ids=_parse_guild_ids(os.getenv("SFSTATS_BRIDGE_GUILD_IDS")) or DEFAULT_TEST_GUILD_IDS,
    )


def load_command_definitions(path=COMMANDS_FILE):
    try:
        with open(path, encoding="utf-8") as handle:
            return list(json.load(handle).get("commands") or [])
    except (OSError, ValueError) as error:
        print(f"[sfstats-bridge] Command definitions unavailable: {type(error).__name__}", flush=True)
        return []


CONFIG = load_config()
MESSAGES = sfstats_messages.load_config(CONFIG.url)
COMMANDS = load_command_definitions()
_COMMAND_KEYS = frozenset((command.get("name"), int(command.get("type", 1) or 1)) for command in COMMANDS)
_session = None


def enabled(config=None):
    return (config or CONFIG).enabled and bool(COMMANDS)


def handles_command(name, command_type=1):
    return (name, int(command_type or 1)) in _COMMAND_KEYS


def is_sfstats_interaction(payload):
    """True for SF Stats slash commands, context menus, autocomplete, buttons, selects and forms."""
    if not isinstance(payload, dict):
        return False
    data = payload.get("data") or {}
    interaction_type = payload.get("type")
    if interaction_type in (2, 4):
        return handles_command(data.get("name"), data.get("type", 1))
    if interaction_type in (3, 5):
        return str(data.get("custom_id") or "").startswith(COMPONENT_PREFIXES)
    return False


def fallback_ack(payload):
    """What Discord gets when the web app cannot answer in time."""
    if payload.get("type") == 4:
        return {"type": 8, "data": {"choices": []}}
    return {"type": 4, "data": {"content": UNAVAILABLE_NOTICE, "flags": 64}}


def _interaction_label(payload):
    data = payload.get("data") or {}
    if payload.get("type") in (2, 4):
        return str(data.get("name") or "")[:32]
    return str(data.get("custom_id") or "").split(":")[1][:16] if ":" in str(data.get("custom_id") or "") else ""


def _get_session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def _ask_web_app(session, payload, config):
    headers = {"Content-Type": "application/json", "x-sfstats-internal-secret": config.secret}
    auth = None
    if ":" in config.basic_auth:
        user, password = config.basic_auth.split(":", 1)
        auth = aiohttp.BasicAuth(user, password)
    timeout = aiohttp.ClientTimeout(total=config.timeout_seconds)
    async with session.post(config.url, json=payload, headers=headers, auth=auth, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"web app returned HTTP {response.status}")
        ack = await response.json(content_type=None)
    if not isinstance(ack, dict) or "type" not in ack:
        raise RuntimeError("web app returned no acknowledgement")
    return ack


async def _send_callback(session, payload, ack):
    url = f"{DISCORD_API}/interactions/{payload['id']}/{payload['token']}/callback"
    async with session.post(url, json=ack, timeout=aiohttp.ClientTimeout(total=5)) as response:
        if response.status >= 400:
            # Never log the URL: it contains the interaction token.
            print(f"[sfstats-bridge] Discord rejected the acknowledgement: HTTP {response.status}", flush=True)
        return response.status


async def forward(payload, *, session=None, config=None):
    """Forward one interaction and relay its acknowledgement; returns the acknowledgement sent."""
    config = config or CONFIG
    session = session or _get_session()
    started = time.monotonic()
    try:
        ack = await _ask_web_app(session, payload, config)
        outcome = "ok"
        failure = None
    except Exception as error:
        ack = fallback_ack(payload)
        outcome = f"unavailable ({type(error).__name__})"
        failure = type(error).__name__
    undelivered = None
    try:
        status = await _send_callback(session, payload, ack)
        if status >= 400:
            undelivered = f"Discord rejected the acknowledgement (HTTP {status})"
    except Exception as error:
        print(f"[sfstats-bridge] Acknowledgement delivery failed: {type(error).__name__}", flush=True)
        undelivered = f"acknowledgement not delivered ({type(error).__name__})"
    print(
        f"[sfstats-bridge] {_interaction_label(payload)} type={payload.get('type')} ack={ack.get('type')} "
        f"{outcome} {round((time.monotonic() - started) * 1000)}ms",
        flush=True,
    )
    if payload.get("type") != 4:
        # After the acknowledgement, never before it. Autocomplete fails quietly; anything a person sees fail is reported.
        if failure:
            await sfstats_messages.report_unavailable(session, "interaction", f"/{_interaction_label(payload)}: {failure}")
        elif undelivered:
            # The person sees "This interaction failed": usually the web app answered after Discord's 3 seconds.
            await sfstats_messages.report_unavailable(session, "acknowledgement", f"/{_interaction_label(payload)}: {undelivered}",
                                                      title="interaction failed")
    return ack


async def handle_raw_gateway_event(message, *, config=None):
    """``on_socket_raw_receive`` hook: forward SF Stats interactions without blocking the gateway."""
    if isinstance(message, (str, bytes)):
        if b'"INTERACTION_CREATE"' not in (message if isinstance(message, bytes) else message.encode()):
            return None
        try:
            message = json.loads(message)
        except ValueError:
            return None
    if not isinstance(message, dict) or message.get("t") != "INTERACTION_CREATE":
        return None
    payload = message.get("d")
    if not enabled(config) or not is_sfstats_interaction(payload):
        return None
    return asyncio.create_task(forward(payload, config=config))


async def claim_message(message, client, *, config=None, messages=None, reads_as_frame_data=None):
    """Bub's on_message hook: True when SF Stats answers a plain-language message, so Bub skips it.
    See ``sfstats_messages``; any failure leaves the message to Bub."""
    config = config or CONFIG
    messages = messages or MESSAGES
    if not enabled(config) or not messages.enabled:
        return False
    try:
        return await sfstats_messages.claim(
            message, bot_user_id=getattr(getattr(client, "user", None), "id", None),
            application_id=getattr(client, "application_id", None), session=_get_session(), bridge=config, config=messages,
            allowed_guild_ids=None if config.global_commands else frozenset(config.test_guild_ids),
            reads_as_frame_data=reads_as_frame_data)
    except Exception as error:
        print(f"[sfstats-messages] Routing failed, leaving the message to Bub: {type(error).__name__}", flush=True)
        await sfstats_messages.report_unavailable(_get_session(), "routing", type(error).__name__,
                                                  title="message routing failed")
        return False


async def hand_over_message(message, client, *, config=None, messages=None):
    """After Bub and the frame resolver both failed: True when SF Stats answered instead. See ``sfstats_messages.hand_over``."""
    config = config or CONFIG
    messages = messages or MESSAGES
    if not enabled(config) or not messages.enabled:
        return False
    try:
        return await sfstats_messages.hand_over(
            message, bot_user_id=getattr(getattr(client, "user", None), "id", None),
            application_id=getattr(client, "application_id", None), session=_get_session(), bridge=config, config=messages)
    except Exception as error:
        print(f"[sfstats-messages] Hand-over failed: {type(error).__name__}", flush=True)
        await sfstats_messages.report_unavailable(_get_session(), "hand-over", type(error).__name__,
                                                  title="message hand-over failed")
        return False


async def copy_web_app_report(message, client):
    """Bub's on_message hook for its own messages: see ``sfstats_messages.copy_web_app_report``."""
    try:
        return await sfstats_messages.copy_web_app_report(
            _get_session(), message, bot_user_id=getattr(getattr(client, "user", None), "id", None))
    except Exception as error:
        print(f"[sfstats-messages] Report copy failed: {type(error).__name__}", flush=True)
        return False


def install_tree_error_filter(tree):
    """Keep discord.py from logging "command not found" for commands the web app answers."""
    default_on_error = tree.on_error

    async def on_error(interaction, error):
        if isinstance(error, app_commands.CommandNotFound) and enabled():
            command_type = getattr(getattr(error, "type", None), "value", 1)
            if handles_command(error.name, command_type):
                return
        await default_on_error(interaction, error)

    tree.on_error = on_error
    return on_error


def guild_command_payload(commands=None):
    """Definitions for guild registration, which has no install types or contexts."""
    return [
        {key: value for key, value in command.items() if key not in _GLOBAL_ONLY_FIELDS}
        for command in (COMMANDS if commands is None else commands)
    ]


def merge_global_commands(bub_payload, *, config=None, commands=None):
    """Add SF Stats commands to Bub's global bulk sync once global registration is switched on."""
    config = config or CONFIG
    commands = COMMANDS if commands is None else commands
    if not (config.enabled and config.global_commands) or not commands:
        return list(bub_payload)
    taken = {(command.get("name"), int(command.get("type", 1) or 1)) for command in bub_payload}
    merged = list(bub_payload)
    for command in commands:
        key = (command.get("name"), int(command.get("type", 1) or 1))
        if key in taken:
            print(f"[sfstats-bridge] Skipping SF Stats command that collides with Bub: {key[0]}", flush=True)
            continue
        merged.append(command)
    return merged


async def sync_guild_commands(application_id, token, *, session=None, config=None):
    """Test guilds get the SF Stats commands while porting, and none once they are global or disabled."""
    config = config or CONFIG
    if not application_id or not token:
        return {}
    wanted = guild_command_payload() if enabled(config) and not config.global_commands else []
    session = session or _get_session()
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    results = {}
    for guild_id in config.test_guild_ids:
        url = f"{DISCORD_API}/applications/{application_id}/guilds/{guild_id}/commands"
        try:
            async with session.put(url, json=wanted, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as response:
                results[guild_id] = response.status
                if response.status >= 400:
                    print(f"[sfstats-bridge] Guild command sync failed for {guild_id}: HTTP {response.status}", flush=True)
        except Exception as error:
            results[guild_id] = None
            print(f"[sfstats-bridge] Guild command sync failed for {guild_id}: {type(error).__name__}", flush=True)
    if wanted:
        print(f"[sfstats-bridge] Registered {len(wanted)} SF Stats commands in test guilds: {list(config.test_guild_ids)}", flush=True)
    return results
