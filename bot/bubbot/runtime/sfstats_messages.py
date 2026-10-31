"""Plain-language requests: decide whether SF Stats or Bub answers a message.

Messages addressed to SF Stats ("sfstats my rank", a mention of one of
``SFSTATS_MENTION_USER_IDS``) are forwarded to the web app's ``/internal/discord/messages``,
which understands them (``src/lib/discord/nl``) and replies with the card the matching slash
command shows. With ``SFSTATS_ROUTE_BUB_MENTIONS=true``, messages that @mention Bub are
forwarded too, and the web app routes them: its Decisions call (OpenRouter) picks Bub or SF
Stats. Bub answers whatever SF Stats doesn't confidently claim, and everything when the web
app is slow, failing or unconfigured. The reverse works too: a message to SF Stats that the web
app hands back (``route: "bub"``, e.g. "@SF Stats ryu 5hp") is answered by Bub. Replies to Bub's own messages (its follow-up prompts)
and unaddressed chat never leave the bot.

``claim`` is called at the top of Bub's on_message: True means SF Stats has the message and
Bub skips it. Dormant unless ``SFSTATS_NATURAL_LANGUAGE=true`` and the bridge is configured;
test guilds only until ``SFSTATS_COMMANDS_GLOBAL=true``.
"""

import os
import re
import time
from dataclasses import dataclass

import aiohttp

from bubbot.runtime import decisions


PREFIX = re.compile(r"^\s*[!@.]?\s*sf\s*-?\s*stats\b", re.IGNORECASE)
MENTION = re.compile(r"<@[!&]?\d{17,20}>")
MAX_CONTENT = 600
DISCORD_API = "https://discord.com/api/v10"
# The maintainers' report channel, shared with Bub's failed-prompt reports.
DEFAULT_REPORT_CHANNEL = "1563332693423431900"
# Where the web app posts its own reports of misread, refused and failed requests (reportChannel() in
# src/lib/discord/messages.ts). It posts them as this bot, so the bot sees them and copies them to the report channel.
DEFAULT_WEB_APP_REPORT_CHANNEL = "1563301451969724567"
WEB_APP_REPORT_PREFIX = "**SF Stats · "
REPORT_INTERVAL_SECONDS = 60.0
# 0 is a plain message and 19 a reply; joins, pins and other system messages are never requests.
USER_MESSAGE_TYPES = (0, 19)


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


def _ids(raw):
    return frozenset(part.strip() for part in str(raw or "").split(",") if part.strip().isdigit())


def messages_url(interactions_url):
    """``.../internal/discord/interactions`` → ``.../internal/discord/messages`` unless set explicitly."""
    explicit = os.getenv("SFSTATS_BRIDGE_MESSAGES_URL", "").strip()
    if explicit:
        return explicit
    base = (interactions_url or "").rstrip("/")
    return base[: -len("/interactions")] + "/messages" if base.endswith("/interactions") else ""


@dataclass(frozen=True)
class MessageConfig:
    enabled: bool = False
    url: str = ""
    mention_ids: frozenset = frozenset()
    route_bub_mentions: bool = False
    # SF Stats' own requests can wait for the web app; Bub's mentions must not wait long.
    timeout_seconds: float = 5.0
    route_timeout_seconds: float = 2.5


def load_config(interactions_url=""):
    return MessageConfig(
        enabled=_env_bool("SFSTATS_NATURAL_LANGUAGE", False),
        url=messages_url(interactions_url),
        mention_ids=_ids(os.getenv("SFSTATS_MENTION_USER_IDS")),
        route_bub_mentions=_env_bool("SFSTATS_ROUTE_BUB_MENTIONS", False),
        route_timeout_seconds=max(0.5, min(5.0, _env_float("SFSTATS_ROUTE_TIMEOUT_SECONDS", 2.5))),
    )


def snapshot(message, bot_user_id):
    """The plain facts about a discord.Message that routing needs, as a dict."""
    reference = getattr(message, "reference", None)
    replied = getattr(reference, "resolved", None) if reference else None
    message_type = getattr(message, "type", 0)
    guild = getattr(message, "guild", None)
    author = getattr(message, "author", None)
    return {
        "id": str(getattr(message, "id", "") or ""),
        "channel_id": str(getattr(getattr(message, "channel", None), "id", "") or ""),
        "guild_id": str(guild.id) if guild is not None else None,
        "author": {"id": str(getattr(author, "id", "") or ""), "bot": bool(getattr(author, "bot", False))},
        "content": str(getattr(message, "content", "") or ""),
        "type": getattr(message_type, "value", message_type),
        "webhook": bool(getattr(message, "webhook_id", None)),
        # Mentions written in the text; a reply's automatic ping of its author is not one.
        "mentions": {str(i) for i in (getattr(message, "raw_mentions", None) or [])}
                    | {str(i) for i in (getattr(message, "raw_role_mentions", None) or [])},
        "reply": bool(reference),
        "reply_to_bot": bool(replied is not None and str(getattr(getattr(replied, "author", None), "id", "")) == str(bot_user_id)),
    }


def addressed(facts, *, bot_user_id, mention_ids):
    """"sfstats" for messages addressed to SF Stats, "bub" for ones that mention Bub, else None."""
    if facts["author"]["bot"] or facts["webhook"] or facts["type"] not in USER_MESSAGE_TYPES:
        return None
    if PREFIX.match(facts["content"]) or facts["mentions"] & mention_ids:
        return "sfstats"
    if str(bot_user_id) not in facts["mentions"]:
        return None
    # Replies to Bub's own messages answer its prompts; a bare mention opens Bub's menu.
    if facts["reply"] or not MENTION.sub("", facts["content"]).strip():
        return None
    return "bub"


def forward_payload(facts, *, application_id, bot_user_id, mention_ids, kind):
    """Only what the web app needs to answer; never member lists, attachments or embeds."""
    return {
        "id": facts["id"],
        "channel_id": facts["channel_id"],
        "guild_id": facts["guild_id"],
        "application_id": str(application_id or ""),
        "author": {"id": facts["author"]["id"], "bot": False},
        "content": facts["content"][:MAX_CONTENT],
        "addressed": kind,
        "ignore_user_ids": sorted(mention_ids | {str(bot_user_id)}),
    }


def report_channel():
    """SFSTATS_DISCORD_REPORT_CHANNEL_ID, else Bub's BUB_OWNER_NOTIFY_CHANNEL_ID, else the default; "off" for none."""
    for name in ("SFSTATS_DISCORD_REPORT_CHANNEL_ID", "BUB_OWNER_NOTIFY_CHANNEL_ID"):
        raw = os.getenv(name, "").strip()
        if raw.lower() == "off":
            return ""
        if raw.isdigit():
            return raw
    return DEFAULT_REPORT_CHANNEL


def web_app_report_channel():
    """SFSTATS_WEB_APP_REPORT_CHANNEL_ID, else the web app's default; "off" stops copying its reports."""
    raw = os.getenv("SFSTATS_WEB_APP_REPORT_CHANNEL_ID", "").strip()
    if raw.lower() == "off":
        return ""
    return raw if raw.isdigit() else DEFAULT_WEB_APP_REPORT_CHANNEL


_last_report = {}


def _quote(text, limit):
    text = str(text or "").strip() or "(empty)"
    text = text[:limit] + ("…" if len(text) > limit else "")
    return "\n".join(f"> {line}" for line in text.splitlines() or [""])


async def report_unavailable(session, kind, reason, *, message=None, token=None, now=None, title="web app unavailable"):
    """The web app reports what it answers badly; when it can't be reached, or Discord refuses what it answered, the
    bot reports that. At most one report per kind a minute, so an outage doesn't flood the channel."""
    token = token if token is not None else os.getenv("DISCORD_TOKEN", "").strip()
    channel = report_channel()
    if not token or not channel:
        return False
    now = time.monotonic() if now is None else now
    if now - _last_report.get(kind, float("-inf")) < REPORT_INTERVAL_SECONDS:
        return False
    _last_report[kind] = now
    lines = [f"**SF Stats · {title}** ({kind})", f"Reason: {reason}"]
    if message:
        where = f"<#{message.get('channel_id')}>" if message.get("guild_id") else "a DM"
        lines += [f"<@{(message.get('author') or {}).get('id')}> in {where}", _quote(message.get("content"), 500)]
    return await _post_report(session, channel, "\n".join(lines), token)


async def copy_web_app_report(session, message, *, bot_user_id, token=None):
    """One of the web app's own reports ("**SF Stats · unsure**", "· error", ...), copied to the report channel, so
    every SF Stats error lands there. True when copied."""
    source, target = web_app_report_channel(), report_channel()
    if not source or not target or source == target:
        return False
    if str(getattr(getattr(message, "channel", None), "id", "")) != source:
        return False
    if not bot_user_id or str(getattr(getattr(message, "author", None), "id", "")) != str(bot_user_id):
        return False
    content = str(getattr(message, "content", "") or "")
    if not content.startswith(WEB_APP_REPORT_PREFIX):
        return False
    token = token if token is not None else os.getenv("DISCORD_TOKEN", "").strip()
    return bool(token) and await _post_report(session, target, content, token)


async def _post_report(session, channel, content, token):
    payload = {"content": content[:2000], "allowed_mentions": {"parse": []}, "flags": 4}
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    try:
        async with session.post(f"{DISCORD_API}/channels/{channel}/messages", json=payload, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=5)) as response:
            if response.status >= 400:
                print(f"[sfstats-messages] Report rejected: HTTP {response.status}", flush=True)
            return response.status < 400
    except Exception as error:
        print(f"[sfstats-messages] Report failed: {type(error).__name__}", flush=True)
        return False


async def forward(payload, *, url, session, bridge, timeout_seconds):
    headers = {"Content-Type": "application/json", "x-sfstats-internal-secret": bridge.secret}
    auth = None
    if ":" in bridge.basic_auth:
        user, password = bridge.basic_auth.split(":", 1)
        auth = aiohttp.BasicAuth(user, password)
    started = time.monotonic()
    try:
        async with session.post(url, json=payload, headers=headers, auth=auth,
                                timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as response:
            result = await response.json(content_type=None) if response.status == 200 else {"handled": False, "reason": f"HTTP {response.status}"}
        if not isinstance(result, dict):
            result = {"handled": False, "reason": "no answer"}
    except Exception as error:
        result = {"handled": False, "reason": type(error).__name__, "unavailable": True}
    # Never log message content: only where it went and what was understood.
    parts = [f"addressed={payload['addressed']}", f"route={result.get('route')}", f"intent={result.get('intent')}",
             f"handled={result.get('handled')}", str(result.get("reason") or ""), f"{round((time.monotonic() - started) * 1000)}ms"]
    print("[sfstats-messages] " + " ".join(part for part in parts if part), flush=True)
    return result


async def claim(message, *, bot_user_id, application_id, session, bridge, config, allowed_guild_ids=None,
                reads_as_frame_data=None):
    """True when SF Stats answers this message, so Bub must skip it. ``reads_as_frame_data(text)`` is Bub's own
    check: a message it reads as frame data ("yasmine boomerang") stays with Bub whatever the route question says."""
    if not (config.enabled and config.url and bridge.enabled and bot_user_id and application_id):
        return False
    facts = snapshot(message, bot_user_id)
    kind = addressed(facts, bot_user_id=bot_user_id, mention_ids=config.mention_ids)
    if kind is None or (kind == "bub" and not config.route_bub_mentions):
        return False
    if allowed_guild_ids is not None and (not facts["guild_id"] or int(facts["guild_id"]) not in allowed_guild_ids):
        return False
    # Frame data comes first: a move Bub's parser reads (a FAT common name like "boomerang") never reaches
    # the route question, which can mistake a character plus a word for SF Stats data.
    if reads_as_frame_data is not None and reads_as_frame_data(facts["content"]):
        print(f"[sfstats-messages] addressed={kind} route=framedata (parser)", flush=True)
        return False
    # With a Decisions key the bot routes first (bubbot.runtime.decisions): frame data stays with Bub, and
    # SF Stats data goes to the web app marked as routed, so it skips its own route question.
    route = await decisions.route(facts["content"], session=session) if decisions.enabled() else None
    if route == "framedata":
        print(f"[sfstats-messages] addressed={kind} route=framedata (bot)", flush=True)
        return False
    payload = forward_payload(facts, application_id=application_id, bot_user_id=bot_user_id, mention_ids=config.mention_ids, kind=kind)
    if route == "sfstats":
        payload["routed"] = "sfstats"
    result = await forward(payload, url=config.url, session=session, bridge=bridge,
                           timeout_seconds=config.route_timeout_seconds if kind == "bub" else config.timeout_seconds)
    failed = result.get("unavailable") or str(result.get("reason") or "").startswith("HTTP ")
    if kind == "sfstats":
        # Addressed to SF Stats: Bub answers only what the web app hands back to it ("@SF Stats
        # ryu 5hp", with SFSTATS_ROUTE_TO_BUB on the web app); never when the web app can't answer.
        if failed:
            await report_unavailable(session, "message", result.get("reason"), message=payload)
            return True
        return result.get("route") != "bub"
    # Addressed to Bub: SF Stats takes it only when the web app says so; otherwise Bub answers as always.
    if failed:
        await report_unavailable(session, "route", result.get("reason"))
    return result.get("route") == "sfstats"


async def hand_over(message, *, bot_user_id, application_id, session, bridge, config):
    """True when SF Stats answered a message addressed to it that Bub couldn't read either ("who's my nemesis" routed
    to frame data, "most indian character"). Sent as routed to SF Stats, so the web app never hands it back."""
    if not (config.enabled and config.url and bridge.enabled and bot_user_id and application_id):
        return False
    facts = snapshot(message, bot_user_id)
    if addressed(facts, bot_user_id=bot_user_id, mention_ids=config.mention_ids) != "sfstats":
        return False
    payload = forward_payload(facts, application_id=application_id, bot_user_id=bot_user_id, mention_ids=config.mention_ids,
                              kind="sfstats")
    payload["routed"] = "sfstats"
    result = await forward(payload, url=config.url, session=session, bridge=bridge, timeout_seconds=config.timeout_seconds)
    return bool(result.get("handled"))
