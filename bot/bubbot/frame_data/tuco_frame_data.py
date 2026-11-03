"""2XKO frame parser, embeds, hitbox/image helpers."""

import os
import re

import discord
import pandas as pd

from bubbot.data.tuco_aliases import TUCO_CHARACTER_ALIASES, TUCO_LOOKUP_WORDS, TUCO_MOVE_ALIASES
from bubbot.utils.character_lookup import find_alias_positions_in_text, resolve_alias_key
from bubbot.utils.comparison_utils import find_comparison_rows, is_comparison_query
from bubbot.utils.discord_formatting import (
    add_embed_field as shared_add_embed_field,
    add_long_embed_field as shared_add_long_embed_field,
    clean_value as shared_clean_value,
    truncate_value as shared_truncate_value,
)
from bubbot.utils.image_cache_utils import import_cache_module, merge_nested_url_cache
from bubbot.utils.frame_match_utils import find_matching_rows_standard
from bubbot.utils.notation_match_utils import looks_like_notation_query, query_has_jump_motion_notation
from bubbot.utils.row_utils import unique_rows
from bubbot.utils.text_utils import compact_key, correct_alias_typos, normalize_query_terms, query_suffix_candidates, strip_noise_words, strip_query_terms
from bubbot.utils.sfstats_style import ACTION, BRAND, toggle_style
from bubbot.utils.emoji_text import strip_embed_emojis


TUCO_FRAME_DATA_FILE = "2XKO Frame Data.ods"
TUCO_FRAME_DATA = {}
TUCO_MOVE_IMAGE_URLS = {}
TUCO_HITBOX_DATA = {}
TUCO_MOVE_IMAGES_MODULE = "bubbot.data.tuco_move_images"


def normalize_key(value):
    return compact_key(value)


def normalize_move_token(value):
    text = str(value or "").lower().strip()
    text = text.replace("jumping", "j")
    text = re.sub(r"\b(?:jump|air)\s*\.?", "j.", text)
    text = text.replace(" ", "")
    text = text.replace("+", "")
    text = text.replace("/", "")
    text = text.replace(".", "")
    text = text.replace("[", "hold")
    text = text.replace("]", "")
    text = text.replace("(", "")
    text = text.replace(")", "")
    text = text.replace("~", "")
    text = re.sub(r"[^a-z0-9]", "", text)
    return text


def load_move_image_urls(module_name=TUCO_MOVE_IMAGES_MODULE):
    image_module = import_cache_module(module_name, "2xko-images")
    if not image_module:
        return False
    normal_loaded = merge_nested_url_cache(
        TUCO_MOVE_IMAGE_URLS,
        getattr(image_module, "TUCO_MOVE_IMAGE_URLS", {}),
        char_key_fn=lambda value: str(value or "").strip().lower(),
        move_key_fn=normalize_move_token,
    )
    TUCO_HITBOX_DATA.clear()
    hitbox_loaded = 0
    for char_key, moves in (getattr(image_module, "TUCO_HITBOX_DATA", {}) or {}).items():
        if not isinstance(moves, dict):
            continue
        normalized_char = str(char_key or "").strip().lower()
        char_links = TUCO_HITBOX_DATA.setdefault(normalized_char, {})
        for move_key, links in moves.items():
            if isinstance(links, str):
                links = [links]
            clean_links = [str(link or "").strip() for link in (links or []) if str(link or "").strip()]
            if clean_links:
                char_links[normalize_move_token(move_key)] = clean_links
                hitbox_loaded += len(clean_links)
    print(f"[2xko-images] loaded {normal_loaded} move image links and {hitbox_loaded} hitbox links", flush=True)
    return True


def resolve_character_key(text):
    return resolve_alias_key(text, TUCO_CHARACTER_ALIASES, TUCO_FRAME_DATA.keys(), normalize_fn=normalize_key)


def display_char_name(char_key):
    rows = TUCO_FRAME_DATA.get(char_key) or []
    if rows:
        return str(rows[0].get("char_name") or char_key).strip()
    return str(char_key or "Unknown").title()


# ODS load


def load_frame_data(filename=None):
    global TUCO_FRAME_DATA
    TUCO_FRAME_DATA = {}
    filename = filename or TUCO_FRAME_DATA_FILE
    if not os.path.exists(filename):
        print(f"[2xko] frame data file not found: {filename}", flush=True)
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
            TUCO_FRAME_DATA[rows[0]["char_key"]] = rows
            TUCO_CHARACTER_ALIASES.setdefault(rows[0]["char_key"], rows[0]["char_key"])
            TUCO_CHARACTER_ALIASES.setdefault(str(rows[0]["char_name"]).lower(), rows[0]["char_key"])
            loaded += 1
    print(f"[2xko] Total characters loaded: {loaded}", flush=True)
    return bool(TUCO_FRAME_DATA)


def find_characters_in_text(text):
    return find_alias_positions_in_text(text, TUCO_CHARACTER_ALIASES, TUCO_FRAME_DATA.keys())


def normalize_move_query(query):
    text = str(query or "").lower().strip()
    text = strip_query_terms(text)
    text = re.sub(r"\b(?:2xko|tuco)\b", " ", text)
    text = re.sub(r"\b(?:framedata|frame\s*data|frames?|data|gif|gifs|hitbox(?:es)?|images?|start\s*up|startup|active|recovery|total|on\s+hit|on\s+block|flawless\s+block|block\s+damage|rev\s+damage|guard\s+damage|damage|dmg|guard|attack\s+level|atk\s*lvl|atk\s*level|cancel(?:l?able)?|gatling|invuln(?:erability)?|invul|attribute|range|length|hit\s*-?\s*confirm|hitconfirm|confirm\s+window|confirm\s+timing|confirmable|super\s*gain|super\s*meter\s*gain|meter\s*gain|super\s*build|sa\s*gain|drive\s+gain|drive\s+chip|drive\s+dmg|drive\s+damage|hitstun|blockstun|stun|risc\s*gain|risc|proration|prorate|knockdown\s+adv(?:antage)?|kda|counter\s*hit\s+adv(?:antage)?|ch\s*adv)\b", " ", text)
    text = strip_noise_words(text)
    compact = normalize_move_token(text)
    if text in TUCO_MOVE_ALIASES:
        return TUCO_MOVE_ALIASES[text]
    if compact in TUCO_MOVE_ALIASES:
        return TUCO_MOVE_ALIASES[compact]
    corrected_text = correct_alias_typos(text, TUCO_MOVE_ALIASES)
    if corrected_text != text:
        corrected_compact = normalize_move_token(corrected_text)
        if corrected_text in TUCO_MOVE_ALIASES:
            return TUCO_MOVE_ALIASES[corrected_text]
        if corrected_compact in TUCO_MOVE_ALIASES:
            return TUCO_MOVE_ALIASES[corrected_compact]
        return corrected_text
    return text


def row_match_keys(row):
    keys = set()
    for value in (row.get("numCmd"), row.get("moveName")):
        if str(value or "").strip():
            keys.add(normalize_move_token(value))
    return {key for key in keys if key}


def _tuco_notation_query(query_key):
    return looks_like_notation_query(query_key, "digit_button")


def find_matching_rows(char_key, move_text):
    query = normalize_move_query(move_text)
    query_key = normalize_move_token(query)
    rows = TUCO_FRAME_DATA.get(char_key, []) or []
    return find_matching_rows_standard(
        rows,
        query,
        query_key,
        normalize_fn=normalize_move_token,
        looks_like_fn=_tuco_notation_query,
        row_keys_fn=row_match_keys,
        dedupe_fn=unique_rows,
        fuzzy_cutoff=0.82,
    )


def build_disambiguation_prompt(char_key, rows):
    lines = [f"Multiple 2XKO moves match {display_char_name(char_key)}. Reply with the option number:"]
    for index, row in enumerate(rows, start=1):
        move_name = clean_value(row.get("moveName"), "Unknown")
        num_cmd = clean_value(row.get("numCmd"), "?")
        lines.append(f"{index}. {move_name}: `{num_cmd}`")
    return "\n".join(lines)


# Natural-language query entry


def find_moves_in_text(text):
    lowered = normalize_query_terms(text)
    gif_query = bool(re.search(r"\b(?:gif|gifs|hitbox|hitboxes)\b", lowered))
    frame_query = bool(re.search(r"\b(?:framedata|frame\s*data|frames?|data)\b", lowered))
    game_query = bool(re.search(r"\b(?:2xko|tuco)\b", lowered))
    jump_motion_query = query_has_jump_motion_notation(lowered)
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
            "gif_query": gif_query,
            "frame_query": frame_query,
            "game_query": game_query,
            "needs_disambiguation": True,
            "char_found": True,
            "char_key": char_key,
            "wants_comparison": True,
        }
    if comparison_result:
        rows = comparison_result["rows"]
        return {
            "mode": "gif" if gif_query else "frame",
            "rows": rows,
            "data": "\n\n".join(format_frame_data(row) for row in rows),
            "gif_query": gif_query,
            "frame_query": frame_query,
            "game_query": game_query,
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
            matches = find_matching_rows(char_key, normalized_candidate)
            if matches:
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
                "char_found": True,
                "char_key": char_key,
            }
        if matches:
            rows.append(matches[0])
            break
    return {
        "mode": "gif" if gif_query else "frame" if rows else "none",
        "rows": rows,
        "data": "\n\n".join(format_frame_data(row) for row in rows),
        "gif_query": gif_query,
        "frame_query": frame_query,
        "game_query": game_query,
        "char_found": bool(char_matches),
        "char_key": matched_char_key,
        "wants_comparison": is_comparison_query(lowered, char_matches),
        "explicit_move_attempt": bool(char_matches and (frame_query or gif_query or game_query or jump_motion_query)),
        "missing_scrolls_query": bool(char_matches and not rows and (frame_query or gif_query or game_query or jump_motion_query)),
        }


# Discord embed output


def clean_value(value, default=""):
    return shared_clean_value(value, default)


def truncate_value(value, limit):
    return shared_truncate_value(value, limit)


def add_embed_field(embed, name, value, inline=True):
    shared_add_embed_field(embed, name, clean_value(value), inline=inline)


def add_long_embed_field(embed, name, value, inline=False):
    shared_add_long_embed_field(embed, name, clean_value(value), inline=inline)


def get_notes_text(row):
    return clean_value(row.get("extraInfo"))


def format_frame_data(row, include_notes=False):
    text = (
        f"Move: {clean_value(row.get('moveName'))} ({clean_value(row.get('numCmd'))})\n"
        f"Startup: {clean_value(row.get('startup'), '-')}f | Active: {clean_value(row.get('active'), '-')}f | Recovery: {clean_value(row.get('recovery'), '-')}f\n"
        f"On Hit: {clean_value(row.get('onHit'), '-')} | On Block: {clean_value(row.get('onBlock'), '-')}\n"
        f"Damage: {clean_value(row.get('dmg'), '-')} | Guard: {clean_value(row.get('guardLevel'), '-')} | Meter Gain: {clean_value(row.get('meterGain'), '-')}\n"
        f"Cancel: {clean_value(row.get('xx'), '-')} | Invuln: {clean_value(row.get('invuln'), '-')}"
    )
    notes = get_notes_text(row)
    if include_notes and notes:
        text += f"\nNotes: {notes}"
    return text


def get_move_image_url(row):
    return TUCO_MOVE_IMAGE_URLS.get((str(row.get("char_key", "")).strip().lower(), normalize_move_token(row.get("numCmd", ""))))


def get_hitbox_links(row, limit=4):
    char_key = str(row.get("char_key", "")).strip().lower()
    num_cmd_key = normalize_move_token(row.get("numCmd", ""))
    links = (TUCO_HITBOX_DATA.get(char_key, {}) or {}).get(num_cmd_key, [])
    clean_links = [str(link or "").strip() for link in list(links or []) if str(link or "").strip()]
    return clean_links[:limit] if limit is not None else clean_links


def get_media_links(row, limit=4):
    links = get_hitbox_links(row, limit=None)
    image_url = get_move_image_url(row)
    if image_url and image_url not in links:
        links.append(image_url)
    return links[:limit] if limit is not None else links


def build_frame_embed(row, show_notes=False):
    char_name = clean_value(row.get("char_name"), "Unknown")
    move_name = clean_value(row.get("moveName"), "Unknown")
    num_cmd = clean_value(row.get("numCmd"), "?")
    embed = discord.Embed(
        title=truncate_value(f"2XKO - {char_name}", 256),
        description=truncate_value(f"{move_name} ({num_cmd})", 4096),
        colour=BRAND,
    )
    add_embed_field(embed, "Startup", row.get("startup"), inline=True)
    add_embed_field(embed, "Active", row.get("active"), inline=True)
    add_embed_field(embed, "Recovery", row.get("recovery"), inline=True)
    add_embed_field(embed, "On Hit", row.get("onHit"), inline=True)
    add_embed_field(embed, "On Block", row.get("onBlock"), inline=True)
    add_embed_field(embed, "Damage", row.get("dmg"), inline=True)
    add_embed_field(embed, "Guard", row.get("guardLevel"), inline=True)
    add_embed_field(embed, "Meter Gain", row.get("meterGain"), inline=True)
    add_embed_field(embed, "Cancel", row.get("xx"), inline=True)
    add_embed_field(embed, "Invuln", row.get("invuln"), inline=True)
    if show_notes:
        add_long_embed_field(embed, "Notes", get_notes_text(row), inline=False)
    image_url = get_move_image_url(row)
    if image_url:
        embed.set_image(url=image_url)
    return strip_embed_emojis(embed)


class TUCONotesButton(discord.ui.Button):
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
        "tuco",
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
        sent = await message.reply("I have 2XKO frame data for this move but no image link yet.")
        return [sent.id]
    sent = await message.reply("\n".join(links))
    return [sent.id]


load_move_image_urls()
