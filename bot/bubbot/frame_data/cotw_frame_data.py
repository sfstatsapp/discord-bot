"""City of the Wolves frame parser, embeds, regular image/notes helpers."""

import io
import os
import re
from urllib.parse import urlparse

import aiohttp
import discord
import pandas as pd

from bubbot.data.cotw_aliases import COTW_CHARACTER_ALIASES, COTW_MOVE_ALIASES
from bubbot.utils.character_lookup import find_alias_positions_in_text, resolve_alias_key
from bubbot.utils.comparison_utils import find_comparison_rows, is_comparison_query
from bubbot.utils.image_cache_utils import import_cache_module, merge_nested_url_cache
from bubbot.utils.frame_match_utils import find_matching_rows_standard
from bubbot.utils.notation_match_utils import looks_like_notation_query, query_has_jump_motion_notation
from bubbot.utils.row_utils import unique_rows
from bubbot.utils.text_utils import compact_key, correct_alias_typos, normalize_query_terms, query_suffix_candidates, strip_noise_words, strip_query_terms
from bubbot.utils.sfstats_style import ACTION, BRAND, toggle_style


COTW_FRAME_DATA_FILE = "COTW Frame Data.ods"
COTW_FRAME_DATA = {}
COTW_MOVE_IMAGE_URLS = {}
COTW_MOVE_NOTES = {}
COTW_MOVE_IMAGES_MODULE = "bubbot.data.cotw_move_images"


def normalize_key(value):
    return compact_key(value).replace(".", "")


def normalize_move_token(value):
    text = str(value or "").lower().strip()
    text = text.replace("jumping", "j")
    text = re.sub(r"\b(?:jump|air)\s*\.?,?", "j.", text)
    text = text.replace("[", "hold")
    return re.sub(r"[^a-z0-9]+", "", text)


def query_has_cotw_notation(text):
    lowered = str(text or "").lower()
    return bool(
        re.search(r"(?:^|\s)(?:j|jump|jumping|air)\s*\.?\s*[abcd]\b", lowered)
        or re.search(r"(?:^|\s)(?:cl|close)\s*\.?\s*[abcd]\b", lowered)
        or re.search(r"(?:^|\s)(?:f|far)\s*\.?\s*[abcd]\b", lowered)
        or re.search(r"(?:^|\s)(?:cr|crouch|crouching)\s*\.?\s*[abcd]\b", lowered)
        or re.search(r"(?:^|\s)(?:[1-9][0-9]{0,5}[abcd]|[1-9]?[abcd](?:\+[abcd])+)(?:\s|$)", lowered)
        or query_has_jump_motion_notation(lowered)
    )


def load_move_image_urls(module_name=COTW_MOVE_IMAGES_MODULE):
    image_module = import_cache_module(module_name, "cotw-images")
    if not image_module:
        return False
    normal_loaded = merge_nested_url_cache(
        COTW_MOVE_IMAGE_URLS,
        getattr(image_module, "COTW_MOVE_IMAGE_URLS", {}),
        char_key_fn=lambda value: str(value or "").strip().lower(),
        move_key_fn=normalize_move_token,
    )
    COTW_MOVE_NOTES.clear()
    notes_loaded = 0
    for char_key, moves in (getattr(image_module, "COTW_MOVE_NOTES", {}) or {}).items():
        if not isinstance(moves, dict):
            continue
        normalized_char = str(char_key or "").strip().lower()
        char_notes = COTW_MOVE_NOTES.setdefault(normalized_char, {})
        for move_key, notes in moves.items():
            clean_notes = str(notes or "").strip()
            if clean_notes:
                char_notes[normalize_move_token(move_key)] = clean_notes
                notes_loaded += 1
    print(f"[cotw-images] loaded {normal_loaded} move image links and {notes_loaded} notes", flush=True)
    return True


def resolve_character_key(text):
    return resolve_alias_key(text, COTW_CHARACTER_ALIASES, COTW_FRAME_DATA.keys(), normalize_fn=normalize_key)


def display_char_name(char_key):
    rows = COTW_FRAME_DATA.get(char_key) or []
    if rows:
        return str(rows[0].get("char_name") or char_key).strip()
    return str(char_key or "Unknown").replace("_", " ").title()


# ODS load


def load_frame_data(filename=None):
    global COTW_FRAME_DATA
    COTW_FRAME_DATA = {}
    filename = filename or COTW_FRAME_DATA_FILE
    if not os.path.exists(filename):
        print(f"[cotw] frame data file not found: {filename}", flush=True)
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
            COTW_FRAME_DATA[rows[0]["char_key"]] = rows
            COTW_CHARACTER_ALIASES.setdefault(rows[0]["char_key"].replace("_", " "), rows[0]["char_key"])
            COTW_CHARACTER_ALIASES.setdefault(str(rows[0]["char_name"]).lower(), rows[0]["char_key"])
            loaded += 1
    print(f"[cotw] Total characters loaded: {loaded}", flush=True)
    return bool(COTW_FRAME_DATA)


def find_characters_in_text(text):
    return find_alias_positions_in_text(text, COTW_CHARACTER_ALIASES, COTW_FRAME_DATA.keys())


def normalize_move_query(query):
    text = str(query or "").lower().strip()
    text = strip_query_terms(text)
    text = re.sub(r"\b(?:cotw|city\s+of\s+the\s+wolves|fatal\s+fury)\b", " ", text)
    text = re.sub(r"\b(?:framedata|frame\s*data|frames?|data|gif|gifs|hitbox(?:es)?|images?|pictures?|notes?|start\s*up|startup|active|recovery|total|on\s+hit|on\s+block|flawless\s+block|block\s+damage|rev\s+damage|guard\s+damage|damage|dmg|guard|attack\s+level|atk\s*lvl|atk\s*level|cancel(?:l?able)?|gatling|invuln(?:erability)?|invul|attribute|range|length|hit\s*-?\s*confirm|hitconfirm|confirm\s+window|confirm\s+timing|confirmable|super\s*gain|super\s*meter\s*gain|meter\s*gain|super\s*build|sa\s*gain|drive\s+gain|drive\s+chip|drive\s+dmg|drive\s+damage|hitstun|blockstun|stun|risc\s*gain|risc|proration|prorate|knockdown\s+adv(?:antage)?|kda|counter\s*hit\s+adv(?:antage)?|ch\s*adv)\b", " ", text)
    text = strip_noise_words(text)
    compact = normalize_move_token(text)
    if text in COTW_MOVE_ALIASES:
        return COTW_MOVE_ALIASES[text]
    if compact in COTW_MOVE_ALIASES:
        return COTW_MOVE_ALIASES[compact]
    corrected_text = correct_alias_typos(text, COTW_MOVE_ALIASES)
    if corrected_text != text:
        corrected_compact = normalize_move_token(corrected_text)
        if corrected_text in COTW_MOVE_ALIASES:
            return COTW_MOVE_ALIASES[corrected_text]
        if corrected_compact in COTW_MOVE_ALIASES:
            return COTW_MOVE_ALIASES[corrected_compact]
        return corrected_text
    return text


def crouch_digit_keys(row):
    """Some DreamCancel pages write crouching normals as cr.C; queries ("crouch c", "2c") mean 2C."""
    num_cmd = str(row.get("numCmd") or "").strip()
    return {normalize_move_token(f"2{num_cmd[3:]}")} if num_cmd.lower().startswith("cr.") else set()


def row_match_keys(row):
    keys = set()
    for value in (row.get("numCmd"), row.get("moveName")):
        if str(value or "").strip():
            keys.add(normalize_move_token(value))
    keys |= crouch_digit_keys(row)
    num_cmd = str(row.get("numCmd") or "").strip()
    motion_match = re.match(r"^([1-9][0-9]*)([A-Za-z]+(?:/[A-Za-z]+)+)$", num_cmd)
    if motion_match:
        motion, buttons = motion_match.groups()
        for button in buttons.split("/"):
            keys.add(normalize_move_token(f"{motion}{button}"))
    return {key for key in keys if key}


def _cotw_notation_query(query_key):
    return looks_like_notation_query(query_key, "digit_button")


def _cotw_filter_version_matches(matches, query_key):
    button_match = re.search(r"([a-d]+)$", query_key)
    requested_button = button_match.group(1) if button_match else ""
    if not requested_button:
        return matches
    version_filtered = []
    for row in matches:
        version_key = normalize_move_token(row.get("version", ""))
        if version_key and len(version_key) % 2 == 0 and version_key[: len(version_key) // 2] == version_key[len(version_key) // 2 :]:
            version_key = version_key[: len(version_key) // 2]
        if version_key and requested_button == version_key:
            version_filtered.append(row)
    return version_filtered or matches


def find_matching_rows(char_key, move_text):
    query = normalize_move_query(move_text)
    query_key = normalize_move_token(query)
    rows = COTW_FRAME_DATA.get(char_key, []) or []
    version_filter = lambda matches: _cotw_filter_version_matches(matches, query_key)
    return find_matching_rows_standard(
        rows,
        query,
        query_key,
        normalize_fn=normalize_move_token,
        looks_like_fn=_cotw_notation_query,
        row_keys_fn=row_match_keys,
        extra_row_keys_fn=crouch_digit_keys,
        dedupe_fn=unique_rows,
        post_notation=version_filter,
        post_exact=version_filter,
    )


def build_disambiguation_prompt(char_key, rows):
    lines = [f"Multiple COTW moves match {display_char_name(char_key)}. Reply with the option number:"]
    for index, row in enumerate(rows, start=1):
        move_name = str(row.get("moveName") or "Unknown").strip()
        num_cmd = str(row.get("numCmd") or "?").strip()
        lines.append(f"{index}. {move_name}: `{num_cmd}`")
    return "\n".join(lines)


# Natural-language query entry


def find_moves_in_text(text):
    lowered = normalize_query_terms(text)
    image_query = bool(re.search(r"\b(?:gif|gifs|hitbox|hitboxes|image|images|picture|pictures)\b", lowered))
    frame_query = bool(re.search(r"\b(?:framedata|frame\s*data|frames?|data)\b", lowered))
    game_query = bool(re.search(r"\b(?:cotw|city\s+of\s+the\s+wolves|fatal\s+fury)\b", lowered))
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
        "explicit_move_attempt": bool(char_matches and (frame_query or image_query or game_query or query_has_cotw_notation(lowered))),
        "missing_scrolls_query": bool(char_matches and not rows and (frame_query or image_query or game_query)),
        }


# Discord embed output


def get_notes_text(row):
    char_key = str(row.get("char_key", "")).strip().lower()
    num_cmd_key = normalize_move_token(row.get("numCmd", ""))
    cached_notes = (COTW_MOVE_NOTES.get(char_key, {}) or {}).get(num_cmd_key)
    return str(cached_notes or row.get("extraInfo") or "").strip()


def get_move_image_url(row):
    cached = COTW_MOVE_IMAGE_URLS.get((str(row.get("char_key", "")).strip().lower(), normalize_move_token(row.get("numCmd", ""))))
    if cached:
        return cached
    return str(row.get("imageUrl") or "").strip()


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
        title=truncate_value(f"COTW - {char_name}", 256),
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
    add_embed_field(embed, "Cancel", row.get("cancel"), inline=True)
    add_embed_field(embed, "Invuln", row.get("invuln"), inline=True)
    add_embed_field(embed, "REV Damage", row.get("revDamage"), inline=True)
    add_embed_field(embed, "Guard Damage", row.get("guardDamage"), inline=True)
    if show_notes:
        add_long_embed_field(embed, "Notes", get_notes_text(row), inline=False)
    image_url = get_move_image_url(row)
    if image_url:
        embed.set_image(url=image_url)
    source_url = clean_value(row.get("source_page_url"))
    if source_url:
        embed.url = source_url
    return embed


def image_attachment_filename(row):
    char_key = normalize_move_token(row.get("char_key", "cotw")) or "cotw"
    move_key = normalize_move_token(row.get("numCmd", "move")) or "move"
    image_url = get_move_image_url(row)
    parsed_path = urlparse(image_url).path
    extension = os.path.splitext(parsed_path)[1].lower()
    if extension not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        extension = ".png"
    return f"cotw_{char_key}_{move_key}{extension}"


async def build_image_attachment(row):
    image_url = get_move_image_url(row)
    if not image_url:
        return None, ""
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": "bubbot-cotw-images/1.0"}) as session:
            async with session.get(image_url, timeout=20) as response:
                if response.status != 200:
                    return None, image_url
                content_type = str(response.headers.get("Content-Type") or "").lower()
                if "image" not in content_type:
                    return None, image_url
                data = await response.read()
    except Exception as exc:
        print(f"[cotw-images] failed to fetch image attachment: {exc}", flush=True)
        return None, image_url
    filename = image_attachment_filename(row)
    return discord.File(io.BytesIO(data), filename=filename), f"attachment://{filename}"


class COTWNotesButton(discord.ui.Button):
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
        files = self.view.active_files() if hasattr(self.view, "active_files") else []
        kwargs = {"embed": self.view.build_embed(), "view": self.view}
        if files:
            kwargs["attachments"] = files
        await interaction.response.edit_message(**kwargs)


async def send_frame_response(message, rows):
    from bubbot.features.menu_system import send_frame_result_messages

    return await send_frame_result_messages(
        message.channel,
        "cotw",
        rows,
        owner_id=getattr(message.author, "id", None),
        menu_locked=False,
        source_message=message,
        prompt=str(getattr(message, "content", "") or ""),
    )


async def send_image_response(message, rows):
    if not rows:
        return []
    links = []
    for row in rows:
        image_url = get_move_image_url(row)
        if image_url:
            links.append(image_url)
    if not links:
        sent = await message.reply("DreamCancel does not have a COTW move image link for this move yet.")
        return [sent.id]
    sent = await message.reply("DreamCancel does not provide COTW hitbox images, so here is the regular move image:\n" + "\n".join(links))
    return [sent.id]


async def send_hitbox_response(message, rows):
    return await send_image_response(message, rows)


def format_frame_data(row, include_notes=False):
    text = (
        f"Move: {row.get('moveName') or row.get('numCmd')} ({row.get('numCmd') or '?'})\n"
        f"Startup: {row.get('startup') or '-'} | Active: {row.get('active') or '-'} | Recovery: {row.get('recovery') or '-'}\n"
        f"On Hit: {row.get('onHit') or '-'} | On Block: {row.get('onBlock') or '-'}\n"
        f"Damage: {row.get('dmg') or '-'} | Guard: {row.get('guardLevel') or '-'} | Cancel: {row.get('cancel') or '-'}\n"
        f"Invuln: {row.get('invuln') or '-'} | REV Damage: {row.get('revDamage') or '-'} | Guard Damage: {row.get('guardDamage') or '-'}"
    )
    notes = get_notes_text(row)
    if include_notes and notes:
        text += f"\nNotes: {notes}"
    return text


load_move_image_urls()
