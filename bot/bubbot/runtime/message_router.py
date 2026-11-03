"""Main Discord message router. Owns on_message flow: quiz, menus, combos, per-game
frame/gif routing, SF6 prompt replies, buenavista LLM hook, and invalid-query fallback.
Also holds SF6 compat wrappers (load_frame_data, find_moves_in_text) for import bot regressions."""

import discord
import asyncio
import random
import datetime
import difflib
import json
import os
import re
from dotenv import load_dotenv

load_dotenv()

from bubbot.data.aliases import (
    CHARACTER_ALIASES,
    CHARACTER_INPUT_ALIASES,
    DP_PREFIX_EXCEPTIONS,
    INPUT_ALIASES,
)
try:
    from bubbot.features.reminders import ReminderManager
except ImportError:  # Bub's own deployment only; frame data runs without it (frame_only.py).
    from bubbot.runtime.frame_only import FrameOnlyReminders as ReminderManager
import bubbot.features.quiz as quiz_module
import bubbot.frame_data.gif_lookup as gif_lookup_module
import bubbot.frame_data.frame_output as frame_output_module
import bubbot.frame_data.sf6_loader as sf6_loader
import bubbot.frame_data.sf6_lookup as sf6_lookup
import bubbot.frame_data.sf6_parser as sf6_parser
import bubbot.frame_data.sf6_prompt_replies as sf6_prompt_replies
import bubbot.frame_data.ggst_frame_data as ggst_module
import bubbot.frame_data.sfv_frame_data as sfv_module
import bubbot.frame_data.tuco_frame_data as tuco_module
import bubbot.frame_data.bbcf_frame_data as bbcf_module
import bubbot.frame_data.ggacr_frame_data as ggacr_module
import bubbot.frame_data.cotw_frame_data as cotw_module
import bubbot.frame_data.third_strike_frame_data as third_strike_module
import bubbot.frame_data.usfiv_frame_data as usfiv_module
import bubbot.frame_data.mk1_frame_data as mk1_module
import bubbot.frame_data.avtl_frame_data as avtl_module
import bubbot.frame_data.combo_data as combo_data_module
import bubbot.features.menu_system as menu_system
from bubbot.features.fg_glossary import (
    build_glossary_definition_embed,
    build_glossary_link_view,
    glossary_question_term,
    normalize_glossary_term,
)
from bubbot.frame_data.frame_output import (
    send_character_stats_response,
    send_frame_embeds_with_views,
    send_frame_table_response,
    send_gif_links_response,
    send_missing_hitbox_gif_reply,
)
from bubbot.frame_data.gif_lookup import get_frame_row_gif_links
from bubbot.runtime.config import (
    BASE_DIR,
    LOCAL_HITBOX_GIF_EXTENSIONS,
    LOCAL_HITBOX_GIF_ROOT,
    RANGE_MISSING_PLACEHOLDERS,
    MISSING_SCROLLS_TEXT,
    PUBLIC_INVALID_QUERY_TEXT,
    MISSING_SCROLLS_HINT,
    RANGE_SCROLLS_MISSING_TEXT,
    TOKEN,
)
from bubbot.utils.character_lookup import find_aliases_in_text, resolve_alias_key, text_mentions_alias
from bubbot.utils.response_log import (
    FAILED_PROMPT_REASONS,
    INTERACTION_MENU,
    INTERACTION_PROMPT,
    build_log_record,
    log_message_and_reply,
    notify_owner,
    log_record,
)
from bubbot.utils.text_utils import (
    compact_key,
    contains_token_sequence,
    normalize_query_terms,
    remove_first_token_sequence,
    word_tokens,
)
try:
    from bubbot.runtime.buenavista_extension import buenavista_extension
except ImportError:  # Bub's own deployment only; frame data runs without it (frame_only.py).
    from bubbot.runtime.frame_only import buenavista_extension
from collections import deque
from bubbot.features.failure_card import FailureCard
from bubbot.utils.sfstats_style import ACTION, CardView, card_text, label_icon
from bubbot.runtime import decisions, frame_rankings, frame_resolver, render_server, sfstats_bridge
from bubbot.runtime.slash_commands import register_slash_commands
from bubbot.runtime.startup import handle_ready


_FRAME_DATA_RESPONSE_IDS = deque(maxlen=500)


# Bub couldn't read the request ("I don't have the scrolls", the invalid-query notice): the Decisions API
# gets one try first (frame_resolver), reading it into a game, character and move from Bub's tables.
_RESOLVABLE_REASONS = frozenset({"missing_scrolls", "public_invalid_query"})


async def _resolve_with_decisions(message):
    if render_server.rendering() or not decisions.enabled() or not message_directly_mentions_bot(message):
        return False
    if frame_resolver.throttled(str(getattr(getattr(message, "author", None), "id", ""))):
        return False
    try:
        async with message.channel.typing():
            return await _resolve_with_decisions_now(message)
    except (AttributeError, TypeError, discord.HTTPException):
        # No typing indicator (a stand-in channel, or Discord refused it): answer without one.
        return await _resolve_with_decisions_now(message)


async def _resolve_with_decisions_now(message):
    text = strip_discord_mentions(str(getattr(message, "content", "") or ""))
    frame_resolver.configure(_frame_resolver_games())
    tagged = frame_resolver.tags_in(text.lower(), _cheap_explicit_frame_game_tags)
    session = sfstats_bridge._get_session()
    # "the move with the most range", "fastest normal": a ranking over Bub's tables, not one move.
    if frame_rankings.is_ranking(text):
        ranking = await frame_rankings.answer(text, games=frame_resolver.games(), session=session, tagged=tagged,
                                              label=frame_resolver.move_label)
        if ranking is not None and ranking.entries:
            await message.reply(embed=frame_rankings.embed(ranking), mention_author=False)
            print(f"[frame-rank] game={ranking.game.key} metric={ranking.metric} sort={ranking.sort} n={len(ranking.entries)}", flush=True)
            return True
    return await frame_resolver.resolve_and_rerun(
        message,
        text,
        rerun=_handle_message,
        bot_user_id=getattr(client.user, "id", None),
        session=session,
        tagged=tagged,
        throttle=False,
    )


# Reply helpers: log failed prompts, attach Report Issue, stamp frame-data ids for reports
async def _reply_and_log_response(message, response_text, reason, **kwargs):
    if reason in _RESOLVABLE_REASONS:
        # Inside the try a miss stays silent, so the original message gets this reply instead.
        if frame_resolver.resolving():
            return None
        if await _resolve_with_decisions(message):
            return None
        # Nothing in it reads as a move ("who's my nemesis", "most indian character"): SF Stats answers instead.
        text = strip_discord_mentions(str(getattr(message, "content", "") or ""))
        if (not render_server.rendering() and decisions.local_route(text) != "framedata"
                and await sfstats_bridge.hand_over_message(message, client)):
            return None
    view = kwargs.pop("view", None)
    if reason in FAILED_PROMPT_REASONS and view is None:
        # The plain card, like SF Stats' notices; the report channel gets the failure either way (below).
        hint = MISSING_SCROLLS_HINT if reason in _RESOLVABLE_REASONS else None
        sent = await message.reply(view=FailureCard(response_text, hint), **kwargs)
    else:
        sent = await message.reply(response_text, view=view, **kwargs)
    for child in getattr(view, "children", []) or []:
        report_context = getattr(child, "report_context", None)
        if isinstance(report_context, dict):
            report_context["reply_message_id"] = getattr(sent, "id", None)
            report_context["reply_jump_url"] = str(getattr(sent, "jump_url", "") or "")
            report_context["client"] = client
    try:
        log_message_and_reply(
            message,
            sent,
            interaction_type=INTERACTION_PROMPT,
            reason=reason,
            response_text=response_text,
        )
        if reason in FAILED_PROMPT_REASONS:
            record = build_log_record(
                interaction_type=INTERACTION_PROMPT,
                reason=reason,
                prompt=str(getattr(message, "content", "") or ""),
                response_text=response_text,
                failure_reason=reason,
                source_message=message,
                reply_message=sent,
            )
            await notify_owner(client, record)
    except Exception as log_error:
        print(f"Response log error: {log_error}", flush=True)
    return sent


def _message_prompt_text(message):
    parts = [str(getattr(message, "content", "") or "")]
    for embed in getattr(message, "embeds", []) or []:
        parts.append(str(getattr(embed, "title", "") or ""))
        parts.append(str(getattr(embed, "description", "") or ""))
    return "\n".join(part for part in parts if part).strip()


def _text_is_numbered_disambiguation_prompt(text):
    return bool(_NUMBERED_DISAMBIGUATION_PROMPT_RE.search(str(text or "")))


def _message_is_numbered_disambiguation_prompt(message):
    return _text_is_numbered_disambiguation_prompt(_message_prompt_text(message))


def _record_frame_data_ids(ids, *, response_text=None):
    if response_text and _text_is_numbered_disambiguation_prompt(response_text):
        return
    for mid in (ids or []):
        if mid:
            _FRAME_DATA_RESPONSE_IDS.append(mid)


_FRAME_RESULT_COMPONENT_LABELS = {
    "back to menu",
    "compare",
    "hide image",
    "hide notes",
    "return to menu",
    "show all images",
    "show full framedata",
    "show gif",
    "show hitbox",
    "show image",
    "show notes",
}


def _message_component_labels(message):
    labels = set()
    for component in getattr(message, "components", []) or []:
        children = getattr(component, "children", None) or []
        for child in children:
            label = str(getattr(child, "label", "") or "").strip().lower()
            if label:
                labels.add(label)
    return labels


def _is_frame_data_embed(embed):
    field_names = {
        str(getattr(field, "name", "") or "").strip().lower()
        for field in getattr(embed, "fields", []) or []
    }
    if {"startup", "active", "recovery"}.issubset(field_names):
        return True
    if "input" in field_names and "startup" in field_names and {"on hit", "on block"} & field_names:
        return True
    title = str(getattr(embed, "title", "") or "").strip().lower()
    if title.startswith(("ggst - ", "ggacr - ", "2xko - ", "bbcf - ", "cotw - ", "third strike - ", "ultra street fighter iv - ", "mk1 - ", "avtl - ")):
        return bool(field_names & {"startup", "input", "on hit", "on block"})
    return False


def _is_quiz_embed(embed):
    title = str(getattr(embed, "title", "") or "").strip().lower()
    return "frame data quiz" in title


def _is_card_message(message):
    return bool(getattr(getattr(message, "flags", None), "components_v2", False))


def _message_looks_like_quiz_output(message):
    return any(_is_quiz_embed(embed) for embed in getattr(message, "embeds", []) or [])


def _message_looks_like_frame_data_output(message):
    if any(_is_frame_data_embed(embed) for embed in getattr(message, "embeds", []) or []):
        return True

    labels = _message_component_labels(message)
    if labels and labels <= _FRAME_RESULT_COMPONENT_LABELS:
        return True
    if {"compare", "show full framedata"}.issubset(labels):
        return True

    if getattr(message, "attachments", None):
        return True

    content = str(getattr(message, "content", "") or "").strip().lower()
    if not content:
        return False
    if "wiki.supercombo.gg/images" in content:
        return True
    if "dustloop.com" in content or "dreamcancel.com" in content:
        return True
    if re.fullmatch(r"(?:https?://\S+\s*)+", content) and re.search(r"\.(?:png|webp|gif|jpg|jpeg)(?:\?|\b)", content):
        return True
    if re.search(r"\b\w[\w .'-]*'s .+ \(.+\) .+ is .+\.\s*$", content):
        return True
    return False


_NUMBERED_DISAMBIGUATION_PROMPT_RE = re.compile(
    r"(?:Special Strength Options|Target Combo Options|Multiple (?:SFV|GGST|2XKO|BBCF|COTW|Third Strike|USF4|MK1) moves match)"
)


async def _is_reply_to_suppressed_bub_message(message):
    if not message.reference:
        return False
    replied_id = message.reference.message_id
    try:
        replied_message = await _fetch_referenced_message(message)
    except (discord.NotFound, discord.Forbidden):
        return False
    except Exception as error:
        print(f"Frame data reply guard fetch error: {error}", flush=True)
        return False
    if not replied_message or getattr(replied_message, "author", None) != client.user:
        return False

    if _message_is_numbered_disambiguation_prompt(replied_message):
        return False

    if replied_id in _FRAME_DATA_RESPONSE_IDS:
        return True

    if _message_looks_like_quiz_output(replied_message):
        return False

    # SF Stats' cards and Bub's menus are Components V2 messages: no embeds, but the same rule.
    if getattr(replied_message, "embeds", None) or _is_card_message(replied_message):
        return True

    if _message_looks_like_frame_data_output(replied_message):
        _FRAME_DATA_RESPONSE_IDS.append(replied_id)
        return True
    return False


async def _is_reply_to_frame_data(message):
    return await _is_reply_to_suppressed_bub_message(message)


def is_missing_attack_range_value(raw_value):
    text = str(raw_value or "").strip()
    if not text:
        return True
    normalized = re.sub(r"\s+", "", text).lower()
    return normalized in RANGE_MISSING_PLACEHOLDERS

MENTION_PATTERN = re.compile(r"<@!?\d+>|<@&\d+>|<#\d+>")


def strip_url_like_text(text):
    cleaned = str(text or "")
    cleaned = re.sub(r"https?://\S+", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bwww\.\S+\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\S+\.gif(?:\?\S*)?\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\S+\.gifv(?:\?\S*)?\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def message_directly_mentions_bot(message):
    bot_user = getattr(client, "user", None)
    bot_id = getattr(bot_user, "id", None)
    if bot_id is None:
        return False
    if any(getattr(user, "id", None) == bot_id for user in getattr(message, "mentions", []) or []):
        return True
    return bot_id in (getattr(message, "raw_mentions", []) or [])


def is_plain_bot_mention_only(message):
    """True when the message is only a direct @Bub ping (works for replies too)."""
    if not message_directly_mentions_bot(message):
        return False
    content_no_mentions = strip_discord_mentions(getattr(message, "content", "") or "")
    return not content_no_mentions.strip()


async def _send_main_menu_for_plain_mention(message):
    await menu_system.send_main_menu(message.channel, owner_id=message.author.id)
    try:
        log_record(
            build_log_record(
                interaction_type=INTERACTION_MENU,
                reason="empty_mention_menu",
                prompt=str(getattr(message, "content", "") or ""),
                response_text="Opened main menu embed.",
                response_kind="embed",
                source_message=message,
            )
        )
    except Exception as log_error:
        print(f"Response log error: {log_error}", flush=True)


def has_explicit_gif_lookup_intent(text):
    cleaned = normalize_query_terms(strip_url_like_text(strip_discord_mentions(text).lower()))
    return bool(
        re.search(r"\bgif(?:s)?\b", cleaned)
        or re.search(r"\bhit\s*box(?:es)?\b", cleaned)
        or re.search(r"\bhitbox(?:es)?\b", cleaned)
        or re.search(r"\b(?:image|picture)(?:s)?\b", cleaned)
    )


def extract_glossary_lookup_term(text):
    cleaned = strip_discord_mentions(text).strip()
    patterns = (
        r"^(?:(?:fg|fighting\s+game)\s+)?glossary(?:\s+(.+))?$",
        r"^fgg(?:\s+(.+))?$",
        r"^(?:define|definition)(?:\s+(.+))?$",
    )
    for pattern in patterns:
        match = re.match(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            return normalize_glossary_term(match.group(1) or "")
    return glossary_question_term(cleaned)


async def maybe_handle_glossary_lookup(message, content_no_mentions, *, addressed):
    if not addressed:
        return False
    term = extract_glossary_lookup_term(content_no_mentions)
    if term is None:
        return False
    sent = await message.reply(embed=build_glossary_definition_embed(term), view=build_glossary_link_view(term))
    try:
        log_message_and_reply(
            message,
            sent,
            interaction_type=INTERACTION_PROMPT,
            reason="fg_glossary_lookup",
            response_text=f"Opened FG glossary lookup for: {term or 'home'}",
        )
    except Exception as log_error:
        print(f"Response log error: {log_error}", flush=True)
    return True


DISAMBIGUATION_GAME_CONFIGS = [
    {
        "label": "SFV",
        "prefix": "sfv",
        "module": sfv_module,
        "prompt_re": re.compile(r"Multiple SFV moves match (.+?)\. (?:Please specify one|Reply with the option number):"),
    },
    {
        "label": "GGST",
        "prefix": "ggst",
        "module": ggst_module,
        "prompt_re": re.compile(r"Multiple GGST moves match (.+?)\. (?:Please specify one|Reply with the option number):"),
    },
    {
        "label": "GGACR",
        "prefix": "ggacr",
        "module": ggacr_module,
        "prompt_re": re.compile(r"Multiple GGACR moves match (.+?)\. (?:Please specify one|Reply with the option number):"),
    },
    {
        "label": "2XKO",
        "prefix": "2xko",
        "module": tuco_module,
        "prompt_re": re.compile(r"Multiple 2XKO moves match (.+?)\. (?:Please specify one|Reply with the option number):"),
    },
    {
        "label": "BBCF",
        "prefix": "bbcf",
        "module": bbcf_module,
        "prompt_re": re.compile(r"Multiple BBCF moves match (.+?)\. (?:Please specify one|Reply with the option number):"),
    },
    {
        "label": "COTW",
        "prefix": "cotw",
        "module": cotw_module,
        "prompt_re": re.compile(r"Multiple COTW moves match (.+?)\. (?:Please specify one|Reply with the option number):"),
    },
    {
        "label": "Third Strike",
        "prefix": "3s",
        "module": third_strike_module,
        "prompt_re": re.compile(r"Multiple Third Strike moves match (.+?)\. (?:Please specify one|Reply with the option number):"),
    },
    {
        "label": "USF4",
        "prefix": "usf4",
        "module": usfiv_module,
        "prompt_re": re.compile(r"Multiple USF4 moves match (.+?)\. (?:Please specify one|Reply with the option number):"),
    },
    {
        "label": "MK1",
        "prefix": "mk1",
        "module": mk1_module,
        "prompt_re": re.compile(r"Multiple MK1 moves match (.+?)\. (?:Please specify one|Reply with the option number):"),
    },
    {
        "label": "AVTL",
        "prefix": "avtl",
        "module": avtl_module,
        "prompt_re": re.compile(r"Multiple AVTL moves match (.+?)\. (?:Please specify one|Reply with the option number):"),
    },
]


# Numbered disambiguation reply parsing (GGST/SFV/etc follow-up prompts)
def _compact_disambiguation_text(text):
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _parse_disambiguation_options(prompt_text):
    options = []
    for raw_line in str(prompt_text or "").splitlines():
        line = raw_line.strip()
        option_match = re.match(r"^(?:(\d+)\s*[\.)]\s*|[\-•·]\s*)(.+?):\s*`([^`]+)`(?:\s*\[([^\]]+)\])?", line)
        if option_match:
            options.append(
                {
                    "number": int(option_match.group(1)) if option_match.group(1) else len(options) + 1,
                    "name": option_match.group(2).strip(),
                    "cmd": option_match.group(3).strip(),
                    "version": (option_match.group(4) or "").strip(),
                }
            )
    return options


def _disambiguation_reply_number(reply_text):
    text = str(reply_text or "").strip().lower()
    if not text:
        return None

    ordinal_words = {
        "first": 1,
        "second": 2,
        "third": 3,
        "fourth": 4,
        "fifth": 5,
        "sixth": 6,
        "seventh": 7,
        "eighth": 8,
        "ninth": 9,
        "tenth": 10,
        "eleventh": 11,
        "twelfth": 12,
    }
    digit_match = re.fullmatch(r"(?:#|number\s+|option\s+|pick\s+|choice\s+)?(\d+)(?:st|nd|rd|th)?(?:\s+one)?", text)
    if digit_match:
        return int(digit_match.group(1))
    word_match = re.fullmatch(
        r"(?:the\s+)?(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth)(?:\s+one)?",
        text,
    )
    if word_match:
        return ordinal_words.get(word_match.group(1))
    return None


def _select_disambiguation_option(reply_text, options):
    reply_compact = _compact_disambiguation_text(reply_text)
    if not reply_compact or not options:
        return None

    selected_number = _disambiguation_reply_number(reply_text)
    if selected_number is not None:
        number_matches = [option for option in options if option.get("number") == selected_number]
        if len(number_matches) == 1:
            return number_matches[0]

    exact_matches = []
    contains_matches = []
    for option in options:
        terms = {
            _compact_disambiguation_text(option.get("name")),
            _compact_disambiguation_text(option.get("cmd")),
            _compact_disambiguation_text(option.get("version")),
        }
        terms.discard("")
        if reply_compact in terms:
            exact_matches.append(option)
        elif any(reply_compact in term for term in terms):
            contains_matches.append(option)

    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(contains_matches) == 1:
        return contains_matches[0]
    return None


def _row_matches_disambiguation_option(row, option):
    row_name = _compact_disambiguation_text(row.get("moveName"))
    row_char_name = _compact_disambiguation_text(row.get("char_name"))
    row_cmd = _compact_disambiguation_text(row.get("numCmd"))
    row_version = _compact_disambiguation_text(row.get("version"))
    row_state_label = _compact_disambiguation_text(row.get("state_label"))
    option_name = _compact_disambiguation_text(option.get("name"))
    option_cmd = _compact_disambiguation_text(option.get("cmd"))
    option_version = _compact_disambiguation_text(option.get("version"))
    if option_name and row_name != option_name and option_name != f"{row_char_name}{row_name}":
        return False
    if option_cmd and row_cmd != option_cmd:
        return False
    if option_version and option_version not in {row_version, row_state_label}:
        return False
    return True


def _find_selected_disambiguation_row(rows, option):
    if not option:
        return None
    option_number = option.get("number")
    if isinstance(option_number, int) and 1 <= option_number <= len(rows or []):
        return list(rows or [])[option_number - 1]
    matches = [row for row in rows or [] if _row_matches_disambiguation_option(row, option)]
    return matches[0] if len(matches) == 1 else None


async def _fetch_referenced_message(message):
    if not message.reference or not message.reference.message_id:
        return None
    if message.reference.cached_message:
        return message.reference.cached_message
    return await message.channel.fetch_message(message.reference.message_id)


def _reply_output_mode_from_source_text(source_text):
    source_lower = normalize_query_terms(strip_discord_mentions(source_text or "").lower())
    wants_hitbox = bool(re.search(r"\b(?:gif|gifs|hitbox|hitboxes|image|images|picture|pictures)\b", source_lower))
    wants_frames = bool(re.search(r"\b(?:framedata|frame\s*data|frames?|data|notes?)\b", source_lower)) or not wants_hitbox
    if wants_hitbox and wants_frames:
        return "both"
    if wants_hitbox:
        return "gif"
    return "frame"


PROPERTY_VALUE_ALIASES = [
    ("startup", "Startup", ("startup",), r"\b(?:start\s*up|startup|how\s+fast|how\s+quick|speed\s+of)\b"),
    ("active", "Active", ("active",), r"\bactive(?:\s+frames?)?\b"),
    ("recovery", "Recovery", ("recovery",), r"\brecovery\b"),
    ("total", "Total", ("total",), r"\btotal(?:\s+frames?)?\b"),
    ("on_hit", "On Hit", ("onHit", "onODR"), r"\bon\s+hit\b"),
    ("on_block", "On Block", ("onBlock",), r"\bon\s+block\b|\bplus\s+on\s+block\b|\bminus\s+on\s+block\b"),
    ("flawless_block", "Flawless Block", ("flawlessBlock",), r"\bflawless\s+block\b"),
    ("damage", "Damage", ("dmg", "damage"), r"\b(?:damage|dmg)\b"),
    ("block_damage", "Block Damage", ("blockDamage",), r"\bblock\s+damage\b"),
    ("rev_damage", "REV Damage", ("revDamage",), r"\brev\s+damage\b"),
    ("guard_damage", "Guard Damage", ("guardDamage",), r"\bguard\s+damage\b"),
    ("guard", "Guard", ("guardLevel", "guard", "atkLvl"), r"\bguard\b"),
    ("attack_level", "Attack Level", ("atkLvl", "level"), r"\b(?:attack\s+level|atk\s*lvl|atk\s*level)\b"),
    ("cancel", "Cancel", ("cancel", "xx"), r"\bcancel(?:l?able)?\b"),
    ("gatling", "Gatling", ("gatling",), r"\bgatling\b"),
    ("invuln", "Invuln", ("invuln", "invul"), r"\binvuln(?:erability)?\b|\binvul\b"),
    ("attribute", "Attribute", ("attribute",), r"\battribute\b"),
    ("range", "Range", ("range",), r"\b(?:range|length)\b"),
    ("hitconfirm", "Hit Confirm Window", ("hcWinSpCa", "hcWinTc", "hcWinNotes"), r"\bhit\s*-?\s*confirm\b|\bhitconfirm\b|\bhc\b|\bconfirm\s+(?:window|timing)\b|\bconfirmable\b"),
    ("super_gain", "Super Gain", ("SelfSoH", "SelfSoB"), r"\bsuper\s*gain\b|\bsuper\s*meter\s*gain\b|\bsuper\s*build\b|\bsa\s*gain\b"),
    ("meter_gain", "Meter Gain", ("meterGain",), r"\bmeter\s*gain\b"),
    ("chip_damage", "Chip Damage", ("chp",), r"\bchip\s+damage\b"),
    ("drive_damage", "Drive Damage", ("DDoH", "DDoB"), r"\bdrive\s+(?:chip|dmg|damage)\b"),
    ("stun", "Stun", ("hitstun", "blockstun", "stun"), r"\bhitstun\b|\bblockstun\b|\bstun\b"),
    ("risc_gain", "RISC Gain", ("riscGain",), r"\brisc\s*gain\b|\brisc\b"),
    ("proration", "Proration", ("prorate",), r"\bproration\b|\bprorate\b"),
    ("knockdown_adv", "Knockdown Adv", ("kda",), r"\bknockdown\s+adv(?:antage)?\b|\bkda\b"),
    ("punish_counter", "Punish Counter", (), r"\bpunish\s*counter\b|\bpc\b"),
    ("counter_hit_adv", "Counter Hit", (), r"\bcounter\s*hit\s+adv(?:antage)?\b|\bch\s*adv\b"),
    ("counter_hit", "Counter Hit", (), r"\bcounter\s*hit\b|\bch\b"),
    ("frame_advantage", "Frame Advantage", (), r"\bframe\s+adv(?:antage)?\b"),
]

_PROPERTY_REQUEST_PRIORITY = (
    "punish_counter",
    "counter_hit_adv",
    "counter_hit",
    "frame_advantage",
)


def _requested_property_key(text):
    lowered = normalize_query_terms(text)
    if re.search(r"\b(?:all|full)\s+(?:frame\s*)?data\b|\btable\b", lowered):
        return None
    for key in _PROPERTY_REQUEST_PRIORITY:
        config = next((item for item in PROPERTY_VALUE_ALIASES if item[0] == key), None)
        if config and re.search(config[3], lowered):
            return key
    matches = [
        key for key, _label, _fields, pattern in PROPERTY_VALUE_ALIASES
        if key not in _PROPERTY_REQUEST_PRIORITY and re.search(pattern, lowered)
    ]
    if "damage" in matches and any(
        key in matches for key in ("block_damage", "guard_damage", "rev_damage", "chip_damage", "drive_damage")
    ):
        matches = [key for key in matches if key != "damage"]
    if "guard" in matches and "guard_damage" in matches:
        matches = [key for key in matches if key != "guard"]
    if "meter_gain" in matches and "super_gain" in matches:
        matches = [key for key in matches if key != "meter_gain"]
    return matches[0] if len(matches) == 1 else None


def _requested_property_key_after_move_resolution(text, rows):
    """Parse property intent after consuming resolved move names and commands."""
    remaining_tokens = word_tokens(normalize_query_terms(text))
    command_fields = {"numCmd", "input", "command", "notation"}
    identity_fields = ("moveName", "cmnName", "numCmd", "input", "command", "notation")

    for row in rows or []:
        candidates = set()
        for field in identity_fields:
            value = str(row.get(field) or "").strip()
            if not value:
                continue
            variants = {value}
            if field in command_fields:
                without_notes = re.sub(r"\s*[\[(].*?[\])]\s*", " ", value).strip()
                variants.add(without_notes)
                variants.update(
                    part.strip()
                    for part in re.split(r"\s*(?:/|\||\bor\b)\s*", without_notes, flags=re.IGNORECASE)
                    if part.strip()
                )
            for variant in variants:
                tokens = tuple(word_tokens(normalize_query_terms(variant)))
                if tokens:
                    candidates.add(tokens)
                compact = compact_key(variant)
                if compact:
                    candidates.add((compact,))

        for candidate in sorted(candidates, key=lambda tokens: (len(tokens), sum(map(len, tokens))), reverse=True):
            remaining_tokens, _removed = remove_first_token_sequence(remaining_tokens, list(candidate))

    return _requested_property_key(" ".join(remaining_tokens))


def _format_requested_property_reply(rows, property_key, *, game="sf6"):
    if not rows or not property_key:
        return None
    config = next((item for item in PROPERTY_VALUE_ALIASES if item[0] == property_key), None)
    if not config:
        return None
    _key, label, fields, _pattern = config
    lines = []
    for row in rows:
        if property_key == "range" and row.get("range") is not None:
            range_reply = format_range_only_reply([row])
            if range_reply:
                lines.append(range_reply)
                continue
        if property_key == "hitconfirm":
            hc_sp = str(row.get("hcWinSpCa") or "-").replace("*", ",").strip() or "-"
            hc_tc = str(row.get("hcWinTc") or "-").replace("*", ",").strip() or "-"
            hc_notes = str(row.get("hcWinNotes") or "-").replace("[", "").replace("]", "").replace('"', "").strip() or "-"
            value = f"Sp/Su: {hc_sp}, TC: {hc_tc}. Notes: {hc_notes}"
        elif game == "sf6" and property_key in {"frame_advantage", "counter_hit", "punish_counter", "counter_hit_adv"}:
            from bubbot.utils.sf6_advantage_utils import format_sf6_frame_advantage_value

            value = format_sf6_frame_advantage_value(row, mode=property_key)
        elif game != "sf6" and property_key == "frame_advantage":
            on_hit = str(row.get("onHit") or row.get("on_hit") or "-").replace("*", ",").strip() or "-"
            on_block = str(row.get("onBlock") or row.get("on_block") or "-").replace("*", ",").strip() or "-"
            value = f"On Hit: {on_hit}, On Block: {on_block}"
        elif property_key in {"super_gain", "drive_damage", "stun"} or (
            property_key == "meter_gain" and (row.get("SelfSoH") is not None or row.get("SelfSoB") is not None) and row.get("meterGain") is None
        ):
            hit_value = str(row.get(fields[0]) or "-").replace("*", ",").strip() or "-"
            block_value = str(row.get(fields[1]) or "-").replace("*", ",").strip() or "-"
            value = f"Hit: {hit_value}, Block: {block_value}"
        else:
            value = ""
            for field in fields:
                value = str(row.get(field) or "").strip()
                if value:
                    break
            if not value:
                value = "-"
        char_name = str(row.get("char_name") or row.get("char_key") or "Unknown").strip()
        move_name = str(row.get("moveName") or row.get("name") or row.get("numCmd") or "Unknown").strip()
        num_cmd = str(row.get("numCmd") or row.get("input") or "?").strip()
        suffix = "f" if property_key in {"startup", "active", "recovery", "total"} and any(ch.isdigit() for ch in value) and not value.endswith("f") else ""
        lines.append(f"{char_name}'s {move_name} ({num_cmd}) {label.lower()} is {value}{suffix}.")
    return truncate_message("\n".join(lines))


def _game_key_for_frame_module(module):
    if module is ggst_module:
        return "ggst"
    if module is sfv_module:
        return "sfv"
    if module is tuco_module:
        return "tuco"
    if module is bbcf_module:
        return "bbcf"
    if module is ggacr_module:
        return "ggacr"
    if module is cotw_module:
        return "cotw"
    if module is third_strike_module:
        return "third_strike"
    if module is usfiv_module:
        return "usf4"
    if module is mk1_module:
        return "mk1"
    if module is avtl_module:
        return "avtl"
    return "sf6"


def _property_reply_view(game, rows, property_key, owner_id, content):
    unique_rows = iter_unique_frame_rows(rows or [])
    if not unique_rows or not property_key or owner_id is None:
        return None
    char_key = menu_system._row_character_key(game, unique_rows[0])
    if not char_key:
        return None
    return PropertyValueView(game, char_key, unique_rows, property_key, owner_id, content)


async def _send_property_value_reply(message, rows, property_key, content=None, game="sf6"):
    property_reply = content or _format_requested_property_reply(rows, property_key, game=game)
    if not property_reply:
        return None
    view = _property_reply_view(game, rows, property_key, getattr(message.author, "id", None), property_reply)
    sent = await message.reply(property_reply, view=view)
    _record_frame_data_ids([sent.id])
    return sent


class PropertyValueView(discord.ui.View):
    def __init__(self, game, char_key, rows, property_key, owner_id, content):
        super().__init__(timeout=300)
        self.game = game
        self.char_key = char_key
        self.rows = iter_unique_frame_rows(rows or [])
        self.row = self.rows[0] if self.rows else None
        self.property_key = property_key
        self.owner_id = owner_id
        self.content = content
        self.add_item(PropertyCompareButton())
        self.add_item(PropertyFullFrameDataButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the person who opened this result can control it.", ephemeral=True)
            return False
        return True


class PropertyCompareButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Compare", style=ACTION, emoji=label_icon("Compare"))

    async def callback(self, interaction: discord.Interaction):
        parent = self.view
        moves = menu_system._move_list(parent.game, parent.char_key)
        if not moves:
            await interaction.response.send_message("No moves found for this character.", ephemeral=True)
            return
        # The reply is plain text, so the picker opens privately and edits the reply when a move is chosen.
        picker = PropertyCompareSelectView(parent, moves, page=0, reply=interaction.message)
        await interaction.response.send_message(view=picker, ephemeral=True)


class PropertyFullFrameDataButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Show Full Framedata", style=ACTION)

    async def callback(self, interaction: discord.Interaction):
        parent = self.view
        await interaction.response.defer()
        for row in parent.rows:
            row_char_key = menu_system._row_character_key(parent.game, row, parent.char_key)
            sent = await menu_system._send_frame_result_message(
                interaction.channel,
                parent.game,
                row_char_key or parent.char_key,
                row,
                parent.owner_id,
            )
            if sent:
                _record_frame_data_ids([sent.id])


class PropertyCompareSelectView(CardView):
    def __init__(self, parent_view, moves, page=0, reply=None):
        super().__init__(timeout=300)
        self.parent_view = parent_view
        self.moves = moves
        self.page = page
        self.reply = reply
        select = PropertyCompareSelect()
        self.add_item(select)
        select._refresh_options()
        page_count = self._page_count()
        self.previous_button.disabled = self.next_button.disabled = page_count == 1
        self.set_card(card_text(
            menu_system._character_display_name(parent_view.game, parent_view.char_key),
            f"Pick a move to compare {parent_view.property_key.replace('_', ' ')} with.",
            footer=f"Page {page + 1} of {page_count}" if page_count > 1 else None,
        ))

    def _page_count(self):
        return max(1, (len(self.moves) + 24) // 25)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.parent_view.owner_id:
            await interaction.response.send_message("Only the person who opened this result can control it.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Previous", style=ACTION, row=4)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._edit_page(interaction, self.page - 1 if self.page > 0 else self._page_count() - 1)

    @discord.ui.button(label="Next", style=ACTION, row=4)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._edit_page(interaction, self.page + 1 if self.page < self._page_count() - 1 else 0)

    @discord.ui.button(label="Close", style=ACTION, row=4)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=CardView().set_card("Compare closed."))

    async def _edit_page(self, interaction, new_page):
        picker = PropertyCompareSelectView(self.parent_view, self.moves, page=new_page, reply=self.reply)
        await interaction.response.edit_message(view=picker)


class PropertyCompareSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder="Select a move", options=[discord.SelectOption(label="Loading...", value="0")])

    def _refresh_options(self):
        parent_view = self.view
        start = parent_view.page * 25
        page_moves = parent_view.moves[start : start + 25]
        self.options = [
            discord.SelectOption(label=(label[:100] if len(label) > 100 else label), value=str(start + index))
            for index, (_row, label) in enumerate(page_moves)
        ] or [discord.SelectOption(label="No moves", value="none")]

    async def callback(self, interaction: discord.Interaction):
        self._refresh_options()
        if self.values[0] == "none":
            await interaction.response.send_message("No moves found for this character.", ephemeral=True)
            return
        idx = int(self.values[0])
        parent = self.view.parent_view
        row, label = self.view.moves[idx]
        next_rows = iter_unique_frame_rows(parent.rows + [row])
        property_reply = _format_requested_property_reply(next_rows, parent.property_key, game=parent.game)
        if not property_reply:
            await interaction.response.send_message("I could not format that value for the selected move.", ephemeral=True)
            return
        next_char_key = menu_system._row_character_key(parent.game, row, parent.char_key) or parent.char_key
        await interaction.response.edit_message(view=CardView().set_card(f"Added **{label}** to the comparison."))
        if self.view.reply is not None:
            await self.view.reply.edit(
                content=property_reply,
                view=PropertyValueView(parent.game, next_char_key, next_rows, parent.property_key, parent.owner_id, property_reply),
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        self._refresh_options()
        return True


async def _send_cross_game_lookup_response(message, module, rows, payload, query_text):
    media_query = bool(payload.get("gif_query") or has_explicit_gif_lookup_intent(query_text))
    if media_query and payload.get("frame_query"):
        _record_frame_data_ids(await module.send_frame_response(message, rows))
        _record_frame_data_ids(await module.send_hitbox_response(message, rows))
        return True
    if media_query:
        _record_frame_data_ids(await module.send_hitbox_response(message, rows))
        return True
    property_key = _requested_property_key_after_move_resolution(query_text, rows)
    if _format_requested_property_reply(rows, property_key, game=_game_key_for_frame_module(module)):
        await _send_property_value_reply(message, rows, property_key, game=_game_key_for_frame_module(module))
        return True
    _record_frame_data_ids(await module.send_frame_response(message, rows))
    return True


def _has_explicit_game_conflict(current_payload, game_payloads, *, explicit_sf6_query=False):
    return bool(
        explicit_sf6_query
        or any(
            payload is not current_payload and payload.get("game_query")
            for payload in game_payloads
        )
    )


def _cheap_explicit_frame_game_tags(content_lower):
    """Detect explicit game tags with regex only; avoids running full parsers.

    Mirrors each parser's game_query definition so early-exit skips the same
    games arbitration would reject via has_explicit_game_conflict.
    """
    text = str(content_lower or "")
    tags = set()
    if re.search(r"\b(?:sf6|street\s*fighter\s*6)\b", text):
        tags.add("sf6")
    if re.search(r"\b(?:ggst|guilty\s+gear|guilty|strive)\b", text):
        tags.add("ggst")
    try:
        if ggacr_module.query_has_explicit_ggacr_tag(text):
            tags.add("ggacr")
    except Exception:
        pass
    if re.search(r"\b(?:sfv|sf5|street\s*fighter\s*(?:v|5))\b", text):
        tags.add("sfv")
    if re.search(r"\b(?:2xko|tuco)\b", text):
        tags.add("tuco")
    if re.search(r"\b(?:bbcf|blazblue|central\s*fiction)\b", text):
        tags.add("bbcf")
    if re.search(r"\b(?:cotw|city\s+of\s+the\s+wolves|fatal\s+fury)\b", text):
        tags.add("cotw")
    if re.search(r"\b(?:3s|third\s*strike|street\s*fighter\s*(?:3|iii)|sf3|sfiii)\b", text):
        tags.add("third_strike")
    try:
        if usfiv_module.query_has_usf4_game_tag(text):
            tags.add("usfiv")
    except Exception:
        pass
    if re.search(r"\b(?:mk1|mortal\s+kombat\s+1|mortal\s+kombat\s+one|mortal\s+kombat)\b", text):
        tags.add("mk1")
    if re.search(r"\b(?:avtl|avatar\s*legends|avatar)\b", text):
        tags.add("avtl")
    return tags


def _reads_as_frame_data(text):
    """Frame data before SF Stats: another game's tag (SF Stats is SF6 only), or a move Bub's SF6 parser reads
    ("yasmine boomerang", "aki coward crouch"). A bare character comparison ("ken vs ryu") stays a matchup question."""
    text = decisions.request_text(text).lower()
    if _cheap_explicit_frame_game_tags(text) - {"sf6"}:
        return True
    payload = find_moves_in_text(text)
    return payload.get("mode") == "frame" and bool(payload.get("explicit_move_attempt") or not payload.get("wants_comparison"))


def _selected_frame_parser_subset(content_lower, exact_matches):
    """Return parser keys to run, or None to run all (preserve fuzzy fallback).

    Order of exact_matches: sf6, ggst, sfv, tuco, bbcf, ggacr, cotw,
    third_strike, usfiv, mk1, avtl. When any game is explicitly tagged, run only
    tagged games. Otherwise when any game has an exact character match, run
    only those games; per routing policy exact matches outrank other games'
    fuzzy matches, so skipped parsers could not have claimed the route.
    """
    explicit_tags = _cheap_explicit_frame_game_tags(content_lower)
    if explicit_tags:
        selected = set(explicit_tags)
        # GGST/GGACR share "guilty gear" wording; keep both when either hits
        # so Strive vs +R arbitration still sees both payloads.
        if "ggst" in selected or "ggacr" in selected:
            selected.update({"ggst", "ggacr"})
        return selected
    keys = ("sf6", "ggst", "sfv", "tuco", "bbcf", "ggacr", "cotw", "third_strike", "usfiv", "mk1", "avtl")
    if exact_matches and any(exact_matches):
        return {key for key, matched in zip(keys, exact_matches) if matched}
    return None


def _frame_resolver_games():
    """Bub's games for the Decisions fallback: key, label, the tag its parser reads, and the live table."""
    return [
        ("sf6", "Street Fighter 6", "sf6", FRAME_DATA),
        ("ggst", "Guilty Gear Strive", "ggst", ggst_module.GGST_FRAME_DATA),
        ("sfv", "Street Fighter V", "sfv", sfv_module.SFV_FRAME_DATA),
        ("tuco", "2XKO", "2xko", tuco_module.TUCO_FRAME_DATA),
        ("bbcf", "BlazBlue Central Fiction", "bbcf", bbcf_module.BBCF_FRAME_DATA),
        ("ggacr", "Guilty Gear XX Accent Core +R", "ggacr", ggacr_module.GGACR_FRAME_DATA),
        ("cotw", "Fatal Fury: City of the Wolves", "cotw", cotw_module.COTW_FRAME_DATA),
        ("third_strike", "Street Fighter III: 3rd Strike", "3s", third_strike_module.THIRD_STRIKE_FRAME_DATA),
        ("usfiv", "Ultra Street Fighter IV", "usf4", usfiv_module.USFIV_FRAME_DATA),
        ("mk1", "Mortal Kombat 1", "mk1", mk1_module.MK1_FRAME_DATA),
        ("avtl", "Avatar Legends", "avtl", avtl_module.AVTL_FRAME_DATA),
    ]


def _empty_frame_payload():
    return {
        "mode": "none",
        "rows": [],
        "data": "",
        "gif_query": False,
        "frame_query": False,
        "game_query": False,
        "notes_query": False,
        "char_found": False,
        "needs_disambiguation": False,
        "missing_scrolls_query": False,
        "explicit_move_attempt": False,
        "wants_comparison": False,
    }


async def _handle_cross_game_disambiguation_reply(message, content_no_mentions):
    if not message.reference:
        return False
    try:
        replied_msg = await _fetch_referenced_message(message)
    except (discord.NotFound, discord.Forbidden):
        return False
    if not replied_msg or replied_msg.author != client.user:
        return False

    replied_content = replied_msg.content or ""
    config = None
    char_hint = ""
    for candidate in DISAMBIGUATION_GAME_CONFIGS:
        prompt_match = candidate["prompt_re"].search(replied_content)
        if prompt_match:
            config = candidate
            char_hint = prompt_match.group(1).strip()
            break
    if not config:
        return False

    selected_option = _select_disambiguation_option(content_no_mentions, _parse_disambiguation_options(replied_content))
    if not selected_option:
        await message.reply(replied_content)
        return True

    source_text = ""
    if replied_msg.reference and replied_msg.reference.message_id:
        try:
            prompt_source = await _fetch_referenced_message(replied_msg)
            source_text = prompt_source.content if prompt_source else ""
        except (discord.NotFound, discord.Forbidden):
            source_text = ""

    output_mode = _reply_output_mode_from_source_text(source_text)
    module = config["module"]

    source_payload = module.find_moves_in_text(strip_discord_mentions(source_text).lower()) if source_text else {}
    selected_row = _find_selected_disambiguation_row(source_payload.get("rows", []) or [], selected_option)
    if selected_row:
        rows = [selected_row]
        payload = {"rows": rows}
    else:
        option_name = selected_option.get("name") or selected_option.get("cmd") or ""
        version_text = f" {selected_option.get('version')}" if selected_option.get("version") else ""
        reply_query = f"{config['prefix']} {char_hint} {option_name}{version_text}".strip()
        if config["label"] == "Third Strike" and third_strike_module.query_requests_genei_jin(source_text):
            reply_query = f"{reply_query} genei jin".strip()
        if output_mode == "gif":
            reply_query = f"{reply_query} hitbox".strip()
        elif output_mode == "both":
            reply_query = f"{reply_query} hitbox framedata".strip()
        else:
            reply_query = f"{reply_query} framedata".strip()
        payload = module.find_moves_in_text(reply_query.lower())
        rows = payload.get("rows", []) or []

    if payload.get("needs_disambiguation"):
        await message.reply(payload.get("data", f"Please specify which {config['label']} move you mean."))
    elif rows and output_mode == "gif":
        _record_frame_data_ids(await module.send_hitbox_response(message, rows))
    elif rows and output_mode == "both":
        _record_frame_data_ids(await module.send_frame_response(message, rows))
        _record_frame_data_ids(await module.send_hitbox_response(message, rows))
    elif rows and _format_requested_property_reply(
        rows,
        _requested_property_key_after_move_resolution(source_text, rows),
        game=_game_key_for_frame_module(module),
    ):
        await _send_property_value_reply(
            message,
            rows,
            _requested_property_key_after_move_resolution(source_text, rows),
            game=_game_key_for_frame_module(module),
        )
    elif rows:
        _record_frame_data_ids(await module.send_frame_response(message, rows))
    else:
        await message.reply(replied_content)
    return True


def _sf6_prompt_reply_deps():
    return {
        "FRAME_DATA": FRAME_DATA,
        "send_missing_hitbox_gif_reply": send_missing_hitbox_gif_reply,
        "normalize_char_name": normalize_char_name,
        "resolve_character_key": resolve_character_key,
        "lookup_frame_data": lookup_frame_data,
        "lookup_hitbox_gif_link": lookup_hitbox_gif_link,
        "collect_hitbox_gif_links_from_text": collect_hitbox_gif_links_from_text,
        "send_frame_table_response": send_frame_table_response,
        "send_gif_links_response": send_gif_links_response,
        "format_frame_data": format_frame_data,
        "find_moves_in_text": find_moves_in_text,
        "reply_and_log_response": _reply_and_log_response,
    }


async def _resolve_special_strength_reply_mode(replied_msg):
    mode = sf6_prompt_replies.get_special_strength_prompt_mode(replied_msg.id)
    if mode:
        return mode
    if not replied_msg.reference or not replied_msg.reference.message_id:
        return None
    try:
        prompt_source = await _fetch_referenced_message(replied_msg)
    except (discord.NotFound, discord.Forbidden):
        return None
    if not prompt_source:
        return None
    prompt_source_text = strip_discord_mentions(prompt_source.content or "").lower()
    if has_explicit_gif_lookup_intent(prompt_source_text):
        return "gif"
    return None


async def _handle_sf6_prompt_disambiguation_reply(message, content_no_mentions, *, gif_query=False):
    if not message.reference:
        return False
    try:
        replied_msg = await _fetch_referenced_message(message)
    except (discord.NotFound, discord.Forbidden):
        return False
    if not replied_msg or replied_msg.author != client.user:
        return False

    replied_content = _message_prompt_text(replied_msg)
    if not (
        "Special Strength Options" in replied_content
        or "Target Combo Options" in replied_content
    ):
        return False

    special_strength_reply_mode = None
    if "Special Strength Options" in replied_content:
        special_strength_reply_mode = await _resolve_special_strength_reply_mode(replied_msg)

    return await sf6_prompt_replies.handle_sf6_prompt_reply(
        _sf6_prompt_reply_deps(),
        message,
        replied_content,
        content_no_mentions,
        gif_query=gif_query,
        special_strength_reply_mode=special_strength_reply_mode,
    )


buenavista_extension.log_status()

# No privileged intents: the portal has them off (only the LLM bot needed them). Discord still sends the text of
# messages that @mention the bot, replies to it and DMs, which is every way people ask for frame data.
intents = discord.Intents.default()
intents.message_content = False
intents.members = False
intents.presences = False
intents.voice_states = False
# Raw gateway events carry SF Stats interactions to the web app unchanged; only needed when bridged.
client = discord.Client(intents=intents, enable_debug_events=sfstats_bridge.enabled())
# Do not rely on the application's portal defaults for bot-DM command availability.
tree = discord.app_commands.CommandTree(
    client,
    allowed_contexts=discord.app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True),
    allowed_installs=discord.app_commands.AppInstallationType(guild=True, user=True),
)
sfstats_bridge.install_tree_error_filter(tree)


def truncate_message(text, limit=1800):
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."







_BOSCH_BREAKDANCE_VIDEO_URL = (
    "https://cdn.discordapp.com/attachments/1345474577316319265/"
    "1520875333803577386/breakdance.mp4"
)


def strip_discord_mentions(content):
    if not content:
        return ""
    stripped = MENTION_PATTERN.sub(" ", content)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return stripped


def normalize_jump_normal_text(text):
    if not text:
        return ""
    strength_map = {
        "light": "l",
        "medium": "m",
        "heavy": "h",
    }
    button_map = {
        "punch": "p",
        "kick": "k",
    }

    def replace_named_jump(match):
        strength = match.group(2)
        button = match.group(3)
        short = f"{strength_map[strength]}{button_map[button]}"
        return f"8{short}"

    text = re.sub(
        r"\b(?:(neutral|n)\s+)?jump\s+(light|medium|heavy)\s+(punch|kick)\b",
        replace_named_jump,
        text,
    )
    text = re.sub(r"\b(?:neutral|n)\s+j\s*\.?\s*([lmh][pk])\b", r"8\1", text)
    text = re.sub(r"\bn\.?j\s*([lmh][pk])\b", r"8\1", text)
    text = re.sub(r"\bnj\s*([lmh][pk])\b", r"8\1", text)
    text = re.sub(r"\bj\s*\.?\s*([lmh][pk])\b", r"8\1", text)
    text = re.sub(r"\b(?:neutral|n)\s+jump\s+([lmh][pk])\b", r"8\1", text)
    text = re.sub(r"\bjump\s+([lmh][pk])\b", r"8\1", text)
    text = re.sub(r"\bn\.?j\s*\.?\s*([1-9][0-9]*[a-z]{1,3})\b", r"neutral j\1", text)
    text = re.sub(r"\bnj\s*([1-9][0-9]*[a-z]{1,3})\b", r"neutral j\1", text)
    text = re.sub(r"\bj\s*\.?\s*([1-9][0-9]*[a-z]{1,3})\b", r"j\1", text)
    return text










FRAME_DATA = {}
FRAME_STATS = {}
HITBOX_GIF_DATA = {}

def normalize_char_name(name: str) -> str:
    """Normalize character name to lowercase alphanumeric."""
    return compact_key(name)


def resolve_character_key(name: str):
    """Resolve free-form character text to a FRAME_DATA key."""
    return resolve_alias_key(name, CHARACTER_ALIASES, FRAME_DATA.keys(), normalize_fn=normalize_char_name)


def text_mentions_character_from_aliases(text, aliases, valid_keys):
    return text_mentions_alias(text, aliases, valid_keys)


def _preferred_character_game_matches(text, game_alias_specs):
    exact_matches = tuple(
        bool(find_aliases_in_text(text, aliases, valid_keys))
        for aliases, valid_keys in game_alias_specs
    )
    if any(exact_matches):
        return exact_matches
    return tuple(
        text_mentions_character_from_aliases(text, aliases, valid_keys)
        for aliases, valid_keys in game_alias_specs
    )


def resolve_character_from_aliases_in_text(text, aliases, valid_keys):
    matches = find_aliases_in_text(text, aliases, valid_keys)
    return matches[0] if matches else None


# SF6 loader/parser compat surface (root bot.py and regressions import these names)
def load_frame_data():
    """Compatibility wrapper for SF6 ODS loading."""
    sf6_loader.load_frame_data(
        {
            "FRAME_DATA": FRAME_DATA,
            "FRAME_STATS": FRAME_STATS,
            "HITBOX_GIF_DATA": HITBOX_GIF_DATA,
            "CHARACTER_ALIASES": CHARACTER_ALIASES,
            "normalize_char_name": normalize_char_name,
            "resolve_character_key": resolve_character_key,
            "lookup_frame_data": lookup_frame_data,
            "find_moves_in_text": find_moves_in_text,
            "is_missing_attack_range_value": is_missing_attack_range_value,
            "strip_discord_mentions": strip_discord_mentions,
            "configure_extracted_modules": configure_extracted_modules,
            "load_local_hitbox_gif_data": load_local_hitbox_gif_data,
            "quiz_module": quiz_module,
            "frame_output_module": frame_output_module,
        }
    )

def find_moves_in_text(text):
    return sf6_parser.find_moves_in_text(
        {
            "CHARACTER_ALIASES": CHARACTER_ALIASES,
            "FRAME_DATA": FRAME_DATA,
            "FRAME_STATS": FRAME_STATS,
            "strip_discord_mentions": strip_discord_mentions,
            "normalize_jump_normal_text": normalize_jump_normal_text,
            "word_tokens": word_tokens,
            "contains_token_sequence": contains_token_sequence,
            "has_explicit_gif_lookup_intent": has_explicit_gif_lookup_intent,
            "lookup_frame_data": lookup_frame_data,
            "normalize_char_name": normalize_char_name,
            "resolve_character_key": resolve_character_key,
            "normalize_num_cmd_token": normalize_num_cmd_token,
            "is_missing_attack_range_value": is_missing_attack_range_value,
            "get_attack_range_details": get_attack_range_details,
            "format_attack_range_for_table": format_attack_range_for_table,
            "format_frame_data": format_frame_data,
            "check_punish": check_punish,
        },
        text,
    )

def lookup_frame_data(character, move_input, _seen_inputs=None):
    return sf6_lookup.lookup_frame_data(
        {
            "FRAME_DATA": FRAME_DATA,
            "INPUT_ALIASES": INPUT_ALIASES,
            "CHARACTER_INPUT_ALIASES": CHARACTER_INPUT_ALIASES,
            "DP_PREFIX_EXCEPTIONS": DP_PREFIX_EXCEPTIONS,
            "normalize_jump_normal_text": normalize_jump_normal_text,
            "compact_key": compact_key,
            "compact_move_token": compact_move_token,
            "extract_button_suffix": extract_button_suffix,
            "normalize_num_cmd_token": normalize_num_cmd_token,
        },
        character,
        move_input,
        _seen_inputs=_seen_inputs,
    )

def check_punish(text_lower, results):
    """Calculate if Move B can punish Move A based on frame advantage."""
    # Only trigger on punish-related queries
    punish_keywords = ['punish', 'punishable', 'can i punish', 'is it punishable']
    if not any(kw in text_lower for kw in punish_keywords):
        return None
    
    # If only one move is identified, provide basic safety guidance
    if len(results) < 2:
        move_a = results[0] if results else None
        if not move_a:
            return None
        try:
            on_block_raw = str(move_a.get("onBlock", "0"))
            on_block_clean = on_block_raw.replace("+", "").strip()
            if not on_block_clean.lstrip("-").isdigit():
                return (
                    f"Cannot calculate punish: {move_a['moveName']} has non-numeric block advantage "
                    f"({on_block_raw})."
                )
            on_block = int(on_block_clean)
        except Exception as e:
            return f"Punish calculation error: {e}"

        move_a_name = f"{move_a.get('char_name', 'Unknown')}'s {move_a['moveName']}"
        frame_advantage = max(-on_block, 0)
        if on_block >= -3:
            return (
                f"**PUNISH CALCULATION**\n"
                f"{move_a_name} is **{on_block}** on block.\n\n"
                f"NO: This is **safe on block**. Moves that are -3 or better cannot be "
                f"punished by normal attacks."
            )
        return (
            f"**PUNISH CALCULATION**\n"
            f"{move_a_name} is **{on_block}** on block.\n\n"
            f"This is punishable **if** your move's startup is **≤{frame_advantage}f** and you're in range."
        )
    
    # Assume first move = blocked move (Move A), second = punish attempt (Move B)
    move_a = results[0]
    move_b = results[1]
    
    try:
        # Extract on_block from Move A (e.g. "-8")
        on_block_raw = str(move_a.get('onBlock', '0'))
        # Handle edge cases like "KD", "+5", "-8"
        on_block_clean = on_block_raw.replace('+', '').strip()
        if on_block_clean.lstrip('-').isdigit():
            on_block = int(on_block_clean)
        else:
            # Non-numeric (e.g. "KD") - can't calculate
            return f"Cannot calculate punish: {move_a['moveName']} has non-numeric block advantage ({on_block_raw})."
        
        # Extract startup from Move B (e.g. "5")
        startup_raw = str(move_b.get('startup', '0'))
        # Handle multi-hit like "3+5" - use first number
        startup_clean = startup_raw.split('+')[0].split('~')[0].split('(')[0].strip()
        if startup_clean.isdigit():
            startup = int(startup_clean)
        else:
            return f"Cannot calculate punish: {move_b['moveName']} has non-numeric startup ({startup_raw})."
        
        move_a_name = f"{move_a.get('char_name', 'Unknown')}'s {move_a['moveName']}"
        move_b_name = f"{move_b.get('char_name', 'Unknown')}'s {move_b['moveName']}"
        
        # Punish logic: defender frame advantage = -on_block (when negative)
        # If startup <= frame advantage, punishable (range still matters).
        frame_advantage = max(-on_block, 0)
        is_punishable = on_block <= -4 and frame_advantage >= startup
        if is_punishable:
            return (
                f"**PUNISH CALCULATION**\n"
                f"{move_a_name} is **{on_block}** on block.\n"
                f"{move_b_name} has **{startup}f startup**.\n\n"
                f"YES: This is punishable numerically speaking, "
                f"but my scrolls do not contain data on pushback so I cannot comment on range."
            )
        else:
            return (
                f"**PUNISH CALCULATION**\n"
                f"{move_a_name} is **{on_block}** on block.\n"
                f"{move_b_name} has **{startup}f startup**.\n\n"
                f"NO: {move_a_name} cannot be punished by {move_b_name}.\n"
                f"{move_b_name} startup must be **≤{frame_advantage}f** to punish, and the character must be in range."
            )
    except Exception as e:
        return f"Punish calculation error: {e}"

reminder_task_handle = None
_TYPING_DISABLED = False
_DATA_LOADED = False
# Set by bubbot.render_service: load game data only, never log in, sync commands or start schedulers.
FRAME_ONLY = False
# The commit the SF Stats helper runs (its deploy writes VERSION), reported by /health.
RENDER_VERSION = "dev"
reminder_manager = ReminderManager(
    client,
    truncate_message,
    buenavista_extension.build_reminder_ack_text,
    buenavista_extension.build_reminder_fire_text,
)


def check_quiz_answer(quiz_state, text):
    return quiz_module.check_quiz_answer(quiz_state, text)


compact_move_token = gif_lookup_module.compact_move_token
normalize_num_cmd_token = gif_lookup_module.normalize_num_cmd_token
extract_button_suffix = gif_lookup_module.extract_button_suffix
parse_local_hitbox_gif_filename = gif_lookup_module.parse_local_hitbox_gif_filename
load_local_hitbox_gif_data = gif_lookup_module.load_local_hitbox_gif_data
get_existing_local_gif_asset_paths = gif_lookup_module.get_existing_local_gif_asset_paths
normalize_move_name_for_gif_text = gif_lookup_module.normalize_move_name_for_gif_text
move_name_match_tokens = gif_lookup_module.move_name_match_tokens
build_num_cmd_candidates_for_gif = gif_lookup_module.build_num_cmd_candidates_for_gif
lookup_hitbox_gif_link = gif_lookup_module.lookup_hitbox_gif_link
collect_hitbox_gif_links = gif_lookup_module.collect_hitbox_gif_links
find_characters_in_text = gif_lookup_module.find_characters_in_text
remove_first_token_sequence = gif_lookup_module.remove_first_token_sequence
extract_gif_move_query_text = gif_lookup_module.extract_gif_move_query_text
resolve_hitbox_gif_query_alias = gif_lookup_module.resolve_hitbox_gif_query_alias
lookup_hitbox_gif_links_from_query = gif_lookup_module.lookup_hitbox_gif_links_from_query
collect_hitbox_gif_links_from_text = gif_lookup_module.collect_hitbox_gif_links_from_text

get_attack_range_details = frame_output_module.get_attack_range_details
format_attack_range_for_table = frame_output_module.format_attack_range_for_table
format_frame_data = frame_output_module.format_frame_data
format_property_only_lines = frame_output_module.format_property_only_lines
format_startup_only_reply = frame_output_module.format_startup_only_reply
format_hitconfirm_only_reply = frame_output_module.format_hitconfirm_only_reply
format_super_gain_only_reply = frame_output_module.format_super_gain_only_reply
format_range_only_reply = frame_output_module.format_range_only_reply
truncate_embed_value = frame_output_module.truncate_embed_value
is_missing_embed_value = frame_output_module.is_missing_embed_value
clean_embed_value = frame_output_module.clean_embed_value
add_embed_field = frame_output_module.add_embed_field
format_hit_block_value = frame_output_module.format_hit_block_value
build_frame_embed = frame_output_module.build_frame_embed
iter_unique_frame_rows = frame_output_module.iter_unique_frame_rows
build_frame_embeds = frame_output_module.build_frame_embeds
sanitize_embed_followup_text = frame_output_module.sanitize_embed_followup_text


def configure_extracted_modules():
    combo_data_module.configure(
        resolve_sf6_character_key=resolve_character_key,
        resolve_mk1_character_key=mk1_module.resolve_character_key,
        sf6_character_aliases=CHARACTER_ALIASES,
        mk1_character_aliases=mk1_module.MK1_CHARACTER_ALIASES,
    )
    gif_lookup_module.configure(
        CHARACTER_ALIASES=CHARACTER_ALIASES,
        FRAME_DATA=FRAME_DATA,
        HITBOX_GIF_DATA=HITBOX_GIF_DATA,
        LOCAL_HITBOX_GIF_ROOT=LOCAL_HITBOX_GIF_ROOT,
        LOCAL_HITBOX_GIF_EXTENSIONS=LOCAL_HITBOX_GIF_EXTENSIONS,
        normalize_char_name=normalize_char_name,
        resolve_character_key=resolve_character_key,
        iter_unique_frame_rows=frame_output_module.iter_unique_frame_rows,
        strip_discord_mentions=strip_discord_mentions,
        find_moves_in_text=find_moves_in_text,
        lookup_frame_data=lookup_frame_data,
        get_sf6_move_image_url=frame_output_module.get_sf6_move_image_url,
    )
    frame_output_module.configure(
        is_missing_attack_range_value=is_missing_attack_range_value,
        truncate_message=truncate_message,
        send_deleted_message_failsafe=send_deleted_message_failsafe,
        get_frame_row_gif_links=gif_lookup_module.get_frame_row_gif_links,
        get_existing_local_gif_asset_paths=gif_lookup_module.get_existing_local_gif_asset_paths,
        is_deleted_message_reference_error=is_deleted_message_reference_error,
        RANGE_SCROLLS_MISSING_TEXT=RANGE_SCROLLS_MISSING_TEXT,
        FRAME_STATS=FRAME_STATS,
        normalize_char_name=normalize_char_name,
    )


# NL combo routing before SF6 frame parser (SF6 Combos.ods + mk1/combos.json)
async def _try_route_combo_query(
    message,
    content_lower,
    *,
    frame_command_is_addressed,
    sf6_exact_character_query,
    mk1_exact_character_query,
):
    """Route NL combo queries before SF6 frame parsing (avoids unrelated parser deps)."""
    if not frame_command_is_addressed:
        return False
    if not re.search(r"\b(?:combo|combos|bnb|bnbs|route|routes)\b", content_lower):
        return False

    if combo_data_module._query_has_game_tag(content_lower, "mk1"):
        combo_games = ["mk1"]
    elif combo_data_module._query_has_game_tag(content_lower, "sf6"):
        combo_games = ["sf6"]
    elif mk1_exact_character_query and not sf6_exact_character_query:
        combo_games = ["mk1"]
    elif sf6_exact_character_query and not mk1_exact_character_query:
        combo_games = ["sf6"]
    else:
        combo_games = ["sf6", "mk1"]

    for game in combo_games:
        if not combo_data_module.has_combos(game):
            continue
        combo_payload = combo_data_module.find_combo_rows_in_text(game, content_lower)
        if combo_payload.get("combo_query") and combo_payload.get("char_found"):
            nav, _details = combo_data_module.combo_entry_nav(
                game,
                combo_payload["char_key"],
                group=combo_payload.get("group"),
                rows=combo_payload.get("rows"),
            )
            if nav != "empty":
                _record_frame_data_ids(
                    await combo_data_module.send_combo_entry(
                        message,
                        game,
                        combo_payload["char_key"],
                        combo_payload,
                        owner_id=getattr(message.author, "id", None),
                        back_to="game_menu",
                    )
                )
                return True
        if combo_payload.get("combo_query") and combo_payload.get("char_found"):
            char_label = combo_data_module.display_char_name(game, combo_payload["char_key"])
            await _reply_and_log_response(
                message,
                f"No combos found for {char_label} with those filters.",
                "missing_scrolls",
            )
            return True
    return False


def is_deleted_message_reference_error(error):
    if isinstance(error, discord.NotFound):
        return True
    if isinstance(error, discord.HTTPException):
        text = str(error).lower()
        if "message_reference" in text and "unknown message" in text:
            return True
    return False


async def send_deleted_message_failsafe(channel):
    try:
        await channel.send("That reply target disappeared, so I cannot attach the response there.")
    except Exception:
        pass


@client.event
async def on_socket_raw_receive(msg):
    await sfstats_bridge.handle_raw_gateway_event(msg)


@client.event
async def on_ready():
    global reminder_task_handle
    global _DATA_LOADED
    reminder_task_handle = await handle_ready(
        {
            "client": client,
            "tree": tree,
            "reminder_manager": reminder_manager,
            "reminder_task_handle": reminder_task_handle,
            "frame_only": FRAME_ONLY,
            "buenavista_extension": buenavista_extension,
            "load_frame_data": load_frame_data,
            "configure_extracted_modules": configure_extracted_modules,
            "quiz_module": quiz_module,
            "menu_system": menu_system,
            "frame_output_module": frame_output_module,
            "ggst_module": ggst_module,
            "sfv_module": sfv_module,
            "tuco_module": tuco_module,
            "bbcf_module": bbcf_module,
            "ggacr_module": ggacr_module,
            "cotw_module": cotw_module,
            "avtl_module": avtl_module,
            "third_strike_module": third_strike_module,
            "usfiv_module": usfiv_module,
            "mk1_module": mk1_module,
            "combo_data_module": combo_data_module,
            "FRAME_DATA": FRAME_DATA,
            "FRAME_STATS": FRAME_STATS,
            "CHARACTER_ALIASES": CHARACTER_ALIASES,
            "resolve_character_key": resolve_character_key,
            "normalize_char_name": normalize_char_name,
            "lookup_frame_data": lookup_frame_data,
            "find_moves_in_text": find_moves_in_text,
            "is_missing_attack_range_value": is_missing_attack_range_value,
            "clean_embed_value": clean_embed_value,
            "truncate_embed_value": truncate_embed_value,
            "build_frame_embed": build_frame_embed,
            "strip_discord_mentions": strip_discord_mentions,
            "send_frame_embeds_with_views": send_frame_embeds_with_views,
        }
    )
    _DATA_LOADED = True
    print("[startup] All game data loaded; message handling is now active.", flush=True)
    try:
        await render_server.start(client=client, handle_message=_handle_message, ready=lambda: _DATA_LOADED, version=RENDER_VERSION)
    except Exception as render_error:
        print(f"[bub-render] Not started: {type(render_error).__name__}: {render_error}", flush=True)

@client.event
async def on_message(message):
    try:
        await _handle_message(message)
    except Exception as e:
        print(f"[on_message] unhandled error: {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()


async def _handle_message(message):
    # ignore bot msgs
    if message.author == client.user:
        # The web app posts its reports of misread and failed requests as this bot: copy them to the error channel.
        await sfstats_bridge.copy_web_app_report(message, client)
        return

    if not _DATA_LOADED:
        return

    content_raw = message.content or ""
    content_no_mentions = strip_discord_mentions(content_raw)
    content_lower = content_no_mentions.lower()

    # Plain @bub or @bub sf6 opens menu without other text
    if is_plain_bot_mention_only(message):
        await _send_main_menu_for_plain_mention(message)
        return

    # SF Stats answers requests addressed to it and, when routing is on, the Bub mentions the
    # web app's Decisions call assigns to it. Everything else, and any failure, stays with Bub.
    # Rendering for SF Stats: the question already came from SF Stats, and only frame data answers.
    rendering = render_server.rendering()
    if not rendering and not frame_resolver.resolving() and await sfstats_bridge.claim_message(
            message, client, reads_as_frame_data=_reads_as_frame_data):
        return

    directly_mentions_bot = message_directly_mentions_bot(message)

    game_only = menu_system.parse_game_only_mention(content_no_mentions)
    if directly_mentions_bot and game_only:
        await menu_system.send_game_menu(message.channel, game_only, owner_id=message.author.id)
        try:
            log_record(
                build_log_record(
                    interaction_type=INTERACTION_MENU,
                    reason="game_mention_menu",
                    prompt=str(getattr(message, "content", "") or ""),
                    response_text=f"Opened {menu_system._game_label(game_only)} menu embed.",
                    response_kind="embed",
                    source_message=message,
                )
            )
        except Exception as log_error:
            print(f"Response log error: {log_error}", flush=True)
        return

    # Quiz beats frame lookup while a session is active (must be @bub or reply to quiz msg)
    quiz_result = False if rendering else await quiz_module.route_message(client, message, content_lower)
    if quiz_result is not False:
        return

    if directly_mentions_bot and "bosch breakdance" in content_lower:
        await message.reply(_BOSCH_BREAKDANCE_VIDEO_URL)
        return

    if await _handle_cross_game_disambiguation_reply(message, content_no_mentions):
        return

    if await _handle_sf6_prompt_disambiguation_reply(
        message,
        content_no_mentions,
        gif_query=has_explicit_gif_lookup_intent(content_lower),
    ):
        return

    if not rendering and await buenavista_extension.maybe_handle_voice_command(
        client=client,
        message=message,
        content_lower=content_lower,
    ):
        return

    if not rendering and await buenavista_extension.maybe_handle_streetfighterdle_message(
        client=client,
        message=message,
        content_lower=content_lower,
    ):
        return

    if await maybe_handle_glossary_lookup(message, content_no_mentions, addressed=directly_mentions_bot):
        return

    if not rendering:
        await buenavista_extension.maybe_ack_streetfighterdle_score(message)

    if await _is_reply_to_suppressed_bub_message(message):
        return

    # @bub menu and @bub {char} moves shortcuts
    if directly_mentions_bot and content_lower.strip() == "menu":
        await menu_system.send_main_menu(message.channel, owner_id=message.author.id)
        return

    if directly_mentions_bot and re.fullmatch(r"\s*(?:.+\s+)?moves\s*", content_lower):
        sf6_char_key = resolve_character_from_aliases_in_text(
            content_lower,
            CHARACTER_ALIASES,
            FRAME_DATA.keys(),
        )
        ggst_char_key = resolve_character_from_aliases_in_text(
            content_lower,
            ggst_module.GGST_CHARACTER_ALIASES,
            ggst_module.GGST_FRAME_DATA.keys(),
        )
        sfv_char_key = resolve_character_from_aliases_in_text(
            content_lower,
            sfv_module.SFV_CHARACTER_ALIASES,
            sfv_module.SFV_FRAME_DATA.keys(),
        )
        tuco_char_key = resolve_character_from_aliases_in_text(
            content_lower,
            tuco_module.TUCO_CHARACTER_ALIASES,
            tuco_module.TUCO_FRAME_DATA.keys(),
        )
        bbcf_char_key = resolve_character_from_aliases_in_text(
            content_lower,
            bbcf_module.BBCF_CHARACTER_ALIASES,
            bbcf_module.BBCF_FRAME_DATA.keys(),
        )
        ggacr_char_key = resolve_character_from_aliases_in_text(
            content_lower,
            ggacr_module.GGACR_CHARACTER_ALIASES,
            ggacr_module.GGACR_FRAME_DATA.keys(),
        )
        cotw_char_key = resolve_character_from_aliases_in_text(
            content_lower,
            cotw_module.COTW_CHARACTER_ALIASES,
            cotw_module.COTW_FRAME_DATA.keys(),
        )
        third_strike_char_key = resolve_character_from_aliases_in_text(
            content_lower,
            third_strike_module.THIRD_STRIKE_CHARACTER_ALIASES,
            third_strike_module.THIRD_STRIKE_FRAME_DATA.keys(),
        )
        usfiv_char_key = resolve_character_from_aliases_in_text(
            content_lower,
            usfiv_module.USFIV_CHARACTER_ALIASES,
            usfiv_module.USFIV_FRAME_DATA.keys(),
        )
        avtl_char_key = resolve_character_from_aliases_in_text(
            content_lower,
            avtl_module.AVTL_CHARACTER_ALIASES,
            avtl_module.AVTL_FRAME_DATA.keys(),
        )
        explicit_ggacr_moves_query = ggacr_module.query_has_explicit_ggacr_tag(content_lower)
        explicit_ggst_moves_query = bool(
            re.search(r"\b(?:ggst|strive|guilty\s+gear|guilty)\b", content_lower)
            and not explicit_ggacr_moves_query
        )
        explicit_sfv_moves_query = bool(re.search(r"\b(?:sfv|sf5|street\s*fighter\s*(?:v|5))\b", content_lower))
        explicit_tuco_moves_query = bool(re.search(r"\b(?:2xko|tuco)\b", content_lower))
        explicit_bbcf_moves_query = bool(re.search(r"\b(?:bbcf|blazblue|central\s*fiction)\b", content_lower))
        explicit_cotw_moves_query = bool(re.search(r"\b(?:cotw|city\s+of\s+the\s+wolves|fatal\s+fury)\b", content_lower))
        explicit_third_strike_moves_query = bool(re.search(r"\b(?:3s|third\s*strike|street\s*fighter\s*(?:3|iii)|sf3|sfiii)\b", content_lower))
        explicit_usfiv_moves_query = usfiv_module.query_has_usf4_game_tag(content_lower)
        explicit_avtl_moves_query = avtl_module.query_has_avtl_game_tag(content_lower)
        if explicit_avtl_moves_query and avtl_char_key:
            await menu_system.send_character_moves_menu(message.channel, "avtl", avtl_char_key, owner_id=message.author.id)
            return
        if explicit_usfiv_moves_query and usfiv_char_key:
            await menu_system.send_character_moves_menu(message.channel, "usf4", usfiv_char_key, owner_id=message.author.id)
            return
        if explicit_third_strike_moves_query and third_strike_char_key:
            await menu_system.send_character_moves_menu(message.channel, "third_strike", third_strike_char_key, owner_id=message.author.id)
            return
        if explicit_cotw_moves_query and cotw_char_key:
            await menu_system.send_character_moves_menu(message.channel, "cotw", cotw_char_key, owner_id=message.author.id)
            return
        if explicit_bbcf_moves_query and bbcf_char_key:
            await menu_system.send_character_moves_menu(message.channel, "bbcf", bbcf_char_key, owner_id=message.author.id)
            return
        if explicit_ggacr_moves_query and ggacr_char_key:
            await menu_system.send_character_moves_menu(message.channel, "ggacr", ggacr_char_key, owner_id=message.author.id)
            return
        if explicit_tuco_moves_query and tuco_char_key:
            await menu_system.send_character_moves_menu(message.channel, "tuco", tuco_char_key, owner_id=message.author.id)
            return
        if explicit_ggst_moves_query and ggst_char_key:
            await menu_system.send_character_moves_menu(message.channel, "ggst", ggst_char_key, owner_id=message.author.id)
            return
        if explicit_sfv_moves_query and sfv_char_key:
            await menu_system.send_character_moves_menu(message.channel, "sfv", sfv_char_key, owner_id=message.author.id)
            return
        if sf6_char_key:
            await menu_system.send_character_moves_menu(message.channel, "sf6", sf6_char_key, owner_id=message.author.id)
            return
        if ggacr_char_key and ggacr_module.is_exclusive_character(ggacr_char_key):
            await menu_system.send_character_moves_menu(message.channel, "ggacr", ggacr_char_key, owner_id=message.author.id)
            return
        if ggst_char_key:
            await menu_system.send_character_moves_menu(message.channel, "ggst", ggst_char_key, owner_id=message.author.id)
            return
        if sfv_char_key:
            await menu_system.send_character_moves_menu(message.channel, "sfv", sfv_char_key, owner_id=message.author.id)
            return
        if tuco_char_key:
            await menu_system.send_character_moves_menu(message.channel, "tuco", tuco_char_key, owner_id=message.author.id)
            return
        if bbcf_char_key:
            await menu_system.send_character_moves_menu(message.channel, "bbcf", bbcf_char_key, owner_id=message.author.id)
            return
        if cotw_char_key:
            await menu_system.send_character_moves_menu(message.channel, "cotw", cotw_char_key, owner_id=message.author.id)
            return
        if avtl_char_key:
            await menu_system.send_character_moves_menu(message.channel, "avtl", avtl_char_key, owner_id=message.author.id)
            return
        if third_strike_char_key:
            await menu_system.send_character_moves_menu(message.channel, "third_strike", third_strike_char_key, owner_id=message.author.id)
            return

    # User reminders (public) then BV catchphrase keyword replies
    if not rendering and await reminder_manager.handle_message(message, content_no_mentions, content_lower):
        return

    if not rendering and await buenavista_extension.maybe_handle_private_message(
        client=client,
        message=message,
        content_no_mentions=content_no_mentions,
        content_lower=content_lower,
    ):
        return

    if await _is_reply_to_frame_data(message):
        return

    if message.reference:
        try:
            if message.reference.cached_message:
                ggst_replied_msg = message.reference.cached_message
            else:
                ggst_replied_msg = await message.channel.fetch_message(message.reference.message_id)

            ggst_replied_content = ggst_replied_msg.content or ""
            if (
                ggst_replied_msg.author == client.user
                and (
                    "Multiple GGST moves match" in ggst_replied_content
                    or "GGST Follow-up Options" in ggst_replied_content
                )
            ):
                ggst_char_match = re.search(
                    r"Multiple GGST moves match ([^.]+)\. (?:Please specify one|Reply with the option number):",
                    ggst_replied_content,
                )
                if not ggst_char_match:
                    ggst_char_match = re.search(r"GGST Follow-up Options \(([^)]+)\)", ggst_replied_content)
                ggst_char_hint = ggst_char_match.group(1).strip() if ggst_char_match else ""
                ggst_reply_query = (content_no_mentions or "").strip()

                if "GGST Follow-up Options" in ggst_replied_content:
                    reply_compact = re.sub(r"[^a-z0-9]", "", ggst_reply_query.lower())
                    followup_options = []
                    for raw_line in ggst_replied_content.splitlines():
                        line = raw_line.strip()
                        option_match = re.match(r"^(?:\d+\s*[\.)]\s*|[\-•·]\s*)(.+?):\s*`([^`]+)`", line)
                        if option_match:
                            followup_options.append((option_match.group(1).strip(), option_match.group(2).strip()))
                    selected_followup_cmd = None
                    if reply_compact and followup_options:
                        suffix_matches = []
                        for option_name, option_cmd in followup_options:
                            option_name_compact = re.sub(r"[^a-z0-9]", "", option_name.lower())
                            option_cmd_compact = re.sub(r"[^a-z0-9]", "", option_cmd.lower())
                            command_parts = [
                                part for part in re.split(r"(?:>|~|during|after)", option_cmd.lower())
                                if part.strip()
                            ]
                            suffix_compacts = {
                                re.sub(r"[^a-z0-9]", "", part)
                                for part in command_parts[1:]
                                if re.sub(r"[^a-z0-9]", "", part)
                            }
                            if re.search(r"\b(?:during|after)\b", option_cmd.lower()) and command_parts:
                                first_part_compact = re.sub(r"[^a-z0-9]", "", command_parts[0])
                                if first_part_compact:
                                    suffix_compacts.add(first_part_compact)
                            if reply_compact in {option_name_compact, option_cmd_compact}:
                                selected_followup_cmd = option_cmd
                                break
                            if reply_compact in suffix_compacts:
                                suffix_matches.append(option_cmd)
                        if selected_followup_cmd is None and len(suffix_matches) == 1:
                            selected_followup_cmd = suffix_matches[0]
                    if selected_followup_cmd:
                        ggst_reply_query = selected_followup_cmd

                if ggst_char_hint and ggst_char_hint.lower() not in ggst_reply_query.lower():
                    ggst_reply_query = f"{ggst_char_hint} {ggst_reply_query}".strip()

                ggst_reply_mode = "frame"
                if ggst_replied_msg.reference and ggst_replied_msg.reference.message_id:
                    try:
                        if ggst_replied_msg.reference.cached_message:
                            ggst_prompt_source = ggst_replied_msg.reference.cached_message
                        else:
                            ggst_prompt_source = await message.channel.fetch_message(ggst_replied_msg.reference.message_id)
                        ggst_prompt_source_text = strip_discord_mentions(ggst_prompt_source.content or "").lower()
                        ggst_source_wants_hitbox = bool(re.search(r"\b(?:gif|gifs|hitbox|hitboxes)\b", ggst_prompt_source_text))
                        ggst_source_wants_frames = bool(re.search(r"\b(?:framedata|frame\s*data|frames?)\b", ggst_prompt_source_text))
                        if ggst_source_wants_hitbox and ggst_source_wants_frames:
                            ggst_reply_mode = "both"
                        elif ggst_source_wants_hitbox:
                            ggst_reply_mode = "gif"
                    except Exception:
                        pass

                if ggst_reply_mode == "gif":
                    ggst_reply_query = f"{ggst_reply_query} hitbox".strip()
                elif ggst_reply_mode == "both":
                    ggst_reply_query = f"{ggst_reply_query} hitbox framedata".strip()
                elif not re.search(r"\b(?:framedata|frame\s*data|frames?)\b", ggst_reply_query.lower()):
                    ggst_reply_query = f"{ggst_reply_query} framedata".strip()

                ggst_reply_payload = ggst_module.find_moves_in_text(ggst_reply_query.lower())
                ggst_reply_rows = ggst_reply_payload.get("rows", []) or []
                if ggst_reply_payload.get("needs_disambiguation"):
                    await message.reply(ggst_reply_payload.get("data", "Please specify which GGST move you mean."))
                elif ggst_reply_rows and ggst_reply_mode == "gif":
                    _record_frame_data_ids(await ggst_module.send_hitbox_response(message, ggst_reply_rows))
                elif ggst_reply_rows and ggst_reply_mode == "both":
                    _record_frame_data_ids(await ggst_module.send_frame_response(message, ggst_reply_rows))
                    _record_frame_data_ids(await ggst_module.send_hitbox_response(message, ggst_reply_rows))
                elif ggst_reply_rows:
                    _record_frame_data_ids(await ggst_module.send_frame_response(message, ggst_reply_rows))
                else:
                    await message.reply(ggst_replied_msg.content)
                return
        except discord.NotFound:
            pass
        except discord.Forbidden:
            pass
        except Exception as e:
            print(f"GGST reply logic error: {e}", flush=True)

    if message.reference:
        try:
            if message.reference.cached_message:
                third_strike_replied_msg = message.reference.cached_message
            else:
                third_strike_replied_msg = await message.channel.fetch_message(message.reference.message_id)

            third_strike_replied_content = third_strike_replied_msg.content or ""
            if (
                third_strike_replied_msg.author == client.user
                and "Multiple Third Strike moves match" in third_strike_replied_content
            ):
                third_strike_char_match = re.search(
                    r"Multiple Third Strike moves match ([^.]+)\. (?:Please specify one|Reply with the option number):",
                    third_strike_replied_content,
                )
                third_strike_char_hint = third_strike_char_match.group(1).strip() if third_strike_char_match else ""
                reply_text = (content_no_mentions or "").strip()
                reply_compact = re.sub(r"[^a-z0-9]", "", reply_text.lower())
                options = []
                for raw_line in third_strike_replied_content.splitlines():
                    line = raw_line.strip()
                    option_match = re.match(r"^(?:\d+\s*[\.)]\s*|[\-•·]\s*)(.+?):\s*`([^`]+)`(?:\s*\[([^\]]+)\])?", line)
                    if option_match:
                        options.append(
                            (
                                option_match.group(1).strip(),
                                option_match.group(2).strip(),
                                (option_match.group(3) or "").strip(),
                            )
                        )

                selected_option = None
                if reply_compact and options:
                    exact_matches = []
                    contains_matches = []
                    for option_name, option_cmd, option_version in options:
                        option_name_compact = re.sub(r"[^a-z0-9]", "", option_name.lower())
                        option_cmd_compact = re.sub(r"[^a-z0-9]", "", option_cmd.lower())
                        option_version_compact = re.sub(r"[^a-z0-9]", "", option_version.lower())
                        option_terms = {
                            option_name_compact,
                            option_cmd_compact,
                            option_version_compact,
                        }
                        option_terms.discard("")
                        if reply_compact in option_terms:
                            exact_matches.append((option_name, option_cmd, option_version))
                        elif any(reply_compact in term for term in option_terms):
                            contains_matches.append((option_name, option_cmd, option_version))
                    if len(exact_matches) == 1:
                        selected_option = exact_matches[0]
                    elif len(contains_matches) == 1:
                        selected_option = contains_matches[0]

                if selected_option and third_strike_char_hint:
                    _option_name, option_cmd, option_version = selected_option
                    version_text = f" {option_version}" if option_version else ""
                    source_wants_hitbox = False
                    source_wants_frames = True
                    prompt_source_text = ""
                    if third_strike_replied_msg.reference and third_strike_replied_msg.reference.message_id:
                        try:
                            if third_strike_replied_msg.reference.cached_message:
                                prompt_source = third_strike_replied_msg.reference.cached_message
                            else:
                                prompt_source = await message.channel.fetch_message(third_strike_replied_msg.reference.message_id)
                            prompt_source_text = strip_discord_mentions(prompt_source.content or "").lower()
                            source_wants_hitbox = bool(re.search(r"\b(?:gif|gifs|hitbox|hitboxes)\b", prompt_source_text))
                            source_wants_frames = bool(re.search(r"\b(?:framedata|frame\s*data|frames?|data)\b", prompt_source_text)) or not source_wants_hitbox
                        except Exception:
                            pass
                    third_strike_reply_query = f"3s {third_strike_char_hint} {option_cmd}{version_text}".strip()
                    if third_strike_module.query_requests_genei_jin(prompt_source_text):
                        third_strike_reply_query = f"{third_strike_reply_query} genei jin".strip()
                    if source_wants_hitbox:
                        third_strike_reply_query = f"{third_strike_reply_query} hitbox".strip()
                    if source_wants_frames:
                        third_strike_reply_query = f"{third_strike_reply_query} framedata".strip()
                    third_strike_reply_payload = third_strike_module.find_moves_in_text(third_strike_reply_query.lower())
                    third_strike_reply_rows = third_strike_reply_payload.get("rows", []) or []
                    if third_strike_reply_payload.get("needs_disambiguation"):
                        await message.reply(third_strike_reply_payload.get("data", "Please specify which Third Strike move you mean."))
                    elif third_strike_reply_rows and source_wants_hitbox and source_wants_frames:
                        _record_frame_data_ids(await third_strike_module.send_frame_response(message, third_strike_reply_rows))
                        _record_frame_data_ids(await third_strike_module.send_hitbox_response(message, third_strike_reply_rows))
                    elif third_strike_reply_rows and source_wants_hitbox:
                        _record_frame_data_ids(await third_strike_module.send_hitbox_response(message, third_strike_reply_rows))
                    elif third_strike_reply_rows:
                        _record_frame_data_ids(await third_strike_module.send_frame_response(message, third_strike_reply_rows))
                    else:
                        await message.reply(third_strike_replied_content)
                else:
                    await message.reply(third_strike_replied_content)
                return
        except discord.NotFound:
            pass
        except discord.Forbidden:
            pass
        except Exception as e:
            print(f"Third Strike reply logic error: {e}", flush=True)

    (
        sf6_exact_character_query,
        ggst_exact_character_query,
        sfv_exact_character_query,
        tuco_exact_character_query,
        bbcf_exact_character_query,
        ggacr_exact_character_query,
        cotw_exact_character_query,
        third_strike_exact_character_query,
        usfiv_exact_character_query,
        mk1_exact_character_query,
        avtl_exact_character_query,
    ) = _preferred_character_game_matches(
        content_lower,
        (
            (CHARACTER_ALIASES, FRAME_DATA.keys()),
            (ggst_module.GGST_CHARACTER_ALIASES, ggst_module.GGST_FRAME_DATA.keys()),
            (sfv_module.SFV_CHARACTER_ALIASES, sfv_module.SFV_FRAME_DATA.keys()),
            (tuco_module.TUCO_CHARACTER_ALIASES, tuco_module.TUCO_FRAME_DATA.keys()),
            (bbcf_module.BBCF_CHARACTER_ALIASES, bbcf_module.BBCF_FRAME_DATA.keys()),
            (ggacr_module.GGACR_CHARACTER_ALIASES, ggacr_module.GGACR_FRAME_DATA.keys()),
            (cotw_module.COTW_CHARACTER_ALIASES, cotw_module.COTW_FRAME_DATA.keys()),
            (third_strike_module.THIRD_STRIKE_CHARACTER_ALIASES, third_strike_module.THIRD_STRIKE_FRAME_DATA.keys()),
            (usfiv_module.USFIV_CHARACTER_ALIASES, usfiv_module.USFIV_FRAME_DATA.keys()),
            (mk1_module.MK1_CHARACTER_ALIASES, mk1_module.MK1_FRAME_DATA.keys()),
            (avtl_module.AVTL_CHARACTER_ALIASES, avtl_module.AVTL_FRAME_DATA.keys()),
        ),
    )
    explicit_ggacr_query = ggacr_module.query_has_explicit_ggacr_tag(content_lower)
    ggacr_exclusive_character_query = bool(
        ggacr_exact_character_query
        and ggacr_module.is_exclusive_character(
            resolve_character_from_aliases_in_text(
                content_lower,
                ggacr_module.GGACR_CHARACTER_ALIASES,
                ggacr_module.GGACR_FRAME_DATA.keys(),
            )
        )
    )
    message_replies_to_bot = False
    if message.reference:
        try:
            addressed_replied_msg = await _fetch_referenced_message(message)
            message_replies_to_bot = bool(addressed_replied_msg and addressed_replied_msg.author == client.user)
        except (discord.NotFound, discord.Forbidden):
            message_replies_to_bot = False
        except Exception as reply_check_error:
            print(f"Frame route reply check error: {reply_check_error}", flush=True)
    frame_command_is_addressed = bool(
        directly_mentions_bot
        or message_replies_to_bot
    )
    allow_implied_frame_routing = bool(
        not message.reference
        or message_replies_to_bot
        or directly_mentions_bot
    )

    # Cross-game frame routing: combos first, then per-game find_moves_in_text blocks below
    if await _try_route_combo_query(
        message,
        content_lower,
        frame_command_is_addressed=frame_command_is_addressed,
        sf6_exact_character_query=sf6_exact_character_query,
        mk1_exact_character_query=mk1_exact_character_query,
    ):
        return

    exact_match_tuple = (
        sf6_exact_character_query,
        ggst_exact_character_query,
        sfv_exact_character_query,
        tuco_exact_character_query,
        bbcf_exact_character_query,
        ggacr_exact_character_query,
        cotw_exact_character_query,
        third_strike_exact_character_query,
        usfiv_exact_character_query,
        mk1_exact_character_query,
        avtl_exact_character_query,
    )
    selected_parsers = _selected_frame_parser_subset(content_lower, exact_match_tuple)

    def _run_frame_parser(key, runner):
        if selected_parsers is not None and key not in selected_parsers:
            return _empty_frame_payload()
        return runner()

    fd_context_payload = _run_frame_parser("sf6", lambda: find_moves_in_text(content_lower))

    ggacr_payload = _run_frame_parser("ggacr", lambda: ggacr_module.find_moves_in_text(content_lower))
    ggst_payload = _run_frame_parser("ggst", lambda: ggst_module.find_moves_in_text(content_lower))
    sfv_payload = _run_frame_parser("sfv", lambda: sfv_module.find_moves_in_text(content_lower))
    tuco_payload = _run_frame_parser("tuco", lambda: tuco_module.find_moves_in_text(content_lower))
    bbcf_payload = _run_frame_parser("bbcf", lambda: bbcf_module.find_moves_in_text(content_lower))
    cotw_payload = _run_frame_parser("cotw", lambda: cotw_module.find_moves_in_text(content_lower))
    third_strike_payload = _run_frame_parser("third_strike", lambda: third_strike_module.find_moves_in_text(content_lower))
    usfiv_payload = _run_frame_parser("usfiv", lambda: usfiv_module.find_moves_in_text(content_lower))
    mk1_payload = _run_frame_parser("mk1", lambda: mk1_module.find_moves_in_text(content_lower))
    avtl_payload = _run_frame_parser("avtl", lambda: avtl_module.find_moves_in_text(content_lower))
    game_payloads = (
        ggacr_payload,
        ggst_payload,
        sfv_payload,
        tuco_payload,
        bbcf_payload,
        cotw_payload,
        third_strike_payload,
        usfiv_payload,
        mk1_payload,
        avtl_payload,
    )
    explicit_sf6_query = bool(re.search(r"\b(?:sf6|street\s*fighter\s*6)\b", content_lower))
    media_query = has_explicit_gif_lookup_intent(content_lower)

    def has_explicit_game_conflict(payload):
        return _has_explicit_game_conflict(
            payload,
            game_payloads,
            explicit_sf6_query=explicit_sf6_query,
        )

    requested_property_key = _requested_property_key(content_lower)
    ggst_rows = ggst_payload.get("rows", [])
    ggst_lookup_intent = bool(
        ggst_payload.get("frame_query")
        or ggst_payload.get("gif_query")
        or media_query
        or ggst_payload.get("game_query")
        or requested_property_key
    )
    explicit_ggst_query = bool(ggst_payload.get("game_query"))
    ggst_route_allowed = bool(
        explicit_ggst_query
        and not explicit_ggacr_query
        or (
            ggst_exact_character_query
            and not has_explicit_game_conflict(ggst_payload)
            and not sf6_exact_character_query
            and not explicit_ggacr_query
            and not ggacr_exclusive_character_query
        )
    )
    ggacr_rows = ggacr_payload.get("rows", [])
    usfiv_rows = usfiv_payload.get("rows", [])
    explicit_other_game_query = has_explicit_game_conflict(usfiv_payload)
    usfiv_exclusive_character_query = bool(
        usfiv_exact_character_query
        and not explicit_other_game_query
        and not any(
            (
                sf6_exact_character_query,
                ggst_exact_character_query,
                sfv_exact_character_query,
                tuco_exact_character_query,
                bbcf_exact_character_query,
                ggacr_exact_character_query,
                cotw_exact_character_query,
                third_strike_exact_character_query,
                mk1_exact_character_query,
            )
        )
    )
    usfiv_lookup_intent = bool(
        usfiv_payload.get("frame_query")
        or usfiv_payload.get("gif_query")
        or media_query
        or usfiv_payload.get("game_query")
        or usfiv_payload.get("notes_query")
        or requested_property_key
        or usfiv_module.query_has_usf4_notation(content_lower)
    )
    usfiv_route_allowed = bool(usfiv_payload.get("game_query") or usfiv_exclusive_character_query)
    if frame_command_is_addressed and usfiv_route_allowed and usfiv_lookup_intent and usfiv_rows:
        if usfiv_payload.get("needs_disambiguation"):
            await message.reply(usfiv_payload.get("data", "Please specify which USF4 move you mean."))
        else:
            await _send_cross_game_lookup_response(message, usfiv_module, usfiv_rows, usfiv_payload, content_lower)
        return
    elif frame_command_is_addressed and usfiv_route_allowed and usfiv_lookup_intent and usfiv_payload.get("needs_disambiguation"):
        await message.reply(usfiv_payload.get("data", "Please specify which USF4 move you mean."))
        return
    elif frame_command_is_addressed and usfiv_route_allowed and usfiv_lookup_intent and usfiv_payload.get("missing_scrolls_query"):
        await _reply_and_log_response(message, MISSING_SCROLLS_TEXT, "missing_scrolls")
        return
    ggacr_lookup_intent = bool(
        ggacr_payload.get("frame_query")
        or ggacr_payload.get("gif_query")
        or media_query
        or ggacr_payload.get("game_query")
        or ggacr_payload.get("notes_query")
        or requested_property_key
    )
    ggacr_route_allowed = bool(
        ggacr_payload.get("game_query")
        or (
            not has_explicit_game_conflict(ggacr_payload)
            and (
                ggacr_exclusive_character_query
                or (
                    ggacr_exact_character_query
                    and explicit_ggacr_query
                    and ggacr_module.query_has_ggacr_notation(content_lower)
                )
                or (
                    ggacr_exact_character_query
                    and ggacr_rows
                    and explicit_ggacr_query
                    and not sf6_exact_character_query
                    and not ggst_exact_character_query
                    and not sfv_exact_character_query
                    and not tuco_exact_character_query
                    and not bbcf_exact_character_query
                    and not cotw_exact_character_query
                    and not third_strike_exact_character_query
                    and not mk1_exact_character_query
                )
            )
        )
    )
    if frame_command_is_addressed and ggacr_route_allowed and ggacr_lookup_intent and ggacr_rows:
        if ggacr_payload.get("needs_disambiguation"):
            await message.reply(ggacr_payload.get("data", "Please specify which GGACR move you mean."))
        else:
            await _send_cross_game_lookup_response(message, ggacr_module, ggacr_rows, ggacr_payload, content_lower)
        return
    elif frame_command_is_addressed and ggacr_route_allowed and ggacr_lookup_intent and ggacr_payload.get("needs_disambiguation"):
        await message.reply(ggacr_payload.get("data", "Please specify which GGACR move you mean."))
        return
    elif frame_command_is_addressed and ggacr_route_allowed and ggacr_lookup_intent and ggacr_payload.get("missing_scrolls_query"):
        await _reply_and_log_response(message, MISSING_SCROLLS_TEXT, "missing_scrolls")
        return

    if frame_command_is_addressed and ggst_route_allowed and ggst_lookup_intent and not ggst_rows:
        rewritten_ggst_query = await buenavista_extension.rewrite_ggst_lookup_query(
            content_no_mentions,
            strip_discord_mentions,
            message=message,
        )
        if rewritten_ggst_query:
            rewritten_ggst_payload = ggst_module.find_moves_in_text(rewritten_ggst_query.lower())
            rewritten_ggst_rows = rewritten_ggst_payload.get("rows", []) or []
            if rewritten_ggst_rows or rewritten_ggst_payload.get("needs_disambiguation"):
                ggst_payload = rewritten_ggst_payload
                ggst_rows = rewritten_ggst_rows
                ggst_lookup_intent = bool(
                    ggst_payload.get("frame_query")
                    or ggst_payload.get("gif_query")
                    or ggst_payload.get("game_query")
                    or requested_property_key
                )
                print(f"[ggst-parser-private] rewritten query: {rewritten_ggst_query}", flush=True)

    if frame_command_is_addressed and ggst_route_allowed and ggst_lookup_intent and ggst_rows:
        if ggst_payload.get("needs_disambiguation"):
            await message.reply(ggst_payload.get("data", "Please specify which GGST move you mean."))
        else:
            await _send_cross_game_lookup_response(message, ggst_module, ggst_rows, ggst_payload, content_lower)
        return
    elif frame_command_is_addressed and ggst_route_allowed and ggst_lookup_intent and ggst_payload.get("needs_disambiguation"):
        await message.reply(ggst_payload.get("data", "Please specify which GGST move you mean."))
        return
    elif frame_command_is_addressed and ggst_route_allowed and ggst_lookup_intent and ggst_payload.get("missing_scrolls_query"):
        await _reply_and_log_response(message, MISSING_SCROLLS_TEXT, "missing_scrolls")
        return

    sfv_rows = sfv_payload.get("rows", [])
    sfv_character_query = bool(sfv_exact_character_query or sfv_payload.get("char_found"))
    sfv_lookup_intent = bool(
        sfv_payload.get("frame_query")
        or sfv_payload.get("gif_query")
        or media_query
        or sfv_payload.get("game_query")
        or sfv_payload.get("notes_query")
        or requested_property_key
        or sfv_module.query_has_sfv_notation(content_lower)
    )
    sfv_route_allowed = bool(
        sfv_payload.get("game_query")
        or (
            not has_explicit_game_conflict(sfv_payload)
            and (
                (
                    sfv_character_query
                    and sfv_module.query_has_sfv_notation(content_lower)
                    and not sf6_exact_character_query
                    and not ggst_exact_character_query
                    and not tuco_exact_character_query
                    and not bbcf_exact_character_query
                    and not cotw_exact_character_query
                    and not third_strike_exact_character_query
                    and not mk1_exact_character_query
                )
                or (
                    sfv_character_query
                    and sfv_rows
                    and not sf6_exact_character_query
                    and not ggst_exact_character_query
                    and not tuco_exact_character_query
                    and not bbcf_exact_character_query
                    and not cotw_exact_character_query
                    and not third_strike_exact_character_query
                    and not mk1_exact_character_query
                )
            )
        )
    )
    if frame_command_is_addressed and sfv_route_allowed and sfv_lookup_intent and sfv_rows:
        if sfv_payload.get("needs_disambiguation"):
            await message.reply(sfv_payload.get("data", "Please specify which SFV move you mean."))
        else:
            await _send_cross_game_lookup_response(message, sfv_module, sfv_rows, sfv_payload, content_lower)
        return
    elif frame_command_is_addressed and sfv_route_allowed and sfv_lookup_intent and sfv_payload.get("needs_disambiguation"):
        await message.reply(sfv_payload.get("data", "Please specify which SFV move you mean."))
        return
    elif frame_command_is_addressed and sfv_route_allowed and sfv_lookup_intent and sfv_payload.get("missing_scrolls_query"):
        await _reply_and_log_response(message, MISSING_SCROLLS_TEXT, "missing_scrolls")
        return

    tuco_rows = tuco_payload.get("rows", [])
    tuco_lookup_intent = bool(
        tuco_payload.get("frame_query")
        or tuco_payload.get("gif_query")
        or media_query
        or tuco_payload.get("game_query")
        or requested_property_key
    )
    tuco_route_allowed = bool(
        tuco_payload.get("game_query")
        or (
            not has_explicit_game_conflict(tuco_payload)
            and tuco_exact_character_query
            and not sf6_exact_character_query
            and not ggst_exact_character_query
            and not sfv_exact_character_query
        )
    )
    if frame_command_is_addressed and tuco_route_allowed and tuco_lookup_intent and tuco_rows:
        if tuco_payload.get("needs_disambiguation"):
            await message.reply(tuco_payload.get("data", "Please specify which 2XKO move you mean."))
        else:
            await _send_cross_game_lookup_response(message, tuco_module, tuco_rows, tuco_payload, content_lower)
        return
    elif frame_command_is_addressed and tuco_route_allowed and tuco_lookup_intent and tuco_payload.get("needs_disambiguation"):
        await message.reply(tuco_payload.get("data", "Please specify which 2XKO move you mean."))
        return
    elif frame_command_is_addressed and tuco_route_allowed and tuco_lookup_intent and tuco_payload.get("missing_scrolls_query"):
        await _reply_and_log_response(message, MISSING_SCROLLS_TEXT, "missing_scrolls")
        return

    bbcf_rows = bbcf_payload.get("rows", [])
    bbcf_lookup_intent = bool(
        bbcf_payload.get("frame_query")
        or bbcf_payload.get("gif_query")
        or media_query
        or bbcf_payload.get("game_query")
        or bbcf_payload.get("notes_query")
        or requested_property_key
    )
    bbcf_route_allowed = bool(
        bbcf_payload.get("game_query")
        or (
            not has_explicit_game_conflict(bbcf_payload)
            and (
                (
                    bbcf_exact_character_query
                    and bbcf_module.query_has_bbcf_notation(content_lower)
                    and not third_strike_module.query_has_third_strike_notation(content_lower)
                )
                or (
                    bbcf_exact_character_query
                    and bbcf_rows
                    and not sf6_exact_character_query
                    and not ggst_exact_character_query
                    and not sfv_exact_character_query
                    and not tuco_exact_character_query
                    and not third_strike_exact_character_query
                )
            )
        )
    )
    if frame_command_is_addressed and bbcf_route_allowed and bbcf_lookup_intent and bbcf_rows:
        if bbcf_payload.get("needs_disambiguation"):
            await message.reply(bbcf_payload.get("data", "Please specify which BBCF move you mean."))
        else:
            await _send_cross_game_lookup_response(message, bbcf_module, bbcf_rows, bbcf_payload, content_lower)
        return
    elif frame_command_is_addressed and bbcf_route_allowed and bbcf_lookup_intent and bbcf_payload.get("needs_disambiguation"):
        await message.reply(bbcf_payload.get("data", "Please specify which BBCF move you mean."))
        return
    elif frame_command_is_addressed and bbcf_route_allowed and bbcf_lookup_intent and bbcf_payload.get("missing_scrolls_query"):
        await _reply_and_log_response(message, MISSING_SCROLLS_TEXT, "missing_scrolls")
        return

    cotw_rows = cotw_payload.get("rows", [])
    cotw_lookup_intent = bool(
        cotw_payload.get("frame_query")
        or cotw_payload.get("gif_query")
        or media_query
        or cotw_payload.get("game_query")
        or cotw_payload.get("notes_query")
        or requested_property_key
    )
    cotw_route_allowed = bool(
        cotw_payload.get("game_query")
        or (
            not has_explicit_game_conflict(cotw_payload)
            and (
                (
                    cotw_exact_character_query
                    and cotw_module.query_has_cotw_notation(content_lower)
                )
                or (
                    cotw_exact_character_query
                    and cotw_rows
                    and not sf6_exact_character_query
                    and not ggst_exact_character_query
                    and not tuco_exact_character_query
                    and not bbcf_exact_character_query
                )
            )
        )
    )
    if frame_command_is_addressed and cotw_route_allowed and cotw_lookup_intent and cotw_rows:
        if cotw_payload.get("needs_disambiguation"):
            await message.reply(cotw_payload.get("data", "Please specify which COTW move you mean."))
        else:
            await _send_cross_game_lookup_response(message, cotw_module, cotw_rows, cotw_payload, content_lower)
        return
    elif frame_command_is_addressed and cotw_route_allowed and cotw_lookup_intent and cotw_payload.get("needs_disambiguation"):
        await message.reply(cotw_payload.get("data", "Please specify which COTW move you mean."))
        return
    elif frame_command_is_addressed and cotw_route_allowed and cotw_lookup_intent and cotw_payload.get("missing_scrolls_query"):
        await _reply_and_log_response(message, MISSING_SCROLLS_TEXT, "missing_scrolls")
        return

    avtl_rows = avtl_payload.get("rows", [])
    avtl_lookup_intent = bool(
        avtl_payload.get("frame_query")
        or avtl_payload.get("gif_query")
        or media_query
        or avtl_payload.get("game_query")
        or avtl_payload.get("notes_query")
        or requested_property_key
    )
    avtl_route_allowed = bool(
        avtl_payload.get("game_query")
        or (
            not has_explicit_game_conflict(avtl_payload)
            and (
                (
                    avtl_exact_character_query
                    and avtl_module.query_has_avtl_notation(content_lower)
                )
                or (
                    avtl_exact_character_query
                    and avtl_rows
                    and not sf6_exact_character_query
                    and not ggst_exact_character_query
                    and not tuco_exact_character_query
                    and not bbcf_exact_character_query
                    and not cotw_exact_character_query
                )
            )
        )
    )
    if frame_command_is_addressed and avtl_route_allowed and avtl_lookup_intent and avtl_rows:
        if avtl_payload.get("needs_disambiguation"):
            await message.reply(avtl_payload.get("data", "Please specify which AVTL move you mean."))
        else:
            await _send_cross_game_lookup_response(message, avtl_module, avtl_rows, avtl_payload, content_lower)
        return
    elif frame_command_is_addressed and avtl_route_allowed and avtl_lookup_intent and avtl_payload.get("needs_disambiguation"):
        await message.reply(avtl_payload.get("data", "Please specify which AVTL move you mean."))
        return
    elif frame_command_is_addressed and avtl_route_allowed and avtl_lookup_intent and avtl_payload.get("missing_scrolls_query"):
        await _reply_and_log_response(message, MISSING_SCROLLS_TEXT, "missing_scrolls")
        return

    third_strike_rows = third_strike_payload.get("rows", [])
    third_strike_lookup_intent = bool(
        third_strike_payload.get("frame_query")
        or third_strike_payload.get("gif_query")
        or media_query
        or third_strike_payload.get("game_query")
        or third_strike_payload.get("notes_query")
        or requested_property_key
        or third_strike_module.query_has_third_strike_notation(content_lower)
    )
    third_strike_route_allowed = bool(
        third_strike_payload.get("game_query")
        or (
            not has_explicit_game_conflict(third_strike_payload)
            and (
                (
                    third_strike_exact_character_query
                    and third_strike_module.query_has_third_strike_notation(content_lower)
                    and not sf6_exact_character_query
                )
                or (
                    third_strike_exact_character_query
                    and third_strike_rows
                    and not sf6_exact_character_query
                    and not ggst_exact_character_query
                    and not tuco_exact_character_query
                    and not bbcf_exact_character_query
                    and not cotw_exact_character_query
                    and not mk1_exact_character_query
                    and not avtl_exact_character_query
                    and not bbcf_module.query_has_bbcf_notation(content_lower)
                )
            )
        )
    )
    if frame_command_is_addressed and third_strike_route_allowed and third_strike_lookup_intent and third_strike_rows:
        if third_strike_payload.get("needs_disambiguation"):
            await message.reply(third_strike_payload.get("data", "Please specify which Third Strike move you mean."))
        else:
            await _send_cross_game_lookup_response(message, third_strike_module, third_strike_rows, third_strike_payload, content_lower)
        return
    elif frame_command_is_addressed and third_strike_route_allowed and third_strike_lookup_intent and third_strike_payload.get("needs_disambiguation"):
        await message.reply(third_strike_payload.get("data", "Please specify which Third Strike move you mean."))
        return
    elif frame_command_is_addressed and third_strike_route_allowed and third_strike_lookup_intent and third_strike_payload.get("missing_scrolls_query"):
        await _reply_and_log_response(message, MISSING_SCROLLS_TEXT, "missing_scrolls")
        return

    mk1_rows = mk1_payload.get("rows", [])
    mk1_lookup_intent = bool(
        mk1_payload.get("frame_query")
        or mk1_payload.get("gif_query")
        or media_query
        or mk1_payload.get("game_query")
        or mk1_payload.get("notes_query")
        or requested_property_key
        or mk1_module.query_has_mk1_notation(content_lower)
    )
    mk1_route_allowed = bool(
        mk1_payload.get("game_query")
        or (
            not has_explicit_game_conflict(mk1_payload)
            and (
                (
                    mk1_exact_character_query
                    and mk1_module.query_has_mk1_notation(content_lower)
                    and not sf6_exact_character_query
                )
                or (
                    mk1_exact_character_query
                    and mk1_rows
                    and not sf6_exact_character_query
                    and not ggst_exact_character_query
                    and not sfv_exact_character_query
                    and not tuco_exact_character_query
                    and not bbcf_exact_character_query
                    and not cotw_exact_character_query
                    and not third_strike_exact_character_query
                    and not avtl_exact_character_query
                )
            )
        )
    )
    if frame_command_is_addressed and mk1_route_allowed and mk1_lookup_intent and mk1_rows:
        if mk1_payload.get("needs_disambiguation"):
            await message.reply(mk1_payload.get("data", "Please specify which MK1 move you mean."))
        else:
            await _send_cross_game_lookup_response(message, mk1_module, mk1_rows, mk1_payload, content_lower)
        return
    elif frame_command_is_addressed and mk1_route_allowed and mk1_lookup_intent and mk1_payload.get("needs_disambiguation"):
        await message.reply(mk1_payload.get("data", "Please specify which MK1 move you mean."))
        return
    elif frame_command_is_addressed and mk1_route_allowed and mk1_lookup_intent and mk1_payload.get("missing_scrolls_query"):
        await _reply_and_log_response(message, MISSING_SCROLLS_TEXT, "missing_scrolls")
        return

    if (
        frame_command_is_addressed
        and usfiv_route_allowed
        and not usfiv_lookup_intent
        and allow_implied_frame_routing
        and (usfiv_rows or usfiv_payload.get("needs_disambiguation"))
    ):
        if usfiv_payload.get("needs_disambiguation"):
            await message.reply(usfiv_payload.get("data", "Please specify which USF4 move you mean."))
        else:
            _record_frame_data_ids(await usfiv_module.send_frame_response(message, usfiv_rows))
        return

    if (
        frame_command_is_addressed
        and mk1_route_allowed
        and not mk1_lookup_intent
        and allow_implied_frame_routing
        and (mk1_rows or mk1_payload.get("needs_disambiguation"))
    ):
        if mk1_payload.get("needs_disambiguation"):
            await message.reply(mk1_payload.get("data", "Please specify which MK1 move you mean."))
        else:
            _record_frame_data_ids(await mk1_module.send_frame_response(message, mk1_rows))
        return

    if (
        frame_command_is_addressed
        and sfv_route_allowed
        and not sfv_lookup_intent
        and allow_implied_frame_routing
        and (sfv_rows or sfv_payload.get("needs_disambiguation"))
    ):
        if sfv_payload.get("needs_disambiguation"):
            await message.reply(sfv_payload.get("data", "Please specify which SFV move you mean."))
        else:
            _record_frame_data_ids(await sfv_module.send_frame_response(message, sfv_rows))
        return

    if (
        frame_command_is_addressed
        and third_strike_route_allowed
        and not third_strike_lookup_intent
        and allow_implied_frame_routing
        and (third_strike_rows or third_strike_payload.get("needs_disambiguation"))
    ):
        if third_strike_payload.get("needs_disambiguation"):
            await message.reply(third_strike_payload.get("data", "Please specify which Third Strike move you mean."))
        else:
            _record_frame_data_ids(await third_strike_module.send_frame_response(message, third_strike_rows))
        return

    if (
        frame_command_is_addressed
        and tuco_route_allowed
        and not tuco_lookup_intent
        and allow_implied_frame_routing
        and (tuco_rows or tuco_payload.get("needs_disambiguation"))
    ):
        if tuco_payload.get("needs_disambiguation"):
            await message.reply(tuco_payload.get("data", "Please specify which 2XKO move you mean."))
        else:
            _record_frame_data_ids(await tuco_module.send_frame_response(message, tuco_rows))
        return

    if (
        frame_command_is_addressed
        and bbcf_route_allowed
        and not bbcf_lookup_intent
        and allow_implied_frame_routing
        and (bbcf_rows or bbcf_payload.get("needs_disambiguation"))
    ):
        if bbcf_payload.get("needs_disambiguation"):
            await message.reply(bbcf_payload.get("data", "Please specify which BBCF move you mean."))
        else:
            _record_frame_data_ids(await bbcf_module.send_frame_response(message, bbcf_rows))
        return

    if (
        frame_command_is_addressed
        and ggacr_route_allowed
        and not ggacr_lookup_intent
        and allow_implied_frame_routing
        and (ggacr_rows or ggacr_payload.get("needs_disambiguation"))
    ):
        if ggacr_payload.get("needs_disambiguation"):
            await message.reply(ggacr_payload.get("data", "Please specify which GGACR move you mean."))
        else:
            _record_frame_data_ids(await ggacr_module.send_frame_response(message, ggacr_rows))
        return

    if (
        frame_command_is_addressed
        and cotw_route_allowed
        and not cotw_lookup_intent
        and allow_implied_frame_routing
        and (cotw_rows or cotw_payload.get("needs_disambiguation"))
    ):
        if cotw_payload.get("needs_disambiguation"):
            await message.reply(cotw_payload.get("data", "Please specify which COTW move you mean."))
        else:
            _record_frame_data_ids(await cotw_module.send_frame_response(message, cotw_rows))
        return

    if (
        frame_command_is_addressed
        and avtl_route_allowed
        and not avtl_lookup_intent
        and allow_implied_frame_routing
        and (avtl_rows or avtl_payload.get("needs_disambiguation"))
    ):
        if avtl_payload.get("needs_disambiguation"):
            await message.reply(avtl_payload.get("data", "Please specify which AVTL move you mean."))
        else:
            _record_frame_data_ids(await avtl_module.send_frame_response(message, avtl_rows))
        return

    if (
        frame_command_is_addressed
        and ggst_route_allowed
        and not ggst_lookup_intent
        and allow_implied_frame_routing
        and (ggst_rows or ggst_payload.get("needs_disambiguation"))
    ):
        if ggst_payload.get("needs_disambiguation"):
            await message.reply(ggst_payload.get("data", "Please specify which GGST move you mean."))
        else:
            _record_frame_data_ids(await ggst_module.send_frame_response(message, ggst_rows))
        return


    # SF6 frame/gif path: unpack parser payload, optional BV LLM lookup rewrite, then respond
    check_media = False
    replied_context = None  # store bub's original message if replying to bot
    special_strength_reply_mode = None
    is_reply_to_bot = False
    
    
    # check mentions
    if directly_mentions_bot:
        check_media = True

    replied_context = None 
    

    fd_context_data = fd_context_payload.get("data", "")
    fd_context_mode = fd_context_payload.get("mode", "none")
    fd_context_rows = fd_context_payload.get("rows", [])
    requested_sf6_property_key = _requested_property_key_after_move_resolution(content_lower, fd_context_rows)
    startup_alias_query = requested_sf6_property_key == "startup"
    hitconfirm_alias_query = requested_sf6_property_key == "hitconfirm"
    super_gain_alias_query = requested_sf6_property_key == "super_gain"
    range_alias_query = requested_sf6_property_key == "range"
    wants_comparison = bool(fd_context_payload.get("wants_comparison"))
    property_only_query = bool(requested_sf6_property_key)
    target_combo_query = bool(fd_context_payload.get("target_combo_query"))
    missing_scrolls_query = bool(fd_context_payload.get("missing_scrolls_query"))
    gif_query = bool(fd_context_payload.get("gif_query"))
    explicit_move_attempt = bool(fd_context_payload.get("explicit_move_attempt"))
    fallback_reply = (
        None
        if fd_context_mode == "frame" and fd_context_rows
        else (fd_context_data if fd_context_data else None)
    )

    def row_matches_requested_strength(row, query_text):
        move_name = str(row.get("moveName", "")).lower().strip()
        cmn_name = str(row.get("cmnName", "")).lower().strip()
        num_cmd = str(row.get("numCmd", "")).lower().strip()
        num_cmd_compact = re.sub(r"[^a-z0-9]", "", num_cmd)

        if re.search(r"\b(?:od|ex)\b", query_text):
            return (
                move_name.startswith(("od ", "ex "))
                or cmn_name.startswith(("od ", "ex "))
                or num_cmd_compact.endswith(("pp", "kk"))
            )

        strength_groups = [
            ({"light", "l", "lp", "lk"}, {"lp", "lk"}),
            ({"medium", "m", "mp", "mk"}, {"mp", "mk"}),
            ({"heavy", "h", "hp", "hk"}, {"hp", "hk"}),
        ]
        requested_suffixes = set()
        for token_group, suffixes in strength_groups:
            if any(re.search(rf"\b{re.escape(token)}\b", query_text) for token in token_group):
                requested_suffixes.update(suffixes)
        if not requested_suffixes:
            return False

        if any(
            move_name.startswith(f"{suffix} ") or cmn_name.startswith(f"{suffix} ")
            for suffix in requested_suffixes
        ):
            return True
        return num_cmd_compact.endswith(tuple(requested_suffixes))

    def row_has_explicit_strength(row):
        move_name = str(row.get("moveName", "")).lower().strip()
        cmn_name = str(row.get("cmnName", "")).lower().strip()
        num_cmd_compact = re.sub(r"[^a-z0-9]", "", str(row.get("numCmd", "")).lower())
        return (
            move_name.startswith(("lp ", "mp ", "hp ", "lk ", "mk ", "hk ", "od ", "ex "))
            or cmn_name.startswith(("lp ", "mp ", "hp ", "lk ", "mk ", "hk ", "od ", "ex "))
            or num_cmd_compact.endswith(("lp", "mp", "hp", "lk", "mk", "hk", "pp", "kk"))
        )

    query_requests_explicit_strength = bool(
        re.search(r"\b(?:od|ex|lp|mp|hp|lk|mk|hk|light|medium|heavy|l|m|h)\b", content_lower)
    )
    payload_strength_mismatch = bool(
        query_requests_explicit_strength
        and fd_context_rows
        and any(row_has_explicit_strength(row) for row in fd_context_rows)
        and not any(row_matches_requested_strength(row, content_lower) for row in fd_context_rows)
    )

    should_try_private_lookup_rewrite = bool(
        directly_mentions_bot
        and (fd_context_payload.get("gif_query") or re.search(r"\b(?:framedata|frame\s*data|frames?)\b", content_lower))
        and (not fd_context_rows or payload_strength_mismatch)
        and "Special Strength Options" not in str(fd_context_data)
        and "Target Combo Options" not in str(fd_context_data)
        and not startup_alias_query
        and not hitconfirm_alias_query
        and not super_gain_alias_query
        and not range_alias_query
    )
    if should_try_private_lookup_rewrite:
        rewritten_lookup_query = await buenavista_extension.rewrite_sf_lookup_query(
            content_no_mentions,
            strip_discord_mentions,
            message=message,
        )
        if rewritten_lookup_query:
            rewritten_payload = find_moves_in_text(rewritten_lookup_query.lower())
            rewritten_data = str(rewritten_payload.get("data", "") or "")
            rewritten_rows = rewritten_payload.get("rows", []) or []
            if (
                rewritten_rows
                or "Special Strength Options" in rewritten_data
                or "Target Combo Options" in rewritten_data
            ):
                content_no_mentions = rewritten_lookup_query
                content_lower = rewritten_lookup_query.lower()
                fd_context_payload = rewritten_payload
                fd_context_data = rewritten_payload.get("data", "")
                fd_context_mode = rewritten_payload.get("mode", "none")
                fd_context_rows = rewritten_rows
                requested_sf6_property_key = _requested_property_key_after_move_resolution(content_lower, fd_context_rows)
                startup_alias_query = requested_sf6_property_key == "startup"
                hitconfirm_alias_query = requested_sf6_property_key == "hitconfirm"
                super_gain_alias_query = requested_sf6_property_key == "super_gain"
                range_alias_query = requested_sf6_property_key == "range"
                wants_comparison = bool(rewritten_payload.get("wants_comparison"))
                property_only_query = bool(requested_sf6_property_key)
                target_combo_query = bool(rewritten_payload.get("target_combo_query"))
                missing_scrolls_query = bool(rewritten_payload.get("missing_scrolls_query"))
                gif_query = bool(rewritten_payload.get("gif_query"))
                explicit_move_attempt = bool(rewritten_payload.get("explicit_move_attempt"))
                fallback_reply = (
                    None
                    if fd_context_mode == "frame" and fd_context_rows
                    else (fd_context_data if fd_context_data else None)
                )
                print(f"[parser-private] rewritten query: {rewritten_lookup_query}", flush=True)

    if gif_query and not frame_command_is_addressed:
        return

    explicit_frame_request = (
        "framedata" in content_lower
        or "frame data" in content_lower
        or re.search(r"\bframes?\b", content_lower)
        or re.search(r"\bhow\s+fast\b", content_lower)
        or re.search(r"\bhow\s+quick\b", content_lower)
        or re.search(r"\bspeed\s+of\b", content_lower)
        or (
            re.search(r"\bfast\b", content_lower)
            and re.search(r"\b[1-9][0-9]*[a-zA-Z]{1,3}\b", content_lower)
        )
    )
    force_verbatim_frame_reply = bool(
        fd_context_mode == "frame"
        and not property_only_query
        and fd_context_rows
    )
    frame_reply_embeds = (
        build_frame_embeds(fd_context_rows)
        if force_verbatim_frame_reply and fd_context_rows
        else []
    )
    frame_reply_rows = (
        iter_unique_frame_rows(fd_context_rows)
        if force_verbatim_frame_reply and fd_context_rows
        else []
    )
    if frame_reply_embeds:
        print(
            "Frame embed mode active: "
            f"count={len(frame_reply_embeds)} property_only={property_only_query}",
            flush=True,
        )
    

    
    should_handle_direct_frame = (
        frame_command_is_addressed
        or ".framedata" in content_lower
    )
    combined_frame_gif_request = bool(
        gif_query
        and explicit_frame_request
        and fd_context_mode == "frame"
        and not property_only_query
        and not startup_alias_query
        and not hitconfirm_alias_query
        and not super_gain_alias_query
        and not range_alias_query
    )

    vague_move_query_without_output_intent = False
    implied_rows = []
    implied_data = ""
    implied_has_special_prompt = False
    if (
        frame_command_is_addressed
        and allow_implied_frame_routing
        and not gif_query
        and not explicit_frame_request
        and not property_only_query
        and not target_combo_query
        and not startup_alias_query
        and not hitconfirm_alias_query
        and not super_gain_alias_query
        and not range_alias_query
        and not re.search(
            r"\b(punish|punishable|compare|comparison|versus|vs|stats?|health|reversal|combo|bnb|oki|playstyle|overview)\b",
            content_lower,
        )
        and not buenavista_extension.should_suppress_public_implied_frame_lookup(content_lower, message=message)
    ):
        implied_frame_payload = find_moves_in_text(f"{content_lower} framedata")
        implied_data = implied_frame_payload.get("data", "")
        implied_rows = implied_frame_payload.get("rows", [])
        implied_mode = implied_frame_payload.get("mode", "none")
        implied_explicit_move_attempt = bool(implied_frame_payload.get("explicit_move_attempt"))
        implied_has_special_prompt = "Special Strength Options" in implied_data
        vague_move_query_without_output_intent = bool(
            implied_explicit_move_attempt
            and (
                (implied_mode == "frame" and implied_rows)
                or implied_has_special_prompt
            )
        )

    if (
        not vague_move_query_without_output_intent
        and frame_command_is_addressed
        and allow_implied_frame_routing
        and target_combo_query
        and explicit_move_attempt
        and fd_context_mode == "frame"
        and fd_context_rows
        and not gif_query
        and not explicit_frame_request
        and not property_only_query
        and not startup_alias_query
        and not hitconfirm_alias_query
        and not super_gain_alias_query
        and not range_alias_query
    ):
        vague_move_query_without_output_intent = True

    if should_handle_direct_frame:
        if (
            frame_command_is_addressed
            and fd_context_payload.get("stats_only")
            and fd_context_payload.get("stats_char_keys")
        ):
            stats_sent_ids = await send_character_stats_response(
                message,
                fd_context_payload.get("stats_char_keys"),
                fd_context_payload.get("stats_keys"),
            )
            _record_frame_data_ids(stats_sent_ids)
            if not stats_sent_ids:
                await message.reply("I don't have stats scrolls for that character.")
            return

        if vague_move_query_without_output_intent:
            if implied_has_special_prompt:
                try:
                    sent_prompt = await message.reply(implied_data)
                    sf6_prompt_replies.remember_special_strength_prompt_mode(sent_prompt.id, "frame")
                except Exception as reply_error:
                    if is_deleted_message_reference_error(reply_error):
                        print("Special strength options implied reply target deleted. Triggering failsafe.", flush=True)
                        await send_deleted_message_failsafe(message.channel)
                    else:
                        print(f"Special strength options implied reply error: {reply_error}", flush=True)
                return
            default_rows = implied_rows or fd_context_rows
            frame_sent_ids = await send_frame_table_response(message, default_rows)
            _record_frame_data_ids(frame_sent_ids)
            return

        if (
            wants_comparison
            and fd_context_mode == "frame"
            and fd_context_rows
            and explicit_move_attempt
            and not gif_query
            and not property_only_query
            and not target_combo_query
            and not startup_alias_query
            and not hitconfirm_alias_query
            and not super_gain_alias_query
            and not range_alias_query
        ):
            frame_sent_ids = await send_frame_table_response(message, fd_context_rows, fd_context_data)
            _record_frame_data_ids(frame_sent_ids)
            return

        if combined_frame_gif_request and frame_command_is_addressed:
            if "Special Strength Options" in fd_context_data:
                try:
                    sent_prompt = await message.reply(fd_context_data)
                    sf6_prompt_replies.remember_special_strength_prompt_mode(sent_prompt.id, "both")
                except Exception as reply_error:
                    if is_deleted_message_reference_error(reply_error):
                        print("Special strength options both reply target deleted. Triggering failsafe.", flush=True)
                        await send_deleted_message_failsafe(message.channel)
                    else:
                        print(f"Special strength options both reply error: {reply_error}", flush=True)
                return

            if not explicit_move_attempt:
                await message.reply("Tell me the exact move too, like 'aki 5hp gif framedata'.")
                return

            if missing_scrolls_query:
                try:
                    await _reply_and_log_response(message, MISSING_SCROLLS_TEXT, "missing_scrolls")
                except Exception as reply_error:
                    if is_deleted_message_reference_error(reply_error):
                        print("Missing-scrolls both reply target deleted. Triggering failsafe.", flush=True)
                        await send_deleted_message_failsafe(message.channel)
                    else:
                        print(f"Missing-scrolls both reply error: {reply_error}", flush=True)
                return

            frame_table_already_sent = False
            if fd_context_rows:
                _record_frame_data_ids(await send_frame_table_response(message, fd_context_rows, fd_context_data))
                frame_table_already_sent = True

                gif_frame_rows = fd_context_rows
                if wants_comparison and fd_context_rows:
                    comparison_rows = []
                    seen_comparison_chars = set()
                    for row in fd_context_rows:
                        row_char = normalize_char_name(row.get("char_name", ""))
                        if not row_char or row_char in seen_comparison_chars:
                            continue
                        seen_comparison_chars.add(row_char)
                        comparison_rows.append(row)
                    if len(comparison_rows) >= 2:
                        gif_frame_rows = comparison_rows

                gif_limit = gif_lookup_module.DISCORD_ATTACHMENT_LIMIT
                if wants_comparison and gif_frame_rows:
                    gif_limit = max(gif_limit, len(gif_frame_rows))

                gif_links = collect_hitbox_gif_links_from_text(
                    content_no_mentions,
                    frame_rows=gif_frame_rows,
                    limit=gif_limit,
                    prefer_frame_rows=wants_comparison,
                )
                if gif_links:
                    _record_frame_data_ids(await send_gif_links_response(
                        message,
                        gif_links,
                        wants_comparison=wants_comparison,
                    ))
                    return

                try:
                    await send_missing_hitbox_gif_reply(
                        message,
                        fd_context_rows,
                        include_framedata_button=not frame_table_already_sent,
                        reply_and_log_response=_reply_and_log_response,
                        record_frame_data_ids=_record_frame_data_ids,
                    )
                except Exception as reply_error:
                    if is_deleted_message_reference_error(reply_error):
                        print("Missing-gif both reply target deleted. Triggering failsafe.", flush=True)
                        await send_deleted_message_failsafe(message.channel)
                    else:
                        print(f"Missing-gif both reply error: {reply_error}", flush=True)
                return

        if gif_query and frame_command_is_addressed:
            if "Special Strength Options" in fd_context_data:
                try:
                    sent_prompt = await message.reply(fd_context_data)
                    sf6_prompt_replies.remember_special_strength_prompt_mode(sent_prompt.id, "gif")
                except Exception as reply_error:
                    if is_deleted_message_reference_error(reply_error):
                        print("Special strength options gif reply target deleted. Triggering failsafe.", flush=True)
                        await send_deleted_message_failsafe(message.channel)
                    else:
                        print(f"Special strength options gif reply error: {reply_error}", flush=True)
                return

            if not explicit_move_attempt:
                await message.reply("Tell me the exact move too, like 'aki 5hp gif'.")
                return

            gif_frame_rows = fd_context_rows
            if wants_comparison and fd_context_rows:
                comparison_rows = []
                seen_comparison_chars = set()
                for row in fd_context_rows:
                    row_char = normalize_char_name(row.get("char_name", ""))
                    if not row_char or row_char in seen_comparison_chars:
                        continue
                    seen_comparison_chars.add(row_char)
                    comparison_rows.append(row)
                if len(comparison_rows) >= 2:
                    gif_frame_rows = comparison_rows

            gif_limit = gif_lookup_module.DISCORD_ATTACHMENT_LIMIT
            if wants_comparison and gif_frame_rows:
                gif_limit = max(gif_limit, len(gif_frame_rows))

            gif_links = collect_hitbox_gif_links_from_text(
                content_no_mentions,
                frame_rows=gif_frame_rows,
                limit=gif_limit,
                prefer_frame_rows=wants_comparison,
            )
            if gif_links:
                _record_frame_data_ids(await send_gif_links_response(
                    message,
                    gif_links,
                    wants_comparison=wants_comparison,
                ))
                return

            if fd_context_rows:
                try:
                    await send_missing_hitbox_gif_reply(
                        message,
                        fd_context_rows,
                        reply_and_log_response=_reply_and_log_response,
                        record_frame_data_ids=_record_frame_data_ids,
                    )
                except Exception as reply_error:
                    if is_deleted_message_reference_error(reply_error):
                        print("Missing-gif reply target deleted. Triggering failsafe.", flush=True)
                        await send_deleted_message_failsafe(message.channel)
                    else:
                        print(f"Missing-gif reply error: {reply_error}", flush=True)
                return

        if missing_scrolls_query:
            try:
                await _reply_and_log_response(message, MISSING_SCROLLS_TEXT, "missing_scrolls")
            except Exception as reply_error:
                if is_deleted_message_reference_error(reply_error):
                    print("Missing-scrolls reply target deleted. Triggering failsafe.", flush=True)
                    await send_deleted_message_failsafe(message.channel)
                else:
                    print(f"Missing-scrolls reply error: {reply_error}", flush=True)
            return
        if "Target Combo Options" in fd_context_data:
            try:
                await message.reply(fd_context_data)
            except Exception as reply_error:
                if is_deleted_message_reference_error(reply_error):
                    print("Target combo options reply target deleted. Triggering failsafe.", flush=True)
                    await send_deleted_message_failsafe(message.channel)
                else:
                    print(f"Target combo options reply error: {reply_error}", flush=True)
            return
        if "Special Strength Options" in fd_context_data:
            try:
                sent_prompt = await message.reply(fd_context_data)
                sf6_prompt_replies.remember_special_strength_prompt_mode(
                    sent_prompt.id,
                    "gif" if gif_query else "frame",
                )
            except Exception as reply_error:
                if is_deleted_message_reference_error(reply_error):
                    print("Special strength options reply target deleted. Triggering failsafe.", flush=True)
                    await send_deleted_message_failsafe(message.channel)
                else:
                    print(f"Special strength options reply error: {reply_error}", flush=True)
            return
        if target_combo_query and fd_context_mode == "frame" and fd_context_rows:
            _record_frame_data_ids(await send_frame_table_response(message, fd_context_rows, fd_context_data))
            return
        if property_only_query and requested_sf6_property_key and fd_context_mode == "frame" and fd_context_rows:
            property_reply = _format_requested_property_reply(fd_context_rows, requested_sf6_property_key)
            if property_reply:
                try:
                    await _send_property_value_reply(message, fd_context_rows, requested_sf6_property_key, content=property_reply, game="sf6")
                except Exception as reply_error:
                    if is_deleted_message_reference_error(reply_error):
                        print("Direct property reply target deleted. Triggering failsafe.", flush=True)
                        await send_deleted_message_failsafe(message.channel)
                    else:
                        print(f"Direct property reply error: {reply_error}", flush=True)
                return
        if range_alias_query and fd_context_mode == "frame" and fd_context_rows:
            range_reply = format_range_only_reply(fd_context_rows)
            if range_reply:
                try:
                    await _send_property_value_reply(message, fd_context_rows, "range", content=range_reply, game="sf6")
                except Exception as reply_error:
                    if is_deleted_message_reference_error(reply_error):
                        print("Direct range reply target deleted. Triggering failsafe.", flush=True)
                        await send_deleted_message_failsafe(message.channel)
                    else:
                        print(f"Direct range reply error: {reply_error}", flush=True)
                return
        if super_gain_alias_query and fd_context_mode == "frame" and fd_context_rows:
            super_gain_reply = format_super_gain_only_reply(fd_context_rows)
            if super_gain_reply:
                try:
                    await _send_property_value_reply(message, fd_context_rows, "super_gain", content=super_gain_reply, game="sf6")
                except Exception as reply_error:
                    if is_deleted_message_reference_error(reply_error):
                        print("Direct super gain reply target deleted. Triggering failsafe.", flush=True)
                        await send_deleted_message_failsafe(message.channel)
                    else:
                        print(f"Direct super gain reply error: {reply_error}", flush=True)
                return
        if hitconfirm_alias_query and fd_context_mode == "frame" and fd_context_rows:
            hitconfirm_reply = format_hitconfirm_only_reply(fd_context_rows)
            if hitconfirm_reply:
                try:
                    await _send_property_value_reply(message, fd_context_rows, "hitconfirm", content=hitconfirm_reply, game="sf6")
                except Exception as reply_error:
                    if is_deleted_message_reference_error(reply_error):
                        print("Direct hitconfirm reply target deleted. Triggering failsafe.", flush=True)
                        await send_deleted_message_failsafe(message.channel)
                    else:
                        print(f"Direct hitconfirm reply error: {reply_error}", flush=True)
                return
        if startup_alias_query and fd_context_mode == "frame" and fd_context_rows:
            startup_reply = format_startup_only_reply(fd_context_rows)
            if startup_reply:
                try:
                    await _send_property_value_reply(message, fd_context_rows, "startup", content=startup_reply, game="sf6")
                except Exception as reply_error:
                    if is_deleted_message_reference_error(reply_error):
                        print("Direct startup reply target deleted. Triggering failsafe.", flush=True)
                        await send_deleted_message_failsafe(message.channel)
                    else:
                        print(f"Direct startup reply error: {reply_error}", flush=True)
                return

        if (
            explicit_frame_request
            and fd_context_mode == "frame"
            and fd_context_rows
            and not property_only_query
            and not target_combo_query
            and not startup_alias_query
            and not hitconfirm_alias_query
            and not super_gain_alias_query
            and not range_alias_query
            and not gif_query
        ):
            frame_sent_ids = await send_frame_table_response(message, fd_context_rows, fd_context_data)
            _record_frame_data_ids(frame_sent_ids)
            return

        if buenavista_extension.should_handle_frame_context_request(
            content_lower=content_lower,
            fd_context_data=fd_context_data,
            message=message,
        ):
            handled = await buenavista_extension.maybe_handle_frame_context(
                client=client,
                message=message,
                content_no_mentions=content_no_mentions,
                content_lower=content_lower,
                fd_context_payload=fd_context_payload,
                fd_context_data=fd_context_data,
                fd_context_mode=fd_context_mode,
                fd_context_rows=fd_context_rows,
                property_only_query=property_only_query,
                frame_reply_embeds=frame_reply_embeds,
                frame_reply_rows=frame_reply_rows,
                fallback_reply=fallback_reply,
                strip_discord_mentions=strip_discord_mentions,
                is_deleted_message_reference_error=is_deleted_message_reference_error,
                send_frame_embeds_with_views=send_frame_embeds_with_views,
                record_frame_data_ids=_record_frame_data_ids,
            )
            if handled:
                return

    if replied_context is None and message.reference:
        try:
            if message.reference.cached_message:
                replied_msg = message.reference.cached_message
            else:
                replied_msg = await message.channel.fetch_message(message.reference.message_id)
            
            # replying to bot
            if replied_msg.author == client.user:
                check_media = True # check media on reply
                is_reply_to_bot = True
                if "Target Combo Options" in replied_msg.content:
                    replied_context = replied_msg.content  # capture only TC prompt
                elif "Special Strength Options" in replied_msg.content:
                    replied_context = replied_msg.content
                    special_strength_reply_mode = await _resolve_special_strength_reply_mode(replied_msg)

        except discord.NotFound:
            pass
        except discord.Forbidden:
            pass
        except Exception as e:
            print(f"Reply logic error: {e}")

    # SF6 target combo / special strength numbered replies
    if await sf6_prompt_replies.handle_sf6_prompt_reply(
        _sf6_prompt_reply_deps(),
        message,
        replied_context,
        content_no_mentions,
        gif_query=gif_query,
        special_strength_reply_mode=special_strength_reply_mode,
    ):
        return

    # BV LLM chat when enabled; otherwise fall through to public invalid-query notice in BV too
    should_respond = buenavista_extension.should_handle_chat_trigger(
        client=client,
        message=message,
        is_reply_to_bot=is_reply_to_bot,
        replied_context=replied_context,
    )
    if should_respond:
        handled = await buenavista_extension.maybe_handle_chat(
            client=client,
            message=message,
            content_no_mentions=content_no_mentions,
            content_lower=content_lower,
            replied_context=replied_context,
            fallback_reply=fallback_reply,
            frame_reply_embeds=frame_reply_embeds,
            frame_reply_rows=frame_reply_rows,
            strip_discord_mentions=strip_discord_mentions,
            is_deleted_message_reference_error=is_deleted_message_reference_error,
            send_frame_embeds_with_views=send_frame_embeds_with_views,
            record_frame_data_ids=_record_frame_data_ids,
        )
        if handled:
            return

    if frame_command_is_addressed and buenavista_extension.should_send_public_invalid_query_notice(message):
        await _reply_and_log_response(
            message,
            PUBLIC_INVALID_QUERY_TEXT,
            "public_invalid_query",
        )
        return


register_slash_commands(
    tree,
    {
        "frame_data": FRAME_DATA,
        "frame_stats": FRAME_STATS,
        "resolve_character_key": resolve_character_key,
        "find_moves_in_text": find_moves_in_text,
        "build_frame_embed": build_frame_embed,
        "frame_output_module": frame_output_module,
        "ggst_module": ggst_module,
        "sfv_module": sfv_module,
        "tuco_module": tuco_module,
        "bbcf_module": bbcf_module,
        "ggacr_module": ggacr_module,
        "cotw_module": cotw_module,
            "avtl_module": avtl_module,
        "third_strike_module": third_strike_module,
        "usfiv_module": usfiv_module,
        "mk1_module": mk1_module,
        "combo_data_module": combo_data_module,
        "menu_system": menu_system,
        "buenavista_extension": buenavista_extension,
    },
)


def main():
    if not TOKEN:
        print("Error: DISCORD_TOKEN not found in .env")
    else:
        client.run(TOKEN)


if __name__ == "__main__":
    main()
