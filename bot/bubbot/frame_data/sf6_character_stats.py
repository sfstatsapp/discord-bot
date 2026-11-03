"""SF6 character stats sheet helpers: intent parsing, embeds, parser context injection."""

import re
from dataclasses import dataclass
from typing import Optional, Tuple

import discord

from bubbot.data.aliases import (
    SF6_STAT_BROAD_TERMS,
    SF6_STAT_LEGACY_TERMS,
    SF6_STAT_NOTATION_ALIASES,
    SF6_STAT_PHRASE_ALIASES,
    SF6_STAT_TOKEN_SEQUENCE_ALIASES,
)
from bubbot.frame_data.sf6_parser_helpers import query_has_directional_normal_notation
from bubbot.frame_data.sf6_parser_helpers import query_has_grounded_normal_notation
from bubbot.utils.discord_formatting import add_embed_field as shared_add_embed_field
from bubbot.utils.discord_formatting import clean_value as shared_clean_value
from bubbot.utils.discord_formatting import truncate_value as shared_truncate_value
from bubbot.utils.text_utils import compact_key, contains_token_sequence, correct_alias_typos, word_tokens
from bubbot.utils.sfstats_style import BRAND


STAT_FIELD_LABELS = {
    "health": "Health",
    "bestReversal": "Best Reversal",
    "nJump": "Neutral Jump",
    "fJump": "Forward Jump",
    "bJump": "Back Jump",
    "fDash": "Forward Dash",
    "bDash": "Back Dash",
    "fWalk": "Forward Walk Speed",
    "bWalk": "Back Walk Speed",
    "fJumpDist": "Forward Jump Distance",
    "bJumpDist": "Back Jump Distance",
    "fDashDist": "Forward Dash Distance",
    "bDashDist": "Back Dash Distance",
    "dRushDist": "Drive Rush Distance",
    "dRushDistMin": "Drive Rush Distance (Min)",
    "dRushDistBlock": "Drive Rush Distance (Block)",
    "dRushDistMax": "Drive Rush Distance (Max)",
    "throwHurt": "Throw Hurtbox",
    "throwRange": "Throw Range",
    "phrase": "Phrase",
}

STAT_DISPLAY_ORDER = tuple(STAT_FIELD_LABELS.keys())
_FRAME_SUFFIX_KEYS = frozenset({"fDash", "bDash", "nJump", "fJump", "bJump"})

_STAT_PHRASE_KEYS = {}
for _phrases, _keys in SF6_STAT_PHRASE_ALIASES:
    for _phrase in _phrases:
        _STAT_PHRASE_KEYS[_phrase] = _keys
        _STAT_PHRASE_KEYS[compact_key(_phrase)] = _keys

_STAT_TYPO_WORDS = {
    word
    for term in (*SF6_STAT_BROAD_TERMS, *SF6_STAT_LEGACY_TERMS, *_STAT_PHRASE_KEYS)
    for word in word_tokens(term)
}
_STAT_TYPO_WORDS = {
    word
    for word in _STAT_TYPO_WORDS
    if not (
        (word.endswith("s") and word[:-1] in _STAT_TYPO_WORDS)
        or (word.endswith("es") and word[:-2] in _STAT_TYPO_WORDS)
    )
}
_STAT_TYPO_ALIASES = {word: word for word in _STAT_TYPO_WORDS}


@dataclass(frozen=True)
class StatsIntent:
    wants_stats: bool
    character_keys: Tuple[str, ...]
    stat_keys: Optional[Tuple[str, ...]]
    stats_only: bool


def _phrase_in_text(text_lower, phrase):
    pattern = rf"\b{re.escape(phrase)}\b"
    return bool(re.search(pattern, text_lower))


def _normalize_stat_terms(text_lower):
    return correct_alias_typos(text_lower, _STAT_TYPO_ALIASES, ambiguity_margin=0.05)


_JUMP_FAMILY_KEYS = frozenset({"nJump", "fJump", "bJump", "fJumpDist", "bJumpDist"})


def _apply_jump_notation_precedence(tokens, matched):
    """When a single numpad jump token (8/7/9) is present, prefer that jump only."""
    jump_notations = [
        notation
        for notation in ("8", "7", "9")
        if contains_token_sequence(tokens, [notation])
    ]
    if len(jump_notations) != 1:
        return matched
    keep = set(SF6_STAT_NOTATION_ALIASES[jump_notations[0]])
    if not (matched & _JUMP_FAMILY_KEYS):
        return matched
    return (matched - _JUMP_FAMILY_KEYS) | keep


def match_stat_keys_in_text(text_lower):
    text_lower = _normalize_stat_terms(text_lower)
    matched = set()
    tokens = word_tokens(text_lower)
    for alias, keys in SF6_STAT_NOTATION_ALIASES.items():
        if contains_token_sequence(tokens, word_tokens(alias)):
            matched.update(keys)
    for token_sequence, keys in SF6_STAT_TOKEN_SEQUENCE_ALIASES:
        if contains_token_sequence(tokens, list(token_sequence)):
            matched.update(keys)
    for phrase, keys in _STAT_PHRASE_KEYS.items():
        if _phrase_in_text(text_lower, phrase):
            matched.update(keys)
    return _apply_jump_notation_precedence(tokens, matched)


def _query_mentions_stats(text_lower):
    text_lower = _normalize_stat_terms(text_lower)
    if any(_phrase_in_text(text_lower, term) for term in SF6_STAT_BROAD_TERMS):
        return True
    if match_stat_keys_in_text(text_lower):
        return True
    return any(_phrase_in_text(text_lower, term) for term in SF6_STAT_LEGACY_TERMS)


def _query_has_air_throw_move_intent(text_lower):
    return bool(
        re.search(r"\b(?:air|aerial)\s+throw\b", text_lower)
        or re.search(r"\bairthrows?\b", text_lower)
    )


def _query_has_jump_normal_move_intent(text_lower):
    return bool(
        re.search(r"\b[789][lmh][pk]\b", text_lower)
        or re.search(r"\bj\.?[789]?[lmh][pk]\b", text_lower)
        or re.search(r"\b(?:neutral|n)\s+j(?:ump)?\s*\.?\s*[lmh][pk]\b", text_lower)
        or re.search(r"\bjump\s+[lmh][pk]\b", text_lower)
    )


def parse_stats_intent(
    text_lower,
    mentioned_chars,
    *,
    explicit_move_attempt=False,
    wants_frame_data=False,
    gif_query=False,
    property_only_query=False,
    startup_alias_query=False,
    charge_button_query=False,
    move_results_found=False,
):
    if not mentioned_chars:
        return StatsIntent(False, tuple(), None, False)

    normalized_stat_text = _normalize_stat_terms(text_lower)
    broad_stats = any(_phrase_in_text(normalized_stat_text, term) for term in SF6_STAT_BROAD_TERMS)
    phrase_keys = match_stat_keys_in_text(normalized_stat_text)
    air_throw_move_query = _query_has_air_throw_move_intent(text_lower)
    jump_normal_move_query = _query_has_jump_normal_move_intent(text_lower)
    directional_normal_move_query = query_has_directional_normal_notation(text_lower)
    grounded_normal_move_query = query_has_grounded_normal_notation(text_lower)
    if charge_button_query and phrase_keys:
        phrase_keys = tuple(key for key in phrase_keys if key != "health")
    if air_throw_move_query and phrase_keys:
        phrase_keys = tuple(
            key for key in phrase_keys
            if key not in {"throwRange", "throwHurt"}
        )
    if jump_normal_move_query and phrase_keys:
        phrase_keys = tuple(key for key in phrase_keys if key not in _JUMP_FAMILY_KEYS and key != "health")
    if directional_normal_move_query and phrase_keys:
        phrase_keys = tuple(key for key in phrase_keys if key != "health")
    if grounded_normal_move_query and phrase_keys:
        phrase_keys = tuple(key for key in phrase_keys if key != "health")
    wants_stats = bool(broad_stats or phrase_keys or _query_mentions_stats(normalized_stat_text))

    if air_throw_move_query and not broad_stats:
        wants_stats = False
    if jump_normal_move_query and not broad_stats:
        wants_stats = False
    if directional_normal_move_query and not broad_stats:
        wants_stats = False
    if grounded_normal_move_query and not broad_stats:
        wants_stats = False
    if charge_button_query and not broad_stats:
        wants_stats = False

    if (startup_alias_query or property_only_query) and not broad_stats and not phrase_keys:
        wants_stats = False
    if wants_frame_data and explicit_move_attempt and not broad_stats and not phrase_keys:
        wants_stats = False

    if not wants_stats:
        return StatsIntent(False, tuple(), None, False)

    stat_keys = None if (broad_stats and not phrase_keys) else (tuple(sorted(phrase_keys)) if phrase_keys else None)
    stats_focused = bool(broad_stats or phrase_keys)
    stats_only = bool(
        wants_stats
        and not gif_query
        and (not explicit_move_attempt or stats_focused)
        and (not wants_frame_data or stats_focused)
        and not (move_results_found and stat_keys is None)
    )
    return StatsIntent(True, tuple(mentioned_chars), stat_keys, stats_only)


def display_character_name(char_key, stats=None):
    return str(char_key or "").replace(".", " ").replace("_", " ").strip().title() or "Unknown"


def format_stat_value(stat_key, raw_value):
    value = shared_clean_value(raw_value, default="?")
    if value in {"", "?"}:
        return "?"
    if stat_key in _FRAME_SUFFIX_KEYS:
        text = str(value).strip()
        return text if text.endswith("f") else f"{text}f"
    return str(value).strip()


def iter_stats_fields(stats, stat_keys=None):
    if not stats:
        return []
    keys = STAT_DISPLAY_ORDER if stat_keys is None else tuple(key for key in STAT_DISPLAY_ORDER if key in stat_keys)
    fields = []
    for key in keys:
        if key not in stats:
            continue
        fields.append((STAT_FIELD_LABELS.get(key, key), format_stat_value(key, stats.get(key))))
    return fields


def build_stats_text_block(char_key, stats, stat_keys=None):
    fields = iter_stats_fields(stats, stat_keys)
    if not fields:
        return ""
    title = display_character_name(char_key, stats)
    lines = [f"**{title} Stats**"]
    lines.extend(f"{label}: {value}" for label, value in fields)
    return "\n".join(lines)


def build_character_stats_embed(char_key, stats, stat_keys=None):
    title_name = display_character_name(char_key, stats)
    fields = iter_stats_fields(stats, stat_keys)
    embed = discord.Embed(
        title=shared_truncate_value(f"{title_name} — Character Stats", 256),
        colour=BRAND,
    )
    for label, value in fields:
        shared_add_embed_field(embed, label, value, inline=True)
    if not fields:
        embed.description = "No matching stats were found for that query."
    return embed


def build_character_stats_comparison_embed(char_key_a, stats_a, char_key_b, stats_b, stat_keys=None):
    name_a = display_character_name(char_key_a, stats_a)
    name_b = display_character_name(char_key_b, stats_b)
    fields_a = iter_stats_fields(stats_a, stat_keys)
    fields_b = dict(iter_stats_fields(stats_b, stat_keys))
    embed = discord.Embed(
        title=shared_truncate_value(f"{name_a} vs {name_b} — Stats", 256),
        colour=BRAND,
    )
    if not fields_a and not fields_b:
        embed.description = "No matching stats were found for that comparison."
        return embed
    for label, value_a in fields_a:
        value_b = fields_b.get(label, "?")
        shared_add_embed_field(
            embed,
            label,
            f"**{name_a}:** {value_a}\n**{name_b}:** {value_b}",
            inline=True,
        )
    for label, value_b in fields_b.items():
        if any(existing_label == label for existing_label, _ in fields_a):
            continue
        shared_add_embed_field(
            embed,
            label,
            f"**{name_a}:** ?\n**{name_b}:** {value_b}",
            inline=True,
        )
    return embed


def should_attach_reversal_frame_row(stats_intent, text_lower):
    if re.search(r"\breversal\b", text_lower):
        return True
    return "bestReversal" in (stats_intent.stat_keys or ())


def append_reversal_frame_row(char_key, stats, results, lookup_frame_data):
    reversal_name = shared_clean_value((stats or {}).get("bestReversal", ""), default="")
    if not reversal_name or reversal_name == "?":
        return results
    rev_row = lookup_frame_data(char_key, reversal_name)
    if rev_row and rev_row not in results:
        return results + [rev_row]
    return results


def apply_stats_context(
    text_lower,
    mentioned_chars,
    frame_stats,
    lookup_frame_data,
    results,
    formatted_blocks,
    *,
    explicit_move_attempt=False,
    wants_frame_data=False,
    gif_query=False,
    property_only_query=False,
    startup_alias_query=False,
    charge_button_query=False,
    move_results_found=False,
):
    stats_intent = parse_stats_intent(
        text_lower,
        mentioned_chars,
        explicit_move_attempt=explicit_move_attempt,
        wants_frame_data=wants_frame_data,
        gif_query=gif_query,
        property_only_query=property_only_query,
        startup_alias_query=startup_alias_query,
        charge_button_query=charge_button_query,
        move_results_found=move_results_found,
    )
    if not stats_intent.wants_stats:
        return stats_intent, results, formatted_blocks

    for char in stats_intent.character_keys:
        stats_row = frame_stats.get(char)
        if not stats_row:
            continue
        stats_block = build_stats_text_block(char, stats_row, stats_intent.stat_keys)
        if stats_block:
            formatted_blocks.append(stats_block)
        if should_attach_reversal_frame_row(stats_intent, text_lower):
            results = append_reversal_frame_row(char, stats_row, results, lookup_frame_data)
    return stats_intent, results, formatted_blocks


def character_key_from_row(row, normalize_char_name):
    if not normalize_char_name:
        return ""
    return normalize_char_name((row or {}).get("char_name", ""))


def sf6_stat_slash_autocomplete_values():
    """Human-readable stat choices for `/sf6-stats` autocomplete."""
    values = ["All stats"]
    seen = {values[0].lower()}
    for label in STAT_FIELD_LABELS.values():
        if label.lower() not in seen:
            values.append(label)
            seen.add(label.lower())
    notation_labels = {
        "66": "Forward dash",
        "44": "Back dash",
        "8": "Neutral jump",
        "7": "Back jump",
        "9": "Forward jump",
    }
    for notation, hint in notation_labels.items():
        entry = f"{notation} — {hint}"
        if entry.lower() not in seen:
            values.append(entry)
            seen.add(entry.lower())
    if "drive rush — dr" not in seen:
        values.append("Drive rush (dr)")
    return values


def normalize_slash_stat_input(stat_input):
    text = str(stat_input or "").strip()
    if not text:
        return ""
    if re.search(r"\s*[—–]\s*", text):
        text = re.split(r"\s*[—–]\s*", text, maxsplit=1)[0].strip()
    paren = re.search(r"\(([^)]+)\)\s*$", text)
    if paren:
        inner = paren.group(1).strip()
        if inner:
            return inner
    return re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()


def build_slash_stats_query(char_name, stat_input=None):
    stat_text = normalize_slash_stat_input(stat_input)
    if not stat_text or stat_text.lower() in {"all stats", "all"}:
        return f"{char_name} stats".strip().lower()
    return f"{char_name} {stat_text}".strip().lower()
