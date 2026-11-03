"""GGACR frame parser, embeds, hitbox/image/notes helpers."""

import os
import re

import discord
import pandas as pd

from bubbot.data.ggst_aliases import GGST_CHARACTER_ALIASES
from bubbot.data.ggacr_aliases import (
    GGACR_CHARACTER_ALIASES,
    GGACR_MOVE_ALIASES,
    GGACR_ONLY_CHARACTER_ALIASES,
    query_has_ggacr_game_tag,
    strip_ggacr_game_tags,
)
from bubbot.runtime.config import FRAME_DATA_ERROR_CONTACT_TEXT
from bubbot.utils.character_lookup import find_alias_positions_in_text, resolve_alias_key
from bubbot.utils.comparison_utils import find_comparison_rows, is_comparison_query
from bubbot.utils.image_cache_utils import import_cache_module, merge_nested_url_cache
from bubbot.utils.frame_match_utils import find_matching_rows_standard
from bubbot.utils.notation_match_utils import looks_like_notation_query, query_has_jump_motion_notation
from bubbot.utils.row_utils import unique_rows
from bubbot.utils.text_utils import compact_key, correct_alias_typos, normalize_query_terms, query_suffix_candidates, strip_noise_words, strip_query_terms
from bubbot.utils.sfstats_style import ACTION, BRAND, AllHitboxImagesButton, toggle_style
from bubbot.utils.emoji_text import strip_embed_emojis


GGACR_FRAME_DATA_FILE = "GGACR Frame Data.ods"
GGACR_FRAME_DATA = {}
GGACR_MOVE_IMAGE_URLS = {}
GGACR_HITBOX_DATA = {}
GGACR_MOVE_NOTES = {}
GGACR_EXCLUSIVE_CHAR_KEYS = set()
GGACR_MOVE_IMAGES_MODULE = "bubbot.data.ggacr_move_images"


def normalize_key(value):
    return compact_key(value).replace("the", "")


def normalize_move_token(value):
    text = str(value or "").lower().strip()
    text = text.replace("jumping", "j")
    text = re.sub(r"\b(?:jump|air)\s*\.?,?", "j.", text)
    text = text.replace("[", "hold")
    text = text.replace("hs", "h")
    return re.sub(r"[^a-z0-9]+", "", text)


def resolve_ggacr_media_url(url):
    text = str(url or "").strip()
    return re.sub(r"/images/thumb/([^/]+/[^/]+/[^/]+)/[^/]+$", r"/images/\1", text)


def query_has_explicit_ggacr_tag(text):
    return query_has_ggacr_game_tag(text)


def query_has_ggacr_notation(text):
    lowered = str(text or "").lower()
    return bool(
        re.search(r"(?:^|\s)j\s*\.\s*(?:[236]?[abcd]|[0-9]{2,3}[abcd])\b", lowered)
        or re.search(r"(?:^|\s)(?:[1-9][a-d]|[0-9]{2,6}[a-d]|[1-9]?\[?[a-d](?:\+[a-d])+\]?)(?:\s|$)", lowered)
        or re.search(r"(?:^|\s)[0-9x]+(?:~[0-9x]+)*[a-d](?:~[0-9x]+[a-d])*\b", lowered)
        or query_has_jump_motion_notation(lowered)
    )


def load_move_image_urls(module_name=GGACR_MOVE_IMAGES_MODULE):
    image_module = import_cache_module(module_name, "ggacr-images")
    if not image_module:
        return False
    normal_loaded = merge_nested_url_cache(
        GGACR_MOVE_IMAGE_URLS,
        getattr(image_module, "GGACR_MOVE_IMAGE_URLS", {}),
        char_key_fn=lambda value: str(value or "").strip().lower(),
        move_key_fn=normalize_move_token,
    )
    GGACR_HITBOX_DATA.clear()
    hitbox_loaded = 0
    for char_key, moves in (getattr(image_module, "GGACR_HITBOX_DATA", {}) or {}).items():
        if not isinstance(moves, dict):
            continue
        normalized_char = str(char_key or "").strip().lower()
        char_links = GGACR_HITBOX_DATA.setdefault(normalized_char, {})
        for move_key, links in moves.items():
            if isinstance(links, str):
                links = [links]
            clean_links = [str(link or "").strip() for link in (links or []) if str(link or "").strip()]
            if clean_links:
                char_links[normalize_move_token(move_key)] = clean_links
                hitbox_loaded += len(clean_links)
    GGACR_MOVE_NOTES.clear()
    notes_loaded = 0
    for char_key, moves in (getattr(image_module, "GGACR_MOVE_NOTES", {}) or {}).items():
        if not isinstance(moves, dict):
            continue
        normalized_char = str(char_key or "").strip().lower()
        char_notes = GGACR_MOVE_NOTES.setdefault(normalized_char, {})
        for move_key, notes in moves.items():
            clean_notes = str(notes or "").strip()
            if clean_notes:
                char_notes[normalize_move_token(move_key)] = clean_notes
                notes_loaded += 1
    print(f"[ggacr-images] loaded {normal_loaded} move image links, {hitbox_loaded} hitbox links, and {notes_loaded} notes", flush=True)
    return True


def _compact_char_bridge_key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _strive_key_matches_ggacr_char(strive_text, ggacr_key):
    strive_text = str(strive_text or "").strip().lower()
    ggacr_key = str(ggacr_key or "").strip().lower()
    if not strive_text or not ggacr_key:
        return False
    if ggacr_key == strive_text or ggacr_key.startswith(f"{strive_text}_"):
        return True
    strive_compact = _compact_char_bridge_key(strive_text)
    ggacr_compact = _compact_char_bridge_key(ggacr_key)
    if not strive_compact or not ggacr_compact:
        return False
    if strive_compact == ggacr_compact:
        return True
    return ggacr_compact.startswith(strive_compact) and len(ggacr_compact) > len(strive_compact)


def _bridge_ggst_aliases():
    valid_keys = set(GGACR_FRAME_DATA.keys())
    GGACR_CHARACTER_ALIASES.clear()
    for ggacr_key, rows in GGACR_FRAME_DATA.items():
        char_name = str(rows[0].get("char_name") or "").strip()
        if char_name:
            GGACR_CHARACTER_ALIASES[char_name.lower()] = ggacr_key
        GGACR_CHARACTER_ALIASES[ggacr_key] = ggacr_key
        GGACR_CHARACTER_ALIASES[ggacr_key.replace("_", " ")] = ggacr_key
        GGACR_CHARACTER_ALIASES[_compact_char_bridge_key(char_name)] = ggacr_key
    for alias, strive_key in GGST_CHARACTER_ALIASES.items():
        alias_text = str(alias or "").strip().lower()
        strive_text = str(strive_key or "").strip().lower()
        if not alias_text or not strive_text:
            continue
        for ggacr_key in valid_keys:
            if _strive_key_matches_ggacr_char(strive_text, ggacr_key):
                GGACR_CHARACTER_ALIASES[alias_text] = ggacr_key
                break
    for alias, ggacr_key in GGACR_ONLY_CHARACTER_ALIASES.items():
        if ggacr_key in valid_keys:
            GGACR_CHARACTER_ALIASES[str(alias or "").strip().lower()] = ggacr_key


def _mark_exclusive_characters():
    global GGACR_EXCLUSIVE_CHAR_KEYS
    shared_keys = set()
    for _alias, strive_key in GGST_CHARACTER_ALIASES.items():
        strive_text = str(strive_key or "").strip().lower()
        if not strive_text:
            continue
        for ggacr_key in GGACR_FRAME_DATA:
            if _strive_key_matches_ggacr_char(strive_text, ggacr_key):
                shared_keys.add(ggacr_key)
                break
    GGACR_EXCLUSIVE_CHAR_KEYS = {char_key for char_key in GGACR_FRAME_DATA if char_key not in shared_keys}


def resolve_character_key(text):
    return resolve_alias_key(text, GGACR_CHARACTER_ALIASES, GGACR_FRAME_DATA.keys(), normalize_fn=normalize_key)


def display_char_name(char_key):
    rows = GGACR_FRAME_DATA.get(char_key) or []
    if rows:
        return str(rows[0].get("char_name") or char_key).strip()
    return str(char_key or "Unknown").replace("_", " ").title()


def is_exclusive_character(char_key):
    return str(char_key or "").strip().lower() in GGACR_EXCLUSIVE_CHAR_KEYS


def load_frame_data(filename=None):
    global GGACR_FRAME_DATA
    GGACR_FRAME_DATA = {}
    filename = filename or GGACR_FRAME_DATA_FILE
    if not os.path.exists(filename):
        print(f"[ggacr] frame data file not found: {filename}", flush=True)
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
            GGACR_FRAME_DATA[rows[0]["char_key"]] = rows
            loaded += 1
    _bridge_ggst_aliases()
    _mark_exclusive_characters()
    print(f"[ggacr] Total characters loaded: {loaded}", flush=True)
    return bool(GGACR_FRAME_DATA)


def find_characters_in_text(text):
    return find_alias_positions_in_text(text, GGACR_CHARACTER_ALIASES, GGACR_FRAME_DATA.keys())


def normalize_move_query(query):
    text = str(query or "").lower().strip()
    text = strip_query_terms(text)
    text = strip_ggacr_game_tags(text)
    text = re.sub(
        r"\b(?:framedata|frame\s*data|frames?|data|gif|gifs|hitbox(?:es)?|images?|notes?|start\s*up|startup|active|recovery|total|on\s+hit|on\s+block|damage|dmg|guard|tension|gbp|gbm|prorate|cancel(?:l?able)?|invuln(?:erability)?|invul|attribute)\b",
        " ",
        text,
    )
    text = _normalize_ggacr_notation_spacing(text)
    text = re.sub(r"\bhs\b", "h", text)
    text = re.sub(r"\bheavy\s+slash\b", "h", text)
    if not query_has_ggacr_notation(text):
        text = strip_noise_words(text)
    compact = normalize_move_token(text)
    if text in GGACR_MOVE_ALIASES:
        return GGACR_MOVE_ALIASES[text]
    if compact in GGACR_MOVE_ALIASES:
        return GGACR_MOVE_ALIASES[compact]
    corrected_text = correct_alias_typos(text, GGACR_MOVE_ALIASES)
    if corrected_text != text:
        corrected_compact = normalize_move_token(corrected_text)
        if corrected_text in GGACR_MOVE_ALIASES:
            return GGACR_MOVE_ALIASES[corrected_text]
        if corrected_compact in GGACR_MOVE_ALIASES:
            return GGACR_MOVE_ALIASES[corrected_compact]
        return corrected_text
    return text


def _normalize_ggacr_notation_spacing(text):
    text = str(text or "").lower()
    text = re.sub(r"\bj\s+([abcd])\b", r"j.\1", text)
    text = re.sub(r"\b([1-9])\s+([abcd])\b", r"\1\2", text)
    return text


def row_match_keys(row):
    keys = set()
    for value in (row.get("numCmd"), row.get("moveName")):
        if str(value or "").strip():
            keys.add(normalize_move_token(value))
    return {key for key in keys if key}


def _ggacr_notation_query(query_key):
    return looks_like_notation_query(query_key, "digit_button")


def find_matching_rows(char_key, move_text):
    query = normalize_move_query(move_text)
    query_key = normalize_move_token(query)
    rows = GGACR_FRAME_DATA.get(char_key, []) or []
    return find_matching_rows_standard(
        rows,
        query,
        query_key,
        normalize_fn=normalize_move_token,
        looks_like_fn=_ggacr_notation_query,
        row_keys_fn=row_match_keys,
        dedupe_fn=unique_rows,
    )


def build_disambiguation_prompt(char_key, rows):
    lines = [f"Multiple GGACR moves match {display_char_name(char_key)}. Reply with the option number:"]
    for index, row in enumerate(rows, start=1):
        move_name = str(row.get("moveName") or "Unknown").strip()
        num_cmd = str(row.get("numCmd") or "?").strip()
        lines.append(f"{index}. {move_name}: `{num_cmd}`")
    return "\n".join(lines)


def find_moves_in_text(text):
    lowered = normalize_query_terms(text)
    hitbox_query = bool(re.search(r"\b(?:gif|gifs|hitbox|hitboxes|image|images|picture|pictures)\b", lowered))
    frame_query = bool(re.search(r"\b(?:framedata|frame\s*data|frames?|data)\b", lowered))
    game_query = query_has_explicit_ggacr_tag(lowered)
    notes_query = bool(re.search(r"\bnotes?\b", lowered))
    char_matches = find_characters_in_text(lowered)
    rows = []
    quiz_answer_too_broad = False
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
            "gif_query": hitbox_query,
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
            "mode": "gif" if hitbox_query else "frame",
            "rows": rows,
            "data": "\n\n".join(format_frame_data(row, include_notes=notes_query) for row in rows),
            "gif_query": hitbox_query,
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
        matches = []
        for move_candidate in query_suffix_candidates(move_text):
            normalized_candidate = normalize_move_query(move_candidate)
            if not normalized_candidate:
                continue
            matches = find_matching_rows(char_key, move_candidate)
            if matches:
                break
        if len(matches) > 1:
            return {
                "mode": "options",
                "rows": matches,
                "data": build_disambiguation_prompt(char_key, matches),
                "gif_query": hitbox_query,
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
    exclusive_char = bool(matched_char_key and is_exclusive_character(matched_char_key))
    return {
        "mode": "gif" if hitbox_query else "frame" if rows else "none",
        "rows": rows,
        "data": "\n\n".join(format_frame_data(row, include_notes=notes_query) for row in rows),
        "gif_query": hitbox_query,
        "frame_query": frame_query,
        "game_query": game_query,
        "notes_query": notes_query,
        "char_found": bool(char_matches),
        "char_key": matched_char_key,
        "exclusive_char": exclusive_char,
        "wants_comparison": is_comparison_query(lowered, char_matches),
        "explicit_move_attempt": bool(
            char_matches
            and (frame_query or hitbox_query or game_query or query_has_ggacr_notation(lowered))
        ),
        "missing_scrolls_query": bool(char_matches and not rows and (frame_query or hitbox_query or game_query)),
        "quiz_answer_too_broad": quiz_answer_too_broad,
    }


def get_notes_text(row):
    char_key = str(row.get("char_key", "")).strip().lower()
    num_cmd_key = normalize_move_token(row.get("numCmd", ""))
    cached_notes = (GGACR_MOVE_NOTES.get(char_key, {}) or {}).get(num_cmd_key)
    return str(cached_notes or row.get("extraInfo") or "").strip()


def get_move_image_url(row):
    char_key = str(row.get("char_key", "")).strip().lower()
    num_cmd_key = normalize_move_token(row.get("numCmd", ""))
    return resolve_ggacr_media_url(GGACR_MOVE_IMAGE_URLS.get((char_key, num_cmd_key), ""))


def get_hitbox_links(row, limit=4):
    char_key = str(row.get("char_key", "")).strip().lower()
    num_cmd_key = normalize_move_token(row.get("numCmd", ""))
    links = (GGACR_HITBOX_DATA.get(char_key, {}) or {}).get(num_cmd_key, [])
    clean_links = [resolve_ggacr_media_url(link) for link in list(links or []) if str(link or "").strip()]
    return clean_links[:limit] if limit is not None else clean_links


def get_media_links(row, limit=4):
    links = get_hitbox_links(row, limit=None)
    image_url = get_move_image_url(row)
    if image_url and image_url not in links:
        links.append(image_url)
    return links[:limit] if limit is not None else links


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


def build_frame_embed(row, show_notes=False):
    char_name = clean_value(row.get("char_name"), "Unknown")
    move_name = clean_value(row.get("moveName"), "Unknown")
    num_cmd = clean_value(row.get("numCmd"), "?")
    embed = discord.Embed(
        title=truncate_value(f"GGACR - {char_name}", 256),
        description=truncate_value(f"{move_name} ({num_cmd})", 4096),
        colour=BRAND,
    )
    add_embed_field(embed, "Startup", row.get("startup"), inline=True)
    add_embed_field(embed, "Active", row.get("active"), inline=True)
    add_embed_field(embed, "Recovery", row.get("recovery"), inline=True)
    add_embed_field(embed, "On Block", row.get("onBlock"), inline=True)
    add_embed_field(embed, "On Hit", row.get("onHit"), inline=True)
    add_embed_field(embed, "Damage", row.get("dmg"), inline=True)
    add_embed_field(embed, "Guard", row.get("guardLevel"), inline=True)
    add_embed_field(embed, "Tension", row.get("tension"), inline=True)
    add_embed_field(embed, "GBP", row.get("gbp"), inline=True)
    add_embed_field(embed, "GBM", row.get("gbm"), inline=True)
    add_embed_field(embed, "Prorate", row.get("prorate"), inline=True)
    add_embed_field(embed, "Attribute", row.get("attribute"), inline=True)
    add_embed_field(embed, "Cancel", row.get("cancel"), inline=True)
    add_embed_field(embed, "Invuln", row.get("invuln"), inline=True)
    if show_notes:
        add_long_embed_field(embed, "Notes", get_notes_text(row), inline=False)
    image_url = get_move_image_url(row)
    if image_url:
        embed.set_image(url=image_url)
    return strip_embed_emojis(embed)


class GGACRAllHitboxImagesButton(AllHitboxImagesButton):
    def __init__(self, row):
        super().__init__(get_media_links(row, limit=None))


class GGACRNotesButton(discord.ui.Button):
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
        "ggacr",
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
        sent = await message.reply("I have GGACR frame data for this move but no image link yet.")
        return [sent.id]
    sent = await message.reply("\n".join(links))
    return [sent.id]


def format_frame_data(row, include_notes=False):
    text = (
        f"Move: {row.get('moveName') or row.get('numCmd')} ({row.get('numCmd') or '?'})\n"
        f"Startup: {row.get('startup') or '-'} | Active: {row.get('active') or '-'} | Recovery: {row.get('recovery') or '-'}\n"
        f"On Block: {row.get('onBlock') or '-'} | On Hit: {row.get('onHit') or '-'}\n"
        f"Damage: {row.get('dmg') or '-'} | Guard: {row.get('guardLevel') or '-'} | Tension: {row.get('tension') or '-'}\n"
        f"GBP: {row.get('gbp') or '-'} | GBM: {row.get('gbm') or '-'} | Prorate: {row.get('prorate') or '-'}\n"
        f"Cancel: {row.get('cancel') or '-'} | Invuln: {row.get('invuln') or '-'}"
    )
    notes = get_notes_text(row)
    if include_notes and notes:
        text += f"\nNotes: {notes}"
    return text


load_move_image_urls()
