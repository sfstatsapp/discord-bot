"""Third Strike frame parser, embeds, hitbox/image/notes helpers."""

import os
import re

import discord
import pandas as pd
from bubbot.utils.frame_match_utils import prefer_grounded_rows

from bubbot.data.third_strike_aliases import (
    THIRD_STRIKE_CHARACTER_ALIASES,
    THIRD_STRIKE_CHARACTER_MOVE_ALIASES,
    THIRD_STRIKE_LOOKUP_WORDS,
    THIRD_STRIKE_MOVE_ALIASES,
)
from bubbot.utils.character_lookup import find_alias_positions_in_text, resolve_alias_key
from bubbot.utils.comparison_utils import find_comparison_rows, is_comparison_query
from bubbot.utils.image_cache_utils import import_cache_module, merge_nested_url_cache
from bubbot.utils.frame_match_utils import filter_rows_by_strength, match_rows_by_fuzzy_keys, normalized_query_words, parse_strength_qualifier
from bubbot.utils.notation_match_utils import (
    extract_jump_motion_command,
    find_rows_by_notation_prefix,
    looks_like_notation_query,
    notation_prefix_matches_row_key,
    query_has_jump_motion_notation,
)
from bubbot.utils.row_utils import unique_rows
from bubbot.utils.text_utils import compact_key, correct_alias_typos, normalize_query_terms, query_suffix_candidates, strip_noise_words, strip_query_terms
from bubbot.utils.sfstats_style import ACTION, BRAND, AllHitboxImagesButton, toggle_style
from bubbot.utils.emoji_text import strip_embed_emojis


THIRD_STRIKE_FRAME_DATA_FILE = "Third Strike Frame Data.ods"
THIRD_STRIKE_FRAME_DATA = {}
THIRD_STRIKE_MOVE_IMAGE_URLS = {}
THIRD_STRIKE_HITBOX_DATA = {}
THIRD_STRIKE_MOVE_NOTES = {}
THIRD_STRIKE_MOVE_IMAGES_MODULE = "bubbot.data.third_strike_move_images"


def normalize_key(value):
    return compact_key(value).replace("thirdstrike", "")


def normalize_move_token(value):
    text = str(value or "").lower().strip()
    text = text.replace("jumping", "j")
    text = re.sub(r"\b(?:jump|air)\s*\.?,?", "j.", text)
    text = text.replace("[", "hold")
    text = re.sub(r"\bclose\b", "cl", text)
    text = re.sub(r"\bfar\b", "far", text)
    text = re.sub(r"\bcrouch(?:ing)?\b", "2", text)
    text = re.sub(r"\bcr\b", "2", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def query_has_third_strike_notation(text):
    lowered = str(text or "").lower()
    return bool(
        re.search(r"(?:^|\s)j\s*\.\s*[lmh][pk]\b", lowered)
        or re.search(r"(?:^|\s)(?:cl|close|far|f)\s*\.\s*[lmh][pk]\b", lowered)
        or re.search(r"(?:^|\s)(?:[1-9][0-9]{0,5}[lmh]?[pk]|[1-9]?[lmh]?[pk](?:\+[lmh]?[pk])+)(?:\s|$)", lowered)
        or re.search(r"(?:^|\s)sa[123](?:\s|$)", lowered)
        or re.search(r"\b(?:(?:cr|st|cl|j|nj)\s*(?:lp|mp|hp|lk|mk|hk))\b", lowered)
        or re.search(r"\b(?:cr|st|cl|j|nj)(?:lp|mp|hp|lk|mk|hk)\b", lowered)
        or re.search(r"\b(?:lp|mp|hp|lk|mk|hk|ex|od)\s+(?:fireball|hadoken|hadouken|dp|srk|shoryuken|tatsu|tatsumaki|hurricane)\b", lowered)
        or query_has_jump_motion_notation(lowered)
    )


def unique_third_strike_rows(rows):
    seen = set()
    unique = []
    for row in rows or []:
        key = (
            str(row.get("char_key", "")).strip().lower(),
            normalize_move_token(row.get("moveName", "")),
            normalize_move_token(row.get("numCmd", "")),
            normalize_move_token(row.get("version", "")),
            normalize_move_token(row.get("state_key", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def load_move_image_urls(module_name=THIRD_STRIKE_MOVE_IMAGES_MODULE):
    image_module = import_cache_module(module_name, "third-strike-images")
    if not image_module:
        return False
    normal_loaded = merge_nested_url_cache(
        THIRD_STRIKE_MOVE_IMAGE_URLS,
        getattr(image_module, "THIRD_STRIKE_MOVE_IMAGE_URLS", {}),
        char_key_fn=lambda value: str(value or "").strip().lower(),
        move_key_fn=normalize_move_token,
    )
    THIRD_STRIKE_HITBOX_DATA.clear()
    hitbox_loaded = 0
    for char_key, moves in (getattr(image_module, "THIRD_STRIKE_HITBOX_DATA", {}) or {}).items():
        if not isinstance(moves, dict):
            continue
        normalized_char = str(char_key or "").strip().lower()
        char_links = THIRD_STRIKE_HITBOX_DATA.setdefault(normalized_char, {})
        for move_key, links in moves.items():
            if isinstance(links, str):
                links = [links]
            clean_links = [str(link or "").strip() for link in (links or []) if str(link or "").strip()]
            if clean_links:
                char_links[normalize_move_token(move_key)] = clean_links
                hitbox_loaded += len(clean_links)
    THIRD_STRIKE_MOVE_NOTES.clear()
    notes_loaded = 0
    for char_key, moves in (getattr(image_module, "THIRD_STRIKE_MOVE_NOTES", {}) or {}).items():
        if not isinstance(moves, dict):
            continue
        normalized_char = str(char_key or "").strip().lower()
        char_notes = THIRD_STRIKE_MOVE_NOTES.setdefault(normalized_char, {})
        for move_key, notes in moves.items():
            clean_notes = str(notes or "").strip()
            if clean_notes:
                char_notes[normalize_move_token(move_key)] = clean_notes
                notes_loaded += 1
    print(f"[third-strike-images] loaded {normal_loaded} move image links, {hitbox_loaded} hitbox links, and {notes_loaded} notes", flush=True)
    return True


def resolve_character_key(text):
    return resolve_alias_key(text, THIRD_STRIKE_CHARACTER_ALIASES, THIRD_STRIKE_FRAME_DATA.keys(), normalize_fn=normalize_key)


def display_char_name(char_key):
    rows = THIRD_STRIKE_FRAME_DATA.get(char_key) or []
    if rows:
        return str(rows[0].get("char_name") or char_key).strip()
    return str(char_key or "Unknown").replace("_", " ").title()


# ODS load


def load_frame_data(filename=None):
    global THIRD_STRIKE_FRAME_DATA
    THIRD_STRIKE_FRAME_DATA = {}
    filename = filename or THIRD_STRIKE_FRAME_DATA_FILE
    if not os.path.exists(filename):
        print(f"[third-strike] frame data file not found: {filename}", flush=True)
        return False
    xls = pd.ExcelFile(filename, engine="odf")
    loaded = 0
    for sheet_name in xls.sheet_names:
        if not sheet_name.endswith("Normal"):
            continue
        df = pd.read_excel(xls, sheet_name=sheet_name).fillna("")
        rows = []
        for row in df.to_dict("records"):
            char_key = str(row.get("char_key") or row.get("char_name") or sheet_name[: -len("Normal")]).strip().lower()
            move_name = str(row.get("moveName", "")).strip()
            num_cmd = str(row.get("numCmd", "")).strip()
            if not move_name and not num_cmd:
                continue
            row["char_key"] = char_key
            row["char_name"] = str(row.get("char_name") or sheet_name[: -len("Normal")]).strip()
            rows.append(row)
        if rows:
            if rows[0]["char_key"] == "yun":
                mark_yun_genei_jin_rows(rows)
            THIRD_STRIKE_FRAME_DATA[rows[0]["char_key"]] = rows
            THIRD_STRIKE_CHARACTER_ALIASES.setdefault(rows[0]["char_key"].replace("_", " "), rows[0]["char_key"])
            THIRD_STRIKE_CHARACTER_ALIASES.setdefault(str(rows[0]["char_name"]).lower(), rows[0]["char_key"])
            loaded += 1
    print(f"[third-strike] Total characters loaded: {loaded}", flush=True)
    return bool(THIRD_STRIKE_FRAME_DATA)


def mark_yun_genei_jin_rows(rows):
    in_genei_block = False
    for row in rows:
        move_name = normalize_move_token(row.get("moveName", ""))
        num_cmd = normalize_move_token(row.get("numCmd", ""))
        if move_name == "geneijin" and "sa3" in num_cmd:
            in_genei_block = True
            continue
        if in_genei_block:
            row["state_key"] = "genei_jin"
            row["state_label"] = "Genei Jin"


def find_characters_in_text(text):
    return find_alias_positions_in_text(text, THIRD_STRIKE_CHARACTER_ALIASES, THIRD_STRIKE_FRAME_DATA.keys())


def query_requests_genei_jin(value):
    text = str(value or "").lower()
    return bool(re.search(r"\b(?:genei\s*jin|genei|geneijin|sa3)\b", text))


def strip_genei_jin_terms(value):
    text = str(value or "").lower()
    stripped = re.sub(r"\b(?:during|in|with|install|activated|active)\b", " ", text)
    stripped = re.sub(r"\b(?:genei\s*jin|genei|geneijin|sa3)\b", " ", stripped)
    return re.sub(r"\s+", " ", stripped).strip()


def normalize_move_query(query, char_key=None):
    text = str(query or "").lower().strip()
    text = strip_query_terms(text)
    text = re.sub(r"<@!?\d+>", " ", text)
    text = re.sub(r"\b(?:3s|third\s*strike|street\s*fighter\s*(?:3|iii)|sf3|sfiii)\b", " ", text)
    text = re.sub(r"\b(?:framedata|frame\s*data|frames?|data|gif|gifs|hitbox(?:es)?|images?|pictures?|notes?|start\s*up|startup|active|recovery|total|on\s+hit|on\s+block|flawless\s+block|block\s+damage|rev\s+damage|guard\s+damage|damage|dmg|guard|attack\s+level|atk\s*lvl|atk\s*level|cancel(?:l?able)?|gatling|invuln(?:erability)?|invul|attribute|range|length|hit\s*-?\s*confirm|hitconfirm|confirm\s+window|confirm\s+timing|confirmable|super\s*gain|super\s*meter\s*gain|meter\s*gain|super\s*build|sa\s*gain|drive\s+gain|drive\s+chip|drive\s+dmg|drive\s+damage|hitstun|blockstun|stun|risc\s*gain|risc|proration|prorate|knockdown\s+adv(?:antage)?|kda|counter\s*hit\s+adv(?:antage)?|ch\s*adv)\b", " ", text)
    text = strip_noise_words(text)
    compact = normalize_move_token(text)
    if str(char_key or "").strip().lower() == "yun" and compact in {"genei", "geneijin", "sa3"}:
        return "236236P (SA3)"
    if str(char_key or "").strip().lower() == "yun" and query_requests_genei_jin(text):
        state_stripped = strip_genei_jin_terms(text)
        if state_stripped:
            text = state_stripped
    compact = normalize_move_token(text)
    char_aliases = THIRD_STRIKE_CHARACTER_MOVE_ALIASES.get(str(char_key or "").strip().lower(), {})
    corrected_text = correct_alias_typos(text, char_aliases, THIRD_STRIKE_MOVE_ALIASES)
    if corrected_text != text:
        text = corrected_text
        compact = normalize_move_token(text)

    strength_aliases = {
        "l": "L",
        "lp": "LP",
        "light punch": "LP",
        "m": "M",
        "mp": "MP",
        "medium punch": "MP",
        "h": "H",
        "hp": "HP",
        "heavy punch": "HP",
        "lk": "LK",
        "light kick": "LK",
        "mk": "MK",
        "medium kick": "MK",
        "hk": "HK",
        "heavy kick": "HK",
        "ex": "EX",
        "od": "EX",
    }

    def resolve_alias(alias_map):
        if text in alias_map:
            return alias_map[text]
        for alias, target in alias_map.items():
            if compact == normalize_move_token(alias):
                return target
        return None

    def expand_strength_for_target(strength, target):
        if strength not in {"L", "M", "H"}:
            return strength
        target_text = str(target or "").upper()
        first_command = re.split(r"\s|\(", target_text, maxsplit=1)[0]
        if "K" in first_command and "P" not in first_command:
            return f"{strength}K"
        return f"{strength}P"

    def resolve_strength_alias(alias_map):
        for strength_text, version in strength_aliases.items():
            prefix = f"{strength_text} "
            suffix = f" {strength_text}"
            remainder = ""
            if text.startswith(prefix):
                remainder = text[len(prefix) :].strip()
            elif text.endswith(suffix):
                remainder = text[: -len(suffix)].strip()
            if not remainder:
                continue
            remainder_compact = normalize_move_token(remainder)
            for alias, target in alias_map.items():
                if remainder == alias or remainder_compact == normalize_move_token(alias):
                    return f"{expand_strength_for_target(version, target)} {target}"
        return None

    char_target = resolve_alias(char_aliases)
    if char_target:
        return char_target
    char_strength_target = resolve_strength_alias(char_aliases)
    if char_strength_target:
        return char_strength_target
    global_target = resolve_alias(THIRD_STRIKE_MOVE_ALIASES)
    if global_target:
        return global_target
    global_strength_target = resolve_strength_alias(THIRD_STRIKE_MOVE_ALIASES)
    if global_strength_target:
        return global_strength_target
    return text


def row_match_keys(row):
    keys = set()
    for value in (row.get("numCmd"), row.get("moveName")):
        if str(value or "").strip():
            keys.add(normalize_move_token(value))
    return {key for key in keys if key}


def row_version_match_keys(row):
    keys = set()
    num_cmd = str(row.get("numCmd") or "").strip()
    version = str(row.get("version") or "").strip()
    if num_cmd and version:
        keys.add(normalize_move_token(f"{version} {num_cmd}"))
        keys.add(normalize_move_token(f"{num_cmd} {version}"))
    move_name = str(row.get("moveName") or "").strip()
    if move_name and version:
        keys.add(normalize_move_token(f"{version} {move_name}"))
        keys.add(normalize_move_token(f"{move_name} {version}"))
    return {key for key in keys if key}


def _version_matches_query(row, query_key):
    version_key = normalize_move_token(row.get("version", ""))
    if not version_key:
        return False
    if version_key.startswith("ex") and "ex" in query_key:
        return True
    return version_key and version_key in query_key


def row_is_air_variant(row):
    move_name = str(row.get("moveName") or "").lower()
    num_cmd = str(row.get("numCmd") or "").lower()
    version = str(row.get("version") or "").lower()
    return bool(
        "air" in num_cmd
        or move_name.startswith(("air ", "aerial "))
        or bool(re.search(r"\(\s*j\s*\)", f"{num_cmd} {version}"))
    )


def query_requests_air_variant(original_query, normalized_query):
    text = f"{original_query or ''} {normalized_query or ''}".lower()
    return bool(
        re.search(r"\b(?:air|aerial|jump(?:ing)?)\b", text)
        or re.search(r"\bj\s*\.\s*", text)
        or query_has_jump_motion_notation(text)
        or "(air" in text
        or "zanku" in text
        or "zankuu" in text
    )


# Move variant preference (air/ground, EX, Genei Jin)


def prefer_ground_or_air_rows(rows, original_query, normalized_query):
    unique = unique_third_strike_rows(rows)
    if len(unique) <= 1:
        return unique
    air_rows = [row for row in unique if row_is_air_variant(row)]
    if air_rows and query_requests_air_variant(original_query, normalized_query):
        return air_rows
    return prefer_grounded_rows(unique, f"{original_query} {normalized_query}")


def prefer_state_rows(char_key, rows, original_query, normalized_query):
    unique = unique_third_strike_rows(rows)
    if len(unique) <= 1 or str(char_key or "").strip().lower() != "yun":
        return unique
    genei_rows = [row for row in unique if str(row.get("state_key") or "").strip().lower() == "genei_jin"]
    normal_rows = [row for row in unique if str(row.get("state_key") or "").strip().lower() != "genei_jin"]
    if not genei_rows or not normal_rows:
        return unique
    return genei_rows if query_requests_genei_jin(f"{original_query or ''} {normalized_query or ''}") else normal_rows


def query_requests_ex_variant(original_query, normalized_query):
    text = f"{original_query or ''} {normalized_query or ''}".lower()
    return bool(re.search(r"\b(?:ex|od)\b", text))


def row_is_ex_variant(row):
    version_key = normalize_move_token(row.get("version", ""))
    num_cmd_key = normalize_move_token(row.get("numCmd", ""))
    move_name_key = normalize_move_token(row.get("moveName", ""))
    return bool(
        version_key.startswith("ex")
        or num_cmd_key.endswith(("pp", "kk"))
        or move_name_key.startswith("ex")
    )


def prefer_non_ex_rows(rows, original_query, normalized_query):
    unique = unique_third_strike_rows(rows)
    if len(unique) <= 1:
        return unique
    ex_rows = [row for row in unique if row_is_ex_variant(row)]
    if query_requests_ex_variant(original_query, normalized_query):
        return ex_rows or unique
    non_ex_rows = [row for row in unique if not row_is_ex_variant(row)]
    if len(non_ex_rows) == 1 and ex_rows:
        return non_ex_rows
    return unique


def apply_match_preferences(char_key, rows, original_query, normalized_query):
    state_rows = prefer_state_rows(char_key, rows, original_query, normalized_query)
    air_rows = prefer_ground_or_air_rows(state_rows, original_query, normalized_query)
    return prefer_non_ex_rows(air_rows, original_query, normalized_query)


def _third_strike_notation_query(query_key):
    return looks_like_notation_query(query_key, "third_strike")


def _find_matching_rows_unfiltered(char_key, move_text, query):
    query_key = normalize_move_token(query)
    if not query_key:
        return []
    rows = THIRD_STRIKE_FRAME_DATA.get(char_key, []) or []
    notation_query = _third_strike_notation_query(query_key)

    notation_matches = find_rows_by_notation_prefix(
        rows,
        query_key,
        normalize_fn=normalize_move_token,
        looks_like_fn=_third_strike_notation_query,
    )
    if notation_matches:
        version_filtered = [row for row in notation_matches if _version_matches_query(row, query_key)]
        return apply_match_preferences(char_key, version_filtered or notation_matches, move_text, query)

    exact = [
        row
        for row in rows
        if query_key in row_match_keys(row) or query_key in row_version_match_keys(row)
    ]
    if exact:
        return apply_match_preferences(char_key, exact, move_text, query)

    base_matches = []
    if notation_query:
        for row in rows:
            row_keys = row_match_keys(row)
            version_keys = row_version_match_keys(row)
            if any(
                key and notation_prefix_matches_row_key(query_key, key)
                for key in row_keys
            ):
                base_matches.append(row)
            elif query_key in version_keys:
                base_matches.append(row)
    else:
        for row in rows:
            row_keys = row_match_keys(row)
            version_keys = row_version_match_keys(row)
            if any(key and (key in query_key or (len(query_key) >= 3 and query_key in key)) for key in row_keys):
                base_matches.append(row)
            elif any(key and key in query_key for key in version_keys):
                base_matches.append(row)
    if base_matches:
        version_filtered = [row for row in base_matches if _version_matches_query(row, query_key)]
        return apply_match_preferences(char_key, version_filtered or base_matches, move_text, query)

    query_words = normalized_query_words(query)
    name_matches = []
    for row in rows:
        move_name = re.sub(r"[^a-z0-9]+", " ", str(row.get("moveName", "")).lower()).strip()
        num_cmd = re.sub(r"[^a-z0-9]+", " ", str(row.get("numCmd", "")).lower()).strip()
        version = re.sub(r"[^a-z0-9]+", " ", str(row.get("version", "")).lower()).strip()
        haystack = " ".join(part for part in (move_name, num_cmd if not notation_query else "", version) if part)
        if query_words and query_words in haystack:
            name_matches.append(row)
    if name_matches:
        version_filtered = [row for row in name_matches if _version_matches_query(row, query_key)]
        return apply_match_preferences(char_key, version_filtered or name_matches, move_text, query)

    fuzzy = match_rows_by_fuzzy_keys(
        rows,
        query_key,
        value_fields=("moveName", "numCmd", "version"),
        normalize_fn=normalize_move_token,
        cutoff=0.84,
        n=4,
    )
    return apply_match_preferences(char_key, unique_rows(fuzzy), move_text, query)


def find_matching_rows(char_key, move_text):
    air_command = extract_jump_motion_command(move_text)
    query = normalize_move_query(air_command or move_text, char_key=char_key)
    base_query, strengths, embedded_notation = parse_strength_qualifier(query)
    candidates = [query, base_query] if embedded_notation else [base_query]
    for candidate in candidates:
        matches = _find_matching_rows_unfiltered(char_key, move_text, candidate)
        matches = filter_rows_by_strength(matches, strengths)
        if matches:
            return matches
    return []


def build_disambiguation_prompt(char_key, rows):
    lines = [f"Multiple Third Strike moves match {display_char_name(char_key)}. Reply with the option number:"]
    for index, row in enumerate(rows, start=1):
        move_name = str(row.get("moveName") or "Unknown").strip()
        num_cmd = str(row.get("numCmd") or "?").strip()
        version = display_version_for_row(row)
        suffix = f" [{version}]" if version else ""
        lines.append(f"{index}. {move_name}: `{num_cmd}`{suffix}")
    return "\n".join(lines)


def find_single_character_multi_move_rows(char_key, move_text):
    parts = [part.strip() for part in re.split(r"\band\b", str(move_text or "").lower()) if part.strip()]
    if len(parts) < 2:
        return None
    rows = []
    for part in parts:
        matches = []
        for move_candidate in query_suffix_candidates(part):
            matches = find_matching_rows(char_key, move_candidate)
            if matches:
                break
        if len(matches) > 1:
            return {"needs_disambiguation": True, "rows": matches}
        if not matches:
            return None
        if matches[0] not in rows:
            rows.append(matches[0])
    return {"rows": rows} if len(rows) >= 2 else None


# Natural-language query entry


def find_moves_in_text(text):
    lowered = normalize_query_terms(text)
    image_query = bool(re.search(r"\b(?:gif|gifs|hitbox|hitboxes|image|images|picture|pictures)\b", lowered))
    frame_query = bool(re.search(r"\b(?:framedata|frame\s*data|frames?|data)\b", lowered))
    game_query = bool(re.search(r"\b(?:3s|third\s*strike|street\s*fighter\s*(?:3|iii)|sf3|sfiii)\b", lowered))
    notes_query = bool(re.search(r"\bnotes?\b", lowered))
    char_matches = find_characters_in_text(lowered)
    rows = []
    matched_char_key = char_matches[0][0] if char_matches else None
    comparison_result = find_comparison_rows(
        lowered,
        char_matches,
        find_characters_in_text=find_characters_in_text,
        find_rows_for_char=find_matching_rows,
    )
    if comparison_result and comparison_result.get("needs_disambiguation"):
        char_key = comparison_result["char_key"]
        matches = comparison_result["rows"]
        return {
            "mode": "options",
            "rows": matches,
            "data": build_disambiguation_prompt(char_key, matches),
            "gif_query": image_query,
            "frame_query": frame_query,
            "game_query": game_query,
            "notes_query": notes_query,
            "needs_disambiguation": True,
            "char_found": True,
            "char_key": char_key,
            "wants_comparison": True,
        }
    if comparison_result:
        rows = comparison_result["rows"]
        return {
            "mode": "gif" if image_query else "frame",
            "rows": rows,
            "data": "\n\n".join(format_frame_data(row, include_notes=notes_query) for row in rows),
            "gif_query": image_query,
            "frame_query": frame_query,
            "game_query": game_query,
            "notes_query": notes_query,
            "char_found": True,
            "char_key": comparison_result.get("char_key") or matched_char_key,
            "wants_comparison": True,
            "explicit_move_attempt": True,
            "missing_scrolls_query": False,
        }
    for char_key, start, end, _alias in char_matches:
        move_text = (lowered[:start] + " " + lowered[end:]).strip() if start >= 0 and end >= 0 else lowered
        multi_move_result = find_single_character_multi_move_rows(char_key, move_text)
        if multi_move_result and multi_move_result.get("needs_disambiguation"):
            matches = multi_move_result["rows"]
            return {
                "mode": "options",
                "rows": matches,
                "data": build_disambiguation_prompt(char_key, matches),
                "gif_query": image_query,
                "frame_query": frame_query,
                "game_query": game_query,
                "notes_query": notes_query,
                "needs_disambiguation": True,
                "char_found": True,
                "char_key": char_key,
            }
        if multi_move_result:
            rows = multi_move_result["rows"]
            return {
                "mode": "gif" if image_query else "frame",
                "rows": rows,
                "data": "\n\n".join(format_frame_data(row, include_notes=notes_query) for row in rows),
                "gif_query": image_query,
                "frame_query": frame_query,
                "game_query": game_query,
                "notes_query": notes_query,
                "char_found": True,
                "char_key": char_key,
                "wants_comparison": False,
                "explicit_move_attempt": True,
                "missing_scrolls_query": False,
            }
        matches = []
        for move_candidate in query_suffix_candidates(move_text):
            matches = find_matching_rows(char_key, move_candidate)
            if matches:
                break
        if len(matches) > 1:
            return {
                "mode": "options",
                "rows": matches,
                "data": build_disambiguation_prompt(char_key, matches),
                "gif_query": image_query,
                "frame_query": frame_query,
                "game_query": game_query,
                "notes_query": notes_query,
                "needs_disambiguation": True,
                "char_found": True,
                "char_key": char_key,
            }
        if matches:
            rows.append(matches[0])
            break
    return {
        "mode": "gif" if image_query else "frame" if rows else "none",
        "rows": rows,
        "data": "\n\n".join(format_frame_data(row, include_notes=notes_query) for row in rows),
        "gif_query": image_query,
        "frame_query": frame_query,
        "game_query": game_query,
        "notes_query": notes_query,
        "char_found": bool(char_matches),
        "char_key": matched_char_key,
        "wants_comparison": is_comparison_query(lowered, char_matches),
        "explicit_move_attempt": bool(char_matches and (frame_query or image_query or game_query or query_has_third_strike_notation(lowered))),
        "missing_scrolls_query": bool(char_matches and not rows and (frame_query or image_query or game_query)),
        }


# Discord embed output


def get_notes_text(row):
    char_key = str(row.get("char_key", "")).strip().lower()
    num_cmd_key = normalize_move_token(row.get("numCmd", ""))
    state_key = normalize_move_token(row.get("state_key", ""))
    if state_key:
        cached_notes = (THIRD_STRIKE_MOVE_NOTES.get(char_key, {}) or {}).get(normalize_move_token(f"{num_cmd_key} {state_key}"))
        if cached_notes:
            return str(cached_notes).strip()
    cached_notes = (THIRD_STRIKE_MOVE_NOTES.get(char_key, {}) or {}).get(num_cmd_key)
    return str(cached_notes or row.get("extraInfo") or "").strip()


def get_move_image_url(row):
    char_key = str(row.get("char_key", "")).strip().lower()
    num_cmd_key = normalize_move_token(row.get("numCmd", ""))
    state_key = normalize_move_token(row.get("state_key", ""))
    if state_key:
        state_url = THIRD_STRIKE_MOVE_IMAGE_URLS.get((char_key, normalize_move_token(f"{num_cmd_key} {state_key}")))
        if state_url:
            return state_url
    return THIRD_STRIKE_MOVE_IMAGE_URLS.get((char_key, num_cmd_key))


def get_hitbox_links(row, limit=4):
    char_key = str(row.get("char_key", "")).strip().lower()
    num_cmd_key = normalize_move_token(row.get("numCmd", ""))
    state_key = normalize_move_token(row.get("state_key", ""))
    if state_key:
        state_links = (THIRD_STRIKE_HITBOX_DATA.get(char_key, {}) or {}).get(normalize_move_token(f"{num_cmd_key} {state_key}"), [])
        clean_state_links = [str(link or "").strip() for link in list(state_links or []) if str(link or "").strip()]
        if clean_state_links:
            return clean_state_links[:limit] if limit is not None else clean_state_links
    links = (THIRD_STRIKE_HITBOX_DATA.get(char_key, {}) or {}).get(num_cmd_key, [])
    clean_links = [str(link or "").strip() for link in list(links or []) if str(link or "").strip()]
    return clean_links[:limit] if limit is not None else clean_links


def clean_value(value, default=""):
    text = str(value or "").strip()
    return text if text else default


def truncate_value(value, limit):
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def add_embed_field(embed, name, value, inline=True):
    clean = clean_value(value)
    if clean:
        embed.add_field(name=name, value=truncate_value(clean, 1024), inline=inline)


def add_long_embed_field(embed, name, value, inline=False):
    clean = clean_value(value)
    if not clean:
        return
    chunks = [clean[index : index + 1024] for index in range(0, len(clean), 1024)]
    for index, chunk in enumerate(chunks):
        embed.add_field(name=name if index == 0 else f"{name} cont.", value=chunk, inline=inline)


def display_version_for_row(row):
    version = clean_value(row.get("version"))
    if not version:
        return ""
    name_text = str(row.get("moveName", "") or "").lower()
    cmd_text = str(row.get("numCmd", "") or "").lower()
    if "air" in version.lower() and ("air" in name_text or "air" in cmd_text):
        version = re.sub(r"\s*\(\s*air\s*\)\s*", " ", version, flags=re.IGNORECASE)
        version = re.sub(r"\bair\b", " ", version, flags=re.IGNORECASE)
        version = re.sub(r"\s+", " ", version).strip()
    return version


def build_frame_embed(row, show_notes=False):
    char_name = clean_value(row.get("char_name"), "Unknown")
    move_name = clean_value(row.get("moveName"), "Unknown")
    num_cmd = clean_value(row.get("numCmd"), "?")
    version = display_version_for_row(row)
    description = f"{move_name} ({num_cmd})"
    if version:
        description = f"{description} [{version}]"
    state_label = clean_value(row.get("state_label"))
    if state_label:
        description = f"{description} - {state_label}"
    embed = discord.Embed(
        title=truncate_value(f"Third Strike - {char_name}", 256),
        description=truncate_value(description, 4096),
        colour=BRAND,
    )
    add_embed_field(embed, "Startup", row.get("startup"), inline=True)
    add_embed_field(embed, "Active", row.get("active"), inline=True)
    add_embed_field(embed, "Recovery", row.get("recovery"), inline=True)
    add_embed_field(embed, "On Block", row.get("onBlock"), inline=True)
    add_embed_field(embed, "On Hit", row.get("onHit"), inline=True)
    add_embed_field(embed, "Crouch Hit", row.get("onHitCrouch"), inline=True)
    add_embed_field(embed, "Damage", row.get("dmg"), inline=True)
    add_embed_field(embed, "Stun", row.get("stun"), inline=True)
    add_embed_field(embed, "Guard", row.get("guardLevel"), inline=True)
    add_embed_field(embed, "Parry", row.get("parry"), inline=True)
    add_embed_field(embed, "Cancel", row.get("cancel"), inline=True)
    add_embed_field(embed, "Kara Distance", row.get("karaDistance"), inline=True)
    if show_notes:
        add_long_embed_field(embed, "Notes", get_notes_text(row), inline=False)
    image_url = get_move_image_url(row)
    if image_url:
        embed.set_image(url=image_url)
    return strip_embed_emojis(embed)


class ThirdStrikeAllHitboxImagesButton(AllHitboxImagesButton):
    def __init__(self, row):
        super().__init__(get_hitbox_links(row, limit=None))


class ThirdStrikeNotesButton(discord.ui.Button):
    def __init__(self, row):
        self.frame_row = row
        self.notes_text = get_notes_text(row)
        super().__init__(label="Show Notes", style=ACTION, disabled=not self.notes_text, row=0)

    async def callback(self, interaction: discord.Interaction):
        if not hasattr(self.view, "build_embed"):
            await interaction.response.defer()
            return
        self.view.show_notes = not self.view.show_notes
        self.label = "Hide Notes" if self.view.show_notes else "Show Notes"
        self.style = toggle_style(self.view.show_notes)
        await interaction.response.edit_message(embed=self.view.build_embed(), view=self.view, attachments=self.view.initial_files())


async def send_frame_response(message, rows):
    from bubbot.features.menu_system import send_frame_result_messages

    return await send_frame_result_messages(
        message.channel,
        "third_strike",
        rows,
        owner_id=getattr(message.author, "id", None),
        menu_locked=False,
        source_message=message,
        prompt=str(getattr(message, "content", "") or ""),
    )


async def send_hitbox_response(message, rows):
    if not rows:
        return []
    links = []
    fallback_links = []
    for row in rows:
        links.extend(get_hitbox_links(row))
        image_url = get_move_image_url(row)
        if image_url:
            fallback_links.append(image_url)
    if links:
        sent = await message.reply("\n".join(links))
        return [sent.id]
    if fallback_links:
        sent = await message.reply("No dedicated Third Strike hitbox image found; showing the move image instead.\n" + "\n".join(fallback_links))
        return [sent.id]
    sent = await message.reply("I have Third Strike frame data for this move but no image link yet.")
    return [sent.id]


def format_frame_data(row, include_notes=False):
    version_text = display_version_for_row(row)
    version = f" [{version_text}]" if version_text else ""
    text = (
        f"Move: {row.get('moveName') or row.get('numCmd')} ({row.get('numCmd') or '?'}){version}\n"
        f"Startup: {row.get('startup') or '-'} | Active: {row.get('active') or '-'} | Recovery: {row.get('recovery') or '-'}\n"
        f"On Block: {row.get('onBlock') or '-'} | On Hit: {row.get('onHit') or '-'} | Crouch Hit: {row.get('onHitCrouch') or '-'}\n"
        f"Damage: {row.get('dmg') or '-'} | Stun: {row.get('stun') or '-'} | Guard: {row.get('guardLevel') or '-'} | Parry: {row.get('parry') or '-'}\n"
        f"Cancel: {row.get('cancel') or '-'} | Kara Distance: {row.get('karaDistance') or '-'}"
    )
    notes = get_notes_text(row)
    if include_notes and notes:
        text += f"\nNotes: {notes}"
    return text


load_move_image_urls()
