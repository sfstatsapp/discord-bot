"""Guilty Gear Strive frame parser, embeds, hitbox/notes helpers."""

import difflib
import json
import os
import re

import discord
import pandas as pd
from bubbot.utils.frame_match_utils import prefer_grounded_rows

from bubbot.utils.character_lookup import find_alias_positions_in_text, resolve_alias_key
from bubbot.utils.comparison_utils import find_comparison_rows, is_comparison_query
from bubbot.utils.discord_formatting import (
    add_embed_field as shared_add_embed_field,
    add_long_embed_field as shared_add_long_embed_field,
    clean_value as shared_clean_value,
    truncate_value as shared_truncate_value,
)
from bubbot.data.ggst_aliases import (
    GGST_CHARACTER_ALIASES,
    GGST_CHARACTER_MOVE_ALIASES,
    GGST_CHARACTER_STATE_SHEETS,
    GGST_CHARACTER_SUPPLEMENTAL_SHEETS,
    GGST_LOOKUP_WORDS,
    GGST_MOVE_ALIASES,
    GOLDLEWIS_SECURITY_STATE_SHEETS,
    NAGORIYUKI_BLOOD_STATE_SHEETS,
)
from bubbot.utils.image_cache_utils import import_cache_module, merge_nested_url_cache
from bubbot.utils.notation_match_utils import (
    find_rows_by_notation_prefix,
    looks_like_notation_query,
    notation_prefix_matches_row_key,
    query_has_jump_motion_notation,
)
from bubbot.utils.mediawiki_images import resize_mediawiki_thumb_url as shared_resize_mediawiki_thumb_url
from bubbot.utils.row_utils import row_key
from bubbot.utils.text_utils import compact_key, correct_alias_typos, normalize_query_terms, query_suffix_candidates, strip_noise_words, strip_query_terms, word_tokens
from bubbot.utils.sfstats_style import ACTION, BRAND, AllHitboxImagesButton, toggle_style
from bubbot.utils.emoji_text import strip_embed_emojis


GGST_FRAME_DATA_FILE = "GGST Frame Data.ods"
GGST_FRAME_DATA_FILE_CANDIDATES = [
    GGST_FRAME_DATA_FILE,
    "ggst-framedata.ods",
]
GGST_FRAME_DATA = {}
GGST_STATE_FRAME_DATA = {}
GGST_SUPPLEMENTAL_FRAME_DATA = {}
GGST_HITBOX_DATA = {}
GGST_MOVE_NOTES = {}
FRAME_IMAGE_THUMB_WIDTH = 220
GGST_MOVE_IMAGE_URLS = {
    ("ky", "jd"): "https://www.dustloop.com/wiki/images/thumb/2/2c/GGST_Ky_Kiske_jD.png/315px-GGST_Ky_Kiske_jD.png",
}
GGST_MOVE_IMAGES_MODULE = "bubbot.data.ggst_move_images"

def normalize_key(value):
    return compact_key(value)


def resize_mediawiki_thumb_url(url, thumb_width=FRAME_IMAGE_THUMB_WIDTH):
    return shared_resize_mediawiki_thumb_url(url, thumb_width)


def load_move_image_urls(module_name=GGST_MOVE_IMAGES_MODULE):
    image_module = import_cache_module(module_name, "ggst-images")
    if not image_module:
        return False
    data = {
        "normal": getattr(image_module, "GGST_MOVE_IMAGE_URLS", {}),
        "hitbox": getattr(image_module, "GGST_HITBOX_DATA", {}),
        "notes": getattr(image_module, "GGST_MOVE_NOTES", {}),
    }

    normal_loaded = merge_nested_url_cache(
        GGST_MOVE_IMAGE_URLS,
        (data or {}).get("normal") or {},
        char_key_fn=lambda value: str(value or "").strip().lower(),
        move_key_fn=normalize_move_token,
        url_fn=resize_mediawiki_thumb_url,
    )

    hitbox_loaded = 0
    GGST_HITBOX_DATA.clear()
    for char_key, moves in ((data or {}).get("hitbox") or {}).items():
        if not isinstance(moves, dict):
            continue
        normalized_char = str(char_key or "").strip().lower()
        char_links = GGST_HITBOX_DATA.setdefault(normalized_char, {})
        for move_key, links in moves.items():
            if isinstance(links, str):
                links = [links]
            clean_links = [resize_mediawiki_thumb_url(link) for link in (links or []) if str(link).strip()]
            if not clean_links:
                continue
            char_links[normalize_move_token(move_key)] = clean_links
            hitbox_loaded += len(clean_links)

    notes_loaded = 0
    GGST_MOVE_NOTES.clear()
    for char_key, moves in ((data or {}).get("notes") or {}).items():
        if not isinstance(moves, dict):
            continue
        normalized_char = str(char_key or "").strip().lower()
        char_notes = GGST_MOVE_NOTES.setdefault(normalized_char, {})
        for move_key, notes_text in moves.items():
            clean_notes = str(notes_text or "").strip()
            if not clean_notes:
                continue
            char_notes[normalize_move_token(move_key)] = clean_notes
            notes_loaded += 1

    print(f"[ggst-images] loaded {normal_loaded} move image links, {hitbox_loaded} hitbox links, and {notes_loaded} notes", flush=True)
    return True


def canonical_char_key(value):
    return str(value or "").strip().lower()


def display_char_name(char_key):
    for key in GGST_FRAME_DATA:
        if key == char_key:
            rows = GGST_FRAME_DATA.get(key) or []
            if rows:
                return str(rows[0].get("char_name") or key).strip()
    return str(char_key or "Unknown").title()


def resolve_character_key(text):
    return resolve_alias_key(text, GGST_CHARACTER_ALIASES, GGST_FRAME_DATA.keys(), normalize_fn=normalize_key)


def resolve_frame_data_file(filename=None):
    if filename:
        return filename if os.path.exists(filename) else None
    for candidate in GGST_FRAME_DATA_FILE_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


# ODS load (normal, state, supplemental sheets)


def load_frame_data(filename=None):
    global GGST_FRAME_DATA, GGST_STATE_FRAME_DATA, GGST_SUPPLEMENTAL_FRAME_DATA
    GGST_FRAME_DATA = {}
    GGST_STATE_FRAME_DATA = {}
    GGST_SUPPLEMENTAL_FRAME_DATA = {}
    filename = resolve_frame_data_file(filename)
    if not filename:
        print(f"[ggst] frame data file not found. Tried: {', '.join(GGST_FRAME_DATA_FILE_CANDIDATES)}", flush=True)
        return False

    xls = pd.ExcelFile(filename, engine="odf")
    loaded = 0
    for sheet_name in xls.sheet_names:
        if not sheet_name.endswith("Normal"):
            continue
        if sheet_name in {"IdealSheetNormal", "TemplateNormal"}:
            continue
        char_label = sheet_name[: -len("Normal")]
        char_key = canonical_char_key(char_label)
        df = pd.read_excel(xls, sheet_name=sheet_name).fillna("")
        rows = df.to_dict("records")
        clean_rows = []
        for row in rows:
            if not str(row.get("moveName", "")).strip() and not str(row.get("numCmd", "")).strip():
                continue
            row["char_key"] = char_key
            row["char_name"] = char_label
            clean_rows.append(row)
        if clean_rows:
            GGST_FRAME_DATA[char_key] = clean_rows
            GGST_CHARACTER_ALIASES.setdefault(char_key, char_key)
            GGST_CHARACTER_ALIASES.setdefault(char_label.lower(), char_key)
            loaded += 1

    for sheet_name, (state_key, state_label) in NAGORIYUKI_BLOOD_STATE_SHEETS.items():
        if sheet_name not in xls.sheet_names:
            continue
        df = pd.read_excel(xls, sheet_name=sheet_name).fillna("")
        state_rows = []
        for row in df.to_dict("records"):
            if not str(row.get("moveName", "")).strip() and not str(row.get("numCmd", "")).strip():
                continue
            row["char_key"] = "nagoriyuki"
            row["char_name"] = f"Nagoriyuki ({state_label})"
            row["state_key"] = state_key
            row["state_label"] = state_label
            state_rows.append(row)
        if state_rows:
            GGST_STATE_FRAME_DATA.setdefault("nagoriyuki", {})[state_key] = state_rows

    for sheet_name, (state_key, state_label) in GOLDLEWIS_SECURITY_STATE_SHEETS.items():
        if sheet_name not in xls.sheet_names:
            continue
        df = pd.read_excel(xls, sheet_name=sheet_name).fillna("")
        state_rows = []
        for row in df.to_dict("records"):
            if not str(row.get("moveName", "")).strip() and not str(row.get("numCmd", "")).strip():
                continue
            row["char_key"] = "goldlewis"
            row["char_name"] = f"Goldlewis ({state_label})"
            row["state_key"] = state_key
            row["state_label"] = state_label
            state_rows.append(row)
        if state_rows:
            GGST_STATE_FRAME_DATA.setdefault("goldlewis", {})[state_key] = state_rows

    for char_key, sheets in GGST_CHARACTER_STATE_SHEETS.items():
        for sheet_name, (state_key, state_label) in sheets.items():
            if sheet_name not in xls.sheet_names:
                continue
            df = pd.read_excel(xls, sheet_name=sheet_name).fillna("")
            state_rows = []
            base_name = display_char_name(char_key)
            for row in df.to_dict("records"):
                if not str(row.get("moveName", "")).strip() and not str(row.get("numCmd", "")).strip():
                    continue
                row["char_key"] = char_key
                row["char_name"] = f"{base_name} ({state_label})"
                row["state_key"] = state_key
                row["state_label"] = state_label
                state_rows.append(row)
            if state_rows:
                GGST_STATE_FRAME_DATA.setdefault(char_key, {})[state_key] = state_rows

    for char_key, sheets in GGST_CHARACTER_SUPPLEMENTAL_SHEETS.items():
        for sheet_name, sheet_label in sheets.items():
            if sheet_name not in xls.sheet_names:
                continue
            df = pd.read_excel(xls, sheet_name=sheet_name).fillna("")
            supplemental_rows = []
            base_name = display_char_name(char_key)
            for row in df.to_dict("records"):
                if not str(row.get("moveName", "")).strip() and not str(row.get("numCmd", "")).strip():
                    continue
                row["char_key"] = char_key
                row["char_name"] = f"{base_name} ({sheet_label})"
                row["state_key"] = normalize_key(sheet_label)
                row["state_label"] = sheet_label
                supplemental_rows.append(row)
            if supplemental_rows:
                GGST_SUPPLEMENTAL_FRAME_DATA.setdefault(char_key, []).extend(supplemental_rows)
    print(f"[ggst] Total characters loaded: {loaded}", flush=True)
    return bool(GGST_FRAME_DATA)


def normalize_move_query(query):
    text = str(query or "").lower().strip()
    text = strip_query_terms(text)
    text = re.sub(r"\b(?:ggst|guilty\s+gear|guilty|gear|strive)\b", " ", text)
    text = re.sub(r"\b(?:framedata|frame\s*data|frames?|data|gif|gifs|hitbox(?:es)?|images?|pictures?|start\s*up|startup|active|recovery|total|on\s+hit|on\s+block|flawless\s+block|block\s+damage|rev\s+damage|guard\s+damage|damage|dmg|guard|attack\s+level|atk\s*lvl|atk\s*level|cancel(?:l?able)?|gatling|invuln(?:erability)?|invul|attribute|range|length|hit\s*-?\s*confirm|hitconfirm|confirm\s+window|confirm\s+timing|confirmable|super\s*gain|super\s*meter\s*gain|meter\s*gain|super\s*build|sa\s*gain|drive\s+gain|drive\s+chip|drive\s+dmg|drive\s+damage|hitstun|blockstun|stun|risc\s*gain|risc|proration|prorate|knockdown\s+adv(?:antage)?|kda|counter\s*hit\s+adv(?:antage)?|ch\s*adv)\b", " ", text)
    text = re.sub(r"\bhs\b", "h", text)
    text = re.sub(r"\bheavy\s+slash\b", "h", text)
    text = strip_noise_words(text)
    compact = normalize_key(text)
    if text in GGST_MOVE_ALIASES:
        return GGST_MOVE_ALIASES[text]
    if compact in GGST_MOVE_ALIASES:
        return GGST_MOVE_ALIASES[compact]
    corrected_text = correct_alias_typos(text, GGST_MOVE_ALIASES)
    if corrected_text != text:
        corrected_compact = normalize_key(corrected_text)
        if corrected_text in GGST_MOVE_ALIASES:
            return GGST_MOVE_ALIASES[corrected_text]
        if corrected_compact in GGST_MOVE_ALIASES:
            return GGST_MOVE_ALIASES[corrected_compact]
        return corrected_text
    return text


def lookup_character_move_alias(char_key, text):
    """Return the character-scoped move alias for text, ignoring case, spaces, and punctuation."""
    aliases = GGST_CHARACTER_MOVE_ALIASES.get(char_key)
    text = str(text or "").lower().strip()
    if not aliases or not text:
        return None
    if text in aliases:
        return aliases[text]
    compact = normalize_key(text)
    for alias, target in aliases.items():
        if compact and normalize_key(alias) == compact:
            return target
    return None


def extract_nagoriyuki_blood_state(query):
    text = str(query or "").lower()
    state_key = None

    if re.search(r"\b(?:blood\s*rage|br)\b", text):
        state_key = "blood_rage"
    elif re.search(r"\b(?:blood\s*(?:level|lvl|lv)?\s*3|bl\s*3|l\s*3|lv\s*3|lvl\s*3|level\s*3)\b", text):
        state_key = "blood_l3"
    elif re.search(r"\b(?:blood\s*(?:level|lvl|lv)?\s*2|bl\s*2|l\s*2|lv\s*2|lvl\s*2|level\s*2)\b", text):
        state_key = "blood_l2"
    elif re.search(r"\b(?:blood\s*level|blood|bl)\b", text):
        state_key = "blood_l2"

    cleaned = text
    cleaned = re.sub(r"\b(?:blood\s*rage|br)\b", " ", cleaned)
    cleaned = re.sub(r"\b(?:blood\s*(?:level|lvl|lv)?\s*[23]|bl\s*[23]|l\s*[23]|lv\s*[23]|lvl\s*[23]|level\s*[23])\b", " ", cleaned)
    cleaned = re.sub(r"\b(?:blood\s*level|blood|bl)\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return state_key, cleaned


def extract_goldlewis_security_state(query):
    text = str(query or "").lower()
    state_key = None

    if re.search(r"\b(?:security\s*(?:level|lvl|lv)?\s*3|sec\s*3|sl\s*3|l\s*3|lv\s*3|lvl\s*3|level\s*3)\b", text):
        state_key = "security_l3"
    elif re.search(r"\b(?:security\s*(?:level|lvl|lv)?\s*2|sec\s*2|sl\s*2|l\s*2|lv\s*2|lvl\s*2|level\s*2)\b", text):
        state_key = "security_l2"

    cleaned = text
    cleaned = re.sub(r"\b(?:security\s*(?:level|lvl|lv)?\s*[23]|sec\s*[23]|sl\s*[23]|l\s*[23]|lv\s*[23]|lvl\s*[23]|level\s*[23])\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return state_key, cleaned


def extract_roboky_level_state(query):
    text = str(query or "").lower()
    state_key = None

    if re.search(r"\b(?:heat\s*)?(?:level|lvl|lv)\s*3\b", text):
        state_key = "level_3"
    elif re.search(r"\b(?:heat\s*)?(?:level|lvl|lv)\s*2\b", text):
        state_key = "level_2"

    cleaned = text
    cleaned = re.sub(r"\b(?:heat\s*)?(?:level|lvl|lv)\s*[23]\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return state_key, cleaned


def extract_ky_dragon_install_state(query):
    text = str(query or "").lower()
    state_key = "dragon_install" if re.search(r"\b(?:dragon\s*install|di)\b", text) else None
    cleaned = re.sub(r"\b(?:dragon\s*install|di)\b", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return state_key, cleaned


def extract_bedman_install_state(query):
    text = str(query or "").lower()
    state_key = "install" if re.search(r"\b(?:install|error\s*6e|malfunction)\b", text) else None
    cleaned = re.sub(r"\b(?:install|error\s*6e|malfunction)\b", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return state_key, cleaned


def query_prefers_supplemental_rows(char_key, query):
    text = str(query or "").lower()
    if char_key == "asuka" and re.search(r"\b(?:spell|spells|test\s*case|mana|bookmark)\b", text):
        return True
    if char_key == "faust" and re.search(r"\b(?:item|items|bomb|banana|donut|afro|hammer|weight|horn|trumpet|meteor|mini\s*faust)\b", text):
        return True
    return False


def is_followup_row(row):
    if str(row.get("followUp", "")).strip().lower() in {"true", "1", "yes"}:
        return True
    if str(row.get("char_key", "")).strip().lower() == "h. chaos":
        move_name = str(row.get("moveName", "")).strip().lower()
        num_cmd = str(row.get("numCmd", "")).strip().lower()
        return (
            move_name in {"at the ready (fire)", "at the ready (cancel)", "steady aim (fire)", "steady aim (cancel)"}
            or "during steady aim" in num_cmd
        )
    return False


def is_universal_followup_row(row):
    move_name = str(row.get("moveName", "")).strip().lower()
    return move_name in {"counter blitz", "finishing blow"}


def strip_followup_terms(query):
    text = str(query or "").lower()
    text = re.sub(r"\b(?:follow\s*ups?|followups?|follow\s*up)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_move_token(value):
    text = str(value or "").lower().strip()
    text = text.replace(" ", "")
    text = text.replace("jump", "j")
    text = text.replace("air", "j")
    text = text.replace("hs", "h")
    text = text.replace(".", "")
    return re.sub(r"[^a-z0-9>~+/-]", "", text)


load_move_image_urls()


def expand_or_command_alternatives(value):
    text = str(value or "").strip()
    has_or = re.search(r"\bor\b", text, re.IGNORECASE)
    has_slash = "/" in text and not re.search(r"\d+/\d+", text)
    if not has_or and not has_slash:
        return []
    if has_or:
        parts = re.split(r"\bor\b", text, flags=re.IGNORECASE)
    else:
        parts = text.split("/")
    first_segment = parts[0].strip() if parts else ""
    alternatives = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        alternatives.append(part)
        if re.fullmatch(r"[PKSHDpkshd]", part) and re.search(r"\d+[PKSHDpkshd]$", first_segment):
            numeric_prefix = re.sub(r"[PKSHDpkshd]+$", "", first_segment)
            alternatives.append(f"{numeric_prefix}{part}")
    deduped = []
    seen = set()
    for alternative in alternatives:
        key = normalize_move_token(alternative)
        if key and key not in seen:
            deduped.append(alternative)
            seen.add(key)
    return deduped


def row_matches_move(row, move_query):
    raw_query = str(move_query or "").lower().strip()
    raw_query_norm = normalize_move_token(raw_query)
    query = normalize_move_query(move_query)
    query_norm = normalize_move_token(query)
    if not query_norm:
        return False
    values = [
        row.get("numCmd", ""),
        row.get("cmnName", ""),
        row.get("plnCmd", ""),
        row.get("moveName", ""),
        row.get("dustloopKey", ""),
    ]
    for value in values:
        value_text = str(value or "").lower().strip()
        if query.lower() == value_text:
            return True
        if query_norm == normalize_move_token(value_text):
            return True
        for alternative in expand_or_command_alternatives(value_text):
            alternative_norm = normalize_move_token(alternative)
            if query_norm == alternative_norm or raw_query_norm == alternative_norm:
                return True
    query_words = set(re.findall(r"[a-z0-9]+", query.lower())) - GGST_LOOKUP_WORDS
    descriptive_query_words = {
        word for word in query_words
        if len(word) >= 3 and not any(char.isdigit() for char in word)
    }
    button_query_words = {word for word in query_words if word in {"p", "k", "s", "h", "d"}}
    numeric_variant_words = {word for word in query_words if word.isdigit()}
    if query_words and (descriptive_query_words or numeric_variant_words):
        move_words = set()
        for value in values:
            move_words.update(re.findall(r"[a-z0-9]+", str(value or "").lower()))
        if query_words.issubset(move_words):
            return True
        non_fuzzy_words = query_words - descriptive_query_words - button_query_words - numeric_variant_words
        if not descriptive_query_words or non_fuzzy_words or numeric_variant_words:
            return False
        if button_query_words and not button_query_words.issubset(move_words):
            return False
        if all(
            any(difflib.SequenceMatcher(None, query_word, move_word).ratio() >= 0.74 for move_word in move_words)
            for query_word in descriptive_query_words
        ):
            return True
    return False


def row_primary_command_matches_move(row, move_query):
    raw_query = str(move_query or "").lower().strip()
    raw_query_norm = normalize_move_token(raw_query)
    query = normalize_move_query(move_query)
    query_norm = normalize_move_token(query)
    if not query_norm:
        return False
    values = (row.get("numCmd", ""), row.get("cmnName", ""), row.get("plnCmd", ""),
              row.get("moveName", ""))
    for value_text in values:
        value_text = str(value_text or "").lower().strip()
        if query.lower() == value_text or query_norm == normalize_move_token(value_text):
            return True
        for alternative in expand_or_command_alternatives(value_text):
            alternative_norm = normalize_move_token(alternative)
            if query_norm == alternative_norm or raw_query_norm == alternative_norm:
                return True
    return False


def equivalent_frame_row_key(row):
    return (
        normalize_key(row.get("moveName", "")),
        clean_value(row.get("startup")),
        clean_value(row.get("active")),
        clean_value(row.get("recovery")),
        clean_value(row.get("onHit")),
        clean_value(row.get("onBlock")),
        clean_value(row.get("dmg")),
        clean_value(row.get("guardLevel")),
        clean_value(row.get("atkLvl")),
        clean_value(row.get("state_key")),
    )


def row_preference_score(row):
    num_cmd = str(row.get("numCmd", "") or "").lower()
    score = len(num_cmd)
    if " during " in num_cmd:
        score += 100
    if " or " in num_cmd:
        score += 25
    if num_cmd.startswith("en."):
        score += 10
    return score


def dedupe_equivalent_frame_rows(rows):
    selected = {}
    for row in rows or []:
        key = equivalent_frame_row_key(row)
        current = selected.get(key)
        if current is None or row_preference_score(row) < row_preference_score(current):
            selected[key] = row
    return sorted(selected.values(), key=row_preference_score)


def find_fuzzy_character_in_text(text):
    words = word_tokens(text)
    if not words:
        return None
    best = None
    alias_items = sorted(GGST_CHARACTER_ALIASES.items(), key=lambda item: len(item[0]), reverse=True)
    for alias, char_key in alias_items:
        alias_words = re.findall(r"[a-z0-9]+", alias.lower())
        if not alias_words:
            continue
        span_size = len(alias_words)
        for index in range(0, len(words) - span_size + 1):
            candidate = " ".join(words[index:index + span_size])
            score = difflib.SequenceMatcher(None, normalize_key(candidate), normalize_key(alias)).ratio()
            if score < 0.84:
                continue
            if best is None or score > best[0]:
                best = (score, char_key, candidate)
    if best:
        return best[1], best[2]
    return None


def lookup_frame_data(character, move_input, state_key=None):
    char_key = resolve_character_key(character)
    if not char_key:
        return None
    query = lookup_character_move_alias(char_key, move_input)
    if not query:
        query = normalize_move_query(move_input)
    if char_key == "nagoriyuki" and state_key:
        rows = GGST_STATE_FRAME_DATA.get(char_key, {}).get(state_key, [])
        matches = prefer_grounded_rows([row for row in rows if row_matches_move(row, query)], query)
        if matches:
            return matches[0]

    rows = GGST_FRAME_DATA.get(char_key, [])
    matches = prefer_grounded_rows([row for row in rows if row_matches_move(row, query)], query)
    return matches[0] if matches else None


def find_matching_rows(character, move_input, state_key=None):
    char_key = resolve_character_key(character)
    if not char_key:
        return []
    query = lookup_character_move_alias(char_key, move_input)
    if not query:
        query = normalize_move_query(move_input)

    # Exact moveName match: if query matches exactly one move's moveName,
    # return it directly (avoids ambiguous disambiguation when the same
    # name like "Thrust" appears with different numCmds such as 41236K vs 41236K 4).
    exact_name_matches = []
    for search_rows in (GGST_FRAME_DATA.get(char_key, []), GGST_SUPPLEMENTAL_FRAME_DATA.get(char_key, [])):
        for row in search_rows:
            if query.lower() == str(row.get("moveName", "")).lower().strip():
                exact_name_matches.append(row)
    # Also search state data
    for state_key_name, state_rows in GGST_STATE_FRAME_DATA.get(char_key, {}).items():
        for row in state_rows:
            if query.lower() == str(row.get("moveName", "")).lower().strip():
                exact_name_matches.append(row)
    if exact_name_matches:
        return dedupe_equivalent_frame_rows(prefer_grounded_rows(exact_name_matches, query))

    if state_key:
        state_matches = []
        seen = set()
        for row in GGST_STATE_FRAME_DATA.get(char_key, {}).get(state_key, []):
            key = row_key(row, ("char_name", "moveName", "numCmd", "state_key"))
            if key in seen:
                continue
            if row_matches_move(row, query):
                state_matches.append(row)
                seen.add(key)
        if state_matches:
            return prefer_grounded_rows(state_matches, query)

    supplemental_first = query_prefers_supplemental_rows(char_key, move_input)
    if supplemental_first:
        supplemental_matches = []
        seen_supplemental = set()
        for row in GGST_SUPPLEMENTAL_FRAME_DATA.get(char_key, []):
            key = row_key(row, ("char_name", "moveName", "numCmd", "state_key"))
            if key in seen_supplemental:
                continue
            if row_matches_move(row, query):
                supplemental_matches.append(row)
                seen_supplemental.add(key)
        if supplemental_matches:
            return prefer_grounded_rows(supplemental_matches, query)

    rows_to_search = []
    rows_to_search.extend(GGST_FRAME_DATA.get(char_key, []))
    if not supplemental_first:
        rows_to_search.extend(GGST_SUPPLEMENTAL_FRAME_DATA.get(char_key, []))

    query_compact = normalize_key(query)
    notation_matches = []
    if looks_like_notation_query(query_compact, "ggst"):
        notation_matches = find_rows_by_notation_prefix(
            rows_to_search,
            query_compact,
            normalize_fn=normalize_key,
            looks_like_fn=lambda key: looks_like_notation_query(key, "ggst"),
        )
    if notation_matches:
        primary_matches = [row for row in notation_matches if row_primary_command_matches_move(row, query)]
        if primary_matches:
            return dedupe_equivalent_frame_rows(prefer_grounded_rows(primary_matches, query))
        return dedupe_equivalent_frame_rows(prefer_grounded_rows(notation_matches, query))

    matches = []
    seen = set()
    for row in rows_to_search:
        key = row_key(row, ("char_name", "moveName", "numCmd", "state_key"))
        if key in seen:
            continue
        if row_matches_move(row, query):
            matches.append(row)
            seen.add(key)
    primary_matches = [row for row in matches if row_primary_command_matches_move(row, query)]
    if primary_matches:
        return dedupe_equivalent_frame_rows(prefer_grounded_rows(primary_matches, query))
    return dedupe_equivalent_frame_rows(prefer_grounded_rows(matches, query))


def find_followup_rows(character, move_input):
    char_key = resolve_character_key(character)
    if not char_key:
        return []
    stripped_query = strip_followup_terms(move_input)
    query = lookup_character_move_alias(char_key, stripped_query)
    if not query:
        query = normalize_move_query(stripped_query)
    rows_to_search = [
        row for row in GGST_FRAME_DATA.get(char_key, [])
        if is_followup_row(row) and not is_universal_followup_row(row)
    ]
    if not query:
        return rows_to_search

    query_compact = normalize_key(query)
    if char_key == "h. chaos":
        at_ready_keys = {"236s", "236h", "attheready", "gun"}
        steady_aim_keys = {"214s", "steadyaim"}
        if query_compact in at_ready_keys:
            return [
                row for row in rows_to_search
                if str(row.get("moveName", "")).strip().lower().startswith("at the ready (")
            ]
        if query_compact in steady_aim_keys:
            return [
                row for row in rows_to_search
                if (
                    str(row.get("moveName", "")).strip().lower().startswith("steady aim (")
                    or "during steady aim" in str(row.get("numCmd", "")).strip().lower()
                )
            ]

    matches = []
    seen = set()
    for row in rows_to_search:
        key = row_key(row, ("char_name", "moveName", "numCmd", "state_key"))
        if key in seen:
            continue
        row_cmd_compact = normalize_key(row.get("numCmd", ""))
        if looks_like_notation_query(query_compact, "ggst"):
            cmd_match = notation_prefix_matches_row_key(query_compact, row_cmd_compact)
        else:
            cmd_match = row_cmd_compact.startswith(query_compact)
        if cmd_match or row_matches_move(row, query):
            matches.append(row)
            seen.add(key)
    return matches


def build_disambiguation_prompt(char_key, rows):
    char_name = display_char_name(char_key)
    lines = [f"Multiple GGST moves match {char_name}. Reply with the option number:"]
    for index, row in enumerate(rows, start=1):
        state_label = clean_value(row.get("state_label"))
        state_text = f" [{state_label}]" if state_label else ""
        move_name = clean_value(row.get("moveName"), "Unknown")
        num_cmd = clean_value(row.get("numCmd"), "?")
        lines.append(f"{index}. {move_name}{state_text}: `{num_cmd}`")
    return "\n".join(lines)


def build_followup_prompt(char_key, rows):
    char_name = display_char_name(char_key)
    lines = [f"**GGST Follow-up Options ({char_name})**"]
    lines.append("Follow-ups:")
    for index, row in enumerate(rows, start=1):
        move_name = clean_value(row.get("moveName"), "Unknown")
        num_cmd = clean_value(row.get("numCmd"), "?")
        lines.append(f"{index}. {move_name}: `{num_cmd}`")
    lines.append("Reply with the option number, or make a new prompt with the exact follow-up.")
    return "\n".join(lines)


def find_characters_in_text(text):
    return find_alias_positions_in_text(text, GGST_CHARACTER_ALIASES, GGST_FRAME_DATA.keys())


def find_comparison_matching_rows(char_key, move_text):
    state_key = None
    query_text = str(move_text or "")
    if char_key == "nagoriyuki":
        state_key, query_text = extract_nagoriyuki_blood_state(query_text)
    elif char_key == "robo-ky":
        state_key, query_text = extract_roboky_level_state(query_text)
    elif char_key == "goldlewis":
        state_key, query_text = extract_goldlewis_security_state(query_text)
    elif char_key == "ky":
        state_key, query_text = extract_ky_dragon_install_state(query_text)
    elif char_key == "bedman":
        state_key, query_text = extract_bedman_install_state(query_text)
    char_alias_text = re.sub(r"\b(?:framedata|frame\s*data|frames?|data|gif|gifs|hitbox(?:es)?|images?|pictures?|start\s*up|startup|active|recovery|total|on\s+hit|on\s+block|flawless\s+block|block\s+damage|rev\s+damage|guard\s+damage|damage|dmg|guard|attack\s+level|atk\s*lvl|atk\s*level|cancel(?:l?able)?|gatling|invuln(?:erability)?|invul|attribute|range|length|hit\s*-?\s*confirm|hitconfirm|confirm\s+window|confirm\s+timing|confirmable|super\s*gain|super\s*meter\s*gain|meter\s*gain|super\s*build|sa\s*gain|drive\s+gain|drive\s+chip|drive\s+dmg|drive\s+damage|hitstun|blockstun|stun|risc\s*gain|risc|proration|prorate|knockdown\s+adv(?:antage)?|kda|counter\s*hit\s+adv(?:antage)?|ch\s*adv)\b", " ", query_text)
    char_alias_text = re.sub(r"\s+", " ", char_alias_text).strip()
    char_move_alias = lookup_character_move_alias(char_key, char_alias_text)
    if char_move_alias:
        query_text = char_move_alias
    else:
        query_text = normalize_move_query(query_text)
        char_move_alias = lookup_character_move_alias(char_key, query_text)
        if char_move_alias:
            query_text = char_move_alias
    if not query_text:
        return []
    return find_matching_rows(char_key, query_text, state_key=state_key)


# Natural-language query entry


def find_moves_in_text(text):
    lowered = normalize_query_terms(text)
    gif_query = bool(re.search(r"\b(?:gif|gifs|hitbox|hitboxes|image|images|picture|pictures)\b", lowered))
    frame_query = bool(re.search(r"\b(?:framedata|frame\s*data|frames?|data)\b", lowered))
    game_query = bool(re.search(r"\b(?:ggst|guilty\s+gear|guilty|strive)\b", lowered))
    jump_motion_query = query_has_jump_motion_notation(lowered)
    char_matches = find_characters_in_text(lowered)
    if not char_matches:
        fuzzy_char = find_fuzzy_character_in_text(lowered)
        if fuzzy_char:
            char_key, matched_text = fuzzy_char
            start = lowered.find(matched_text)
            end = start + len(matched_text) if start >= 0 else start
            char_matches = [(char_key, start, end, matched_text)]
    rows = []
    char_found = bool(char_matches)
    matched_char_key = char_matches[0][0] if char_matches else None
    comparison_result = find_comparison_rows(
        lowered,
        char_matches,
        find_characters_in_text=find_characters_in_text,
        find_rows_for_char=find_comparison_matching_rows,
    )
    if comparison_result and comparison_result.get("needs_disambiguation"):
        char_key = comparison_result["char_key"]
        matches = comparison_result["rows"]
        return {
            "mode": "options",
            "rows": matches,
            "data": build_disambiguation_prompt(char_key, matches),
            "gif_query": gif_query,
            "frame_query": frame_query,
            "game_query": game_query,
            "needs_disambiguation": True,
            "char_found": char_found,
            "char_key": char_key,
            "wants_comparison": True,
        }
    if comparison_result:
        rows = comparison_result["rows"]
        data = "\n\n".join(format_frame_data(row) for row in rows)
        return {
            "mode": "gif" if gif_query else "frame",
            "rows": rows,
            "data": data,
            "gif_query": gif_query,
            "frame_query": frame_query,
            "game_query": game_query,
            "char_found": char_found,
            "char_key": comparison_result.get("char_key") or matched_char_key,
            "wants_comparison": True,
            "explicit_move_attempt": True,
            "missing_scrolls_query": False,
        }
    for char_key, start, end, _alias in char_matches:
        if start >= 0 and end >= 0:
            move_text = (lowered[:start] + " " + lowered[end:]).strip()
        else:
            move_text = lowered
        state_key = None
        if char_key == "nagoriyuki":
            state_key, move_text = extract_nagoriyuki_blood_state(move_text)
        elif char_key == "robo-ky":
            state_key, move_text = extract_roboky_level_state(move_text)
        elif char_key == "goldlewis":
            state_key, move_text = extract_goldlewis_security_state(move_text)
        elif char_key == "ky":
            state_key, move_text = extract_ky_dragon_install_state(move_text)
        elif char_key == "bedman":
            state_key, move_text = extract_bedman_install_state(move_text)
        matches = []
        for move_candidate in query_suffix_candidates(move_text):
            char_alias_text = re.sub(r"\b(?:framedata|frame\s*data|frames?|data|gif|gifs|hitbox(?:es)?|images?|pictures?|start\s*up|startup|active|recovery|total|on\s+hit|on\s+block|flawless\s+block|block\s+damage|rev\s+damage|guard\s+damage|damage|dmg|guard|attack\s+level|atk\s*lvl|atk\s*level|cancel(?:l?able)?|gatling|invuln(?:erability)?|invul|attribute|range|length|hit\s*-?\s*confirm|hitconfirm|confirm\s+window|confirm\s+timing|confirmable|super\s*gain|super\s*meter\s*gain|meter\s*gain|super\s*build|sa\s*gain|drive\s+gain|drive\s+chip|drive\s+dmg|drive\s+damage|hitstun|blockstun|stun|risc\s*gain|risc|proration|prorate|knockdown\s+adv(?:antage)?|kda|counter\s*hit\s+adv(?:antage)?|ch\s*adv)\b", " ", move_candidate)
            char_alias_text = re.sub(r"\s+", " ", char_alias_text).strip()
            candidate_text = lookup_character_move_alias(char_key, char_alias_text)
            if not candidate_text:
                candidate_text = normalize_move_query(move_candidate)
                candidate_text = lookup_character_move_alias(char_key, candidate_text) or candidate_text
            if not candidate_text:
                continue
            if re.search(r"\b(?:follow\s*ups?|followups?|follow\s*up)\b", candidate_text):
                followup_matches = find_followup_rows(char_key, candidate_text)
                if len(followup_matches) > 1:
                    return {
                        "mode": "options",
                        "rows": followup_matches,
                        "data": build_followup_prompt(char_key, followup_matches),
                        "gif_query": gif_query,
                        "frame_query": frame_query,
                        "game_query": game_query,
                        "needs_disambiguation": True,
                        "followup_options": True,
                        "char_found": char_found,
                        "char_key": char_key,
                    }
                if followup_matches:
                    rows.append(followup_matches[0])
                    break
            matches = find_matching_rows(char_key, candidate_text, state_key=state_key)
            if matches:
                break
        if rows:
            break
        if len(matches) > 1:
            return {
                "mode": "options",
                "rows": matches,
                "data": build_disambiguation_prompt(char_key, matches),
                "gif_query": gif_query,
                "frame_query": frame_query,
                "game_query": game_query,
                "needs_disambiguation": True,
            }
        if matches:
            rows.append(matches[0])
            break
    data = "\n\n".join(format_frame_data(row) for row in rows)
    return {
        "mode": "gif" if gif_query else "frame" if rows else "none",
        "rows": rows,
        "data": data,
        "gif_query": gif_query,
        "frame_query": frame_query,
        "game_query": game_query,
        "char_found": char_found,
        "char_key": matched_char_key,
        "wants_comparison": is_comparison_query(lowered, char_matches),
        "explicit_move_attempt": bool(char_found and (frame_query or gif_query or game_query or jump_motion_query)),
        "missing_scrolls_query": bool(char_found and not rows and (frame_query or gif_query or game_query or jump_motion_query)),
    }


# Discord embed output


def clean_value(value, default=""):
    return shared_clean_value(value, default)


def truncate_value(value, limit):
    return shared_truncate_value(value, limit)


def add_embed_field(embed, name, value, inline=True):
    shared_add_embed_field(embed, name, clean_value(value), inline=inline)


def add_long_embed_field(embed, name, value, inline=False, chunk_limit=1024):
    shared_add_long_embed_field(embed, name, clean_value(value), inline=inline, chunk_limit=chunk_limit)


def format_jsonish_list(value):
    text = clean_value(value)
    if not text:
        return ""
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        try:
            parts = json.loads(text)
            if isinstance(parts, list):
                return ", ".join(str(part).strip() for part in parts if str(part).strip())
        except Exception:
            pass
        text = text.strip("[]")
        parts = [part.strip().strip('"\'') for part in text.split(",")]
        return ", ".join(part for part in parts if part)
    return text


def get_notes_text(row):
    char_key = str(row.get("char_key", "")).strip().lower()
    num_cmd_key = normalize_move_token(row.get("numCmd", ""))
    cached_notes = (GGST_MOVE_NOTES.get(char_key, {}) or {}).get(num_cmd_key)
    if cached_notes:
        return cached_notes
    return format_jsonish_list(row.get("extraInfo"))


def format_frame_data(row, include_notes=False):
    text = (
        f"Move: {clean_value(row.get('moveName'))} ({clean_value(row.get('numCmd'))})\n"
        f"Startup: {clean_value(row.get('startup'), '-')}f | Active: {clean_value(row.get('active'), '-')}f | Recovery: {clean_value(row.get('recovery'), '-')}f\n"
        f"On Hit: {clean_value(row.get('onHit'), '-')} | On Block: {clean_value(row.get('onBlock'), '-')}\n"
        f"Damage: {clean_value(row.get('dmg'), '-')} | Guard: {clean_value(row.get('guardLevel'), '-')} | Attack Level: {clean_value(row.get('atkLvl'), '-')}\n"
        f"RISC Gain: {clean_value(row.get('riscGain'), '-')} | Proration: {clean_value(row.get('prorate'), '-')} | Knockdown Adv: {clean_value(row.get('kda'), '-')}"
    )
    notes = get_notes_text(row)
    if include_notes and notes:
        text += f"\nNotes: {notes}"
    return text


def get_move_image_url(row):
    return resize_mediawiki_thumb_url(
        GGST_MOVE_IMAGE_URLS.get(
            (str(row.get("char_key", "")).strip().lower(), normalize_move_token(row.get("numCmd", "")))
        )
    )


def build_frame_embed(row, show_notes=False):
    char_name = clean_value(row.get("char_name"), "Unknown")
    move_name = clean_value(row.get("moveName"), "Unknown")
    num_cmd = clean_value(row.get("numCmd"), "?")
    embed = discord.Embed(
        title=truncate_value(f"GGST - {char_name}", 256),
        description=truncate_value(f"{move_name} ({num_cmd})", 4096),
        colour=BRAND,
    )
    add_embed_field(embed, "Startup", row.get("startup"), inline=True)
    add_embed_field(embed, "Active", row.get("active"), inline=True)
    add_embed_field(embed, "Recovery", row.get("recovery"), inline=True)
    add_embed_field(embed, "Total", row.get("total"), inline=True)
    add_embed_field(embed, "On Hit", row.get("onHit"), inline=True)
    add_embed_field(embed, "On Block", row.get("onBlock"), inline=True)
    add_embed_field(embed, "Damage", row.get("dmg"), inline=True)
    add_embed_field(embed, "RISC Gain", row.get("riscGain"), inline=True)
    add_embed_field(embed, "Knockdown Adv", row.get("kda"), inline=True)
    add_embed_field(embed, "Counter Hit Adv", row.get("chAdv"), inline=True)
    add_embed_field(embed, "Guard", row.get("guardLevel"), inline=True)
    add_embed_field(embed, "Attack Level", row.get("atkLvl"), inline=True)
    add_embed_field(embed, "Cancel", format_jsonish_list(row.get("xx")), inline=True)
    add_embed_field(embed, "Gatling", format_jsonish_list(row.get("gatling")), inline=True)
    if show_notes:
        add_long_embed_field(embed, "Notes", get_notes_text(row), inline=False)
    image_url = get_move_image_url(row)
    if image_url:
        embed.set_image(url=image_url)
    return strip_embed_emojis(embed)


def get_hitbox_links(row, limit=4):
    # Placeholder for future GGST hitbox image assets.
    # Expected future key shape: GGST_HITBOX_DATA[char_key][normalized_num_cmd] -> [paths_or_urls]
    char_key = str(row.get("char_key", "")).strip().lower()
    num_cmd_key = normalize_move_token(row.get("numCmd", ""))
    char_links = GGST_HITBOX_DATA.get(char_key, {})
    links = char_links.get(num_cmd_key, []) if isinstance(char_links, dict) else []
    clean_links = [resize_mediawiki_thumb_url(link) for link in list(links or []) if str(link or "").strip()]
    return clean_links[:limit] if limit is not None else clean_links


def get_media_links(row, limit=4):
    links = get_hitbox_links(row, limit=None)
    image_url = get_move_image_url(row)
    if image_url and image_url not in links:
        links.append(image_url)
    return links[:limit] if limit is not None else links


class GGSTAllHitboxImagesButton(AllHitboxImagesButton):
    def __init__(self, row):
        super().__init__(get_media_links(row, limit=None))


class GGSTNotesButton(discord.ui.Button):
    def __init__(self, row):
        self.frame_row = row
        self.notes_text = get_notes_text(row)
        super().__init__(
            label="Show Notes",
            style=ACTION,
            disabled=not self.notes_text,
            row=0,
        )

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
        "ggst",
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
    for row in rows:
        links.extend(get_media_links(row))
    if not links:
        sent = await message.reply("I have GGST frame data for this move but no image link yet.")
        return [sent.id]
    sent = await message.reply("\n".join(links))
    return [sent.id]
