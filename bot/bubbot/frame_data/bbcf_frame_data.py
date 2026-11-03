"""BBCF frame parser, embeds, hitbox/image/notes helpers."""

import os
import re

import discord
import pandas as pd

from bubbot.data.bbcf_aliases import BBCF_CHARACTER_ALIASES, BBCF_LOOKUP_WORDS, BBCF_MOVE_ALIASES
from bubbot.utils.character_lookup import find_alias_positions_in_text, resolve_alias_key
from bubbot.utils.comparison_utils import find_comparison_rows, is_comparison_query
from bubbot.utils.image_cache_utils import import_cache_module, merge_nested_url_cache
from bubbot.utils.mediawiki_images import resize_mediawiki_thumb_url as shared_resize_mediawiki_thumb_url
from bubbot.utils.frame_match_utils import find_matching_rows_standard, prefer_grounded_rows
from bubbot.utils.notation_match_utils import looks_like_notation_query, query_has_jump_motion_notation
from bubbot.utils.row_utils import unique_rows
from bubbot.utils.text_utils import compact_key, correct_alias_typos, normalize_query_terms, query_suffix_candidates, strip_noise_words, strip_query_terms
from bubbot.utils.sfstats_style import ACTION, BRAND, AllHitboxImagesButton, toggle_style
from bubbot.utils.emoji_text import strip_embed_emojis


BBCF_FRAME_DATA_FILE = "BBCF Frame Data.ods"
BBCF_FRAME_DATA = {}
BBCF_MOVE_IMAGE_URLS = {}
BBCF_HITBOX_DATA = {}
BBCF_MOVE_NOTES = {}
BBCF_MOVE_IMAGES_MODULE = "bubbot.data.bbcf_move_images"
BBCF_IMAGE_THUMB_WIDTH = 200


def normalize_key(value):
    return compact_key(value).replace("the", "")


def normalize_move_token(value):
    text = str(value or "").lower().strip()
    text = text.replace("jumping", "j")
    text = re.sub(r"\b(?:jump|air)\s*\.?,?", "j.", text)
    text = text.replace("[", "hold")
    return re.sub(r"[^a-z0-9]+", "", text)


def resize_bbcf_image_url(url):
    return shared_resize_mediawiki_thumb_url(url, BBCF_IMAGE_THUMB_WIDTH)


def query_has_bbcf_notation(text):
    lowered = str(text or "").lower()
    return bool(
        re.search(r"(?:^|\s)j\s*\.\s*(?:[236]?[abcd]|[0-9]{2,3}[abcd])\b", lowered)
        or re.search(r"(?:^|\s)(?:[1-9][a-d]|[0-9]{2,6}[a-d]|[1-9]?\[?[a-d](?:\+[a-d])+\]?)(?:\s|$)", lowered)
        or re.search(r"(?:^|\s)[0-9x]+(?:~[0-9x]+)*[a-d](?:~[0-9x]+[a-d])*\b", lowered)
        or query_has_jump_motion_notation(lowered)
    )


def load_move_image_urls(module_name=BBCF_MOVE_IMAGES_MODULE):
    image_module = import_cache_module(module_name, "bbcf-images")
    if not image_module:
        return False
    normal_loaded = merge_nested_url_cache(
        BBCF_MOVE_IMAGE_URLS,
        getattr(image_module, "BBCF_MOVE_IMAGE_URLS", {}),
        char_key_fn=lambda value: str(value or "").strip().lower(),
        move_key_fn=normalize_move_token,
    )
    BBCF_HITBOX_DATA.clear()
    hitbox_loaded = 0
    for char_key, moves in (getattr(image_module, "BBCF_HITBOX_DATA", {}) or {}).items():
        if not isinstance(moves, dict):
            continue
        normalized_char = str(char_key or "").strip().lower()
        char_links = BBCF_HITBOX_DATA.setdefault(normalized_char, {})
        for move_key, links in moves.items():
            if isinstance(links, str):
                links = [links]
            clean_links = [str(link or "").strip() for link in (links or []) if str(link or "").strip()]
            if clean_links:
                char_links[normalize_move_token(move_key)] = clean_links
                hitbox_loaded += len(clean_links)
    BBCF_MOVE_NOTES.clear()
    notes_loaded = 0
    for char_key, moves in (getattr(image_module, "BBCF_MOVE_NOTES", {}) or {}).items():
        if not isinstance(moves, dict):
            continue
        normalized_char = str(char_key or "").strip().lower()
        char_notes = BBCF_MOVE_NOTES.setdefault(normalized_char, {})
        for move_key, notes in moves.items():
            clean_notes = str(notes or "").strip()
            if clean_notes:
                char_notes[normalize_move_token(move_key)] = clean_notes
                notes_loaded += 1
    print(f"[bbcf-images] loaded {normal_loaded} move image links, {hitbox_loaded} hitbox links, and {notes_loaded} notes", flush=True)
    return True


def resolve_character_key(text):
    return resolve_alias_key(text, BBCF_CHARACTER_ALIASES, BBCF_FRAME_DATA.keys(), normalize_fn=normalize_key)


def display_char_name(char_key):
    rows = BBCF_FRAME_DATA.get(char_key) or []
    if rows:
        return str(rows[0].get("char_name") or char_key).strip()
    return str(char_key or "Unknown").replace("_", " ").title()


# ODS load


def load_frame_data(filename=None):
    global BBCF_FRAME_DATA
    BBCF_FRAME_DATA = {}
    filename = filename or BBCF_FRAME_DATA_FILE
    if not os.path.exists(filename):
        print(f"[bbcf] frame data file not found: {filename}", flush=True)
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
            BBCF_FRAME_DATA[rows[0]["char_key"]] = rows
            BBCF_CHARACTER_ALIASES.setdefault(rows[0]["char_key"], rows[0]["char_key"])
            BBCF_CHARACTER_ALIASES.setdefault(rows[0]["char_key"].replace("_", " "), rows[0]["char_key"])
            BBCF_CHARACTER_ALIASES.setdefault(str(rows[0]["char_name"]).lower(), rows[0]["char_key"])
            loaded += 1
    print(f"[bbcf] Total characters loaded: {loaded}", flush=True)
    return bool(BBCF_FRAME_DATA)


def find_characters_in_text(text):
    return find_alias_positions_in_text(text, BBCF_CHARACTER_ALIASES, BBCF_FRAME_DATA.keys())


def normalize_move_query(query):
    text = str(query or "").lower().strip()
    text = strip_query_terms(text)
    text = re.sub(r"\b(?:bbcf|blazblue|central\s*fiction)\b", " ", text)
    text = re.sub(r"\b(?:framedata|frame\s*data|frames?|data|gif|gifs|hitbox(?:es)?|images?|notes?|start\s*up|startup|active|recovery|total|on\s+hit|on\s+block|flawless\s+block|block\s+damage|rev\s+damage|guard\s+damage|damage|dmg|guard|attack\s+level|atk\s*lvl|atk\s*level|cancel(?:l?able)?|gatling|invuln(?:erability)?|invul|attribute|range|length|hit\s*-?\s*confirm|hitconfirm|confirm\s+window|confirm\s+timing|confirmable|super\s*gain|super\s*meter\s*gain|meter\s*gain|super\s*build|sa\s*gain|drive\s+gain|drive\s+chip|drive\s+dmg|drive\s+damage|hitstun|blockstun|stun|risc\s*gain|risc|proration|prorate|knockdown\s+adv(?:antage)?|kda|counter\s*hit\s+adv(?:antage)?|ch\s*adv)\b", " ", text)
    text = _normalize_bbcf_notation_spacing(text)
    if not query_has_bbcf_notation(text):
        text = strip_noise_words(text)
    compact = normalize_move_token(text)
    if text in BBCF_MOVE_ALIASES:
        return BBCF_MOVE_ALIASES[text]
    if compact in BBCF_MOVE_ALIASES:
        return BBCF_MOVE_ALIASES[compact]
    corrected_text = correct_alias_typos(text, BBCF_MOVE_ALIASES)
    if corrected_text != text:
        corrected_compact = normalize_move_token(corrected_text)
        if corrected_text in BBCF_MOVE_ALIASES:
            return BBCF_MOVE_ALIASES[corrected_text]
        if corrected_compact in BBCF_MOVE_ALIASES:
            return BBCF_MOVE_ALIASES[corrected_compact]
        return corrected_text
    return text


def _normalize_bbcf_notation_spacing(text):
    text = str(text or "").lower()

    def _join_mai_x_notation(match):
        prefix = match.group(1).replace(" ", "")
        first = match.group(2).replace(" ", "")
        second = (match.group(3) or "").replace(" ", "")
        return f"{prefix}{first}{second}"

    text = re.sub(
        r"\b([125789]\s*x)\s*(\[?\s*[abcd]\s*\]?)(?:\s+([1-9]?\s*[abcd]))?\b",
        _join_mai_x_notation,
        text,
    )
    text = re.sub(r"\bj\s+([abcd])\b", r"j.\1", text)
    text = re.sub(r"\b([1-9])\s+([abcd])\b", r"\1\2", text)
    return text


def _state_stripped_move_key(value):
    text = re.sub(r"\[\s*w\s*\]", "", str(value or ""), flags=re.IGNORECASE)
    return normalize_move_token(text)


def row_match_keys(row):
    keys = set()
    for value in (row.get("numCmd"), row.get("moveName")):
        if str(value or "").strip():
            keys.add(normalize_move_token(value))
    return {key for key in keys if key}


def _bbcf_notation_query(query_key):
    return looks_like_notation_query(query_key, "digit_button")


def _find_matching_rows_generic(char_key, move_text):
    query = normalize_move_query(move_text)
    query_key = normalize_move_token(query)
    rows = BBCF_FRAME_DATA.get(char_key, []) or []
    if _bbcf_notation_query(query_key):
        exact_command_rows = unique_rows(
            row
            for row in rows
            if normalize_move_token(row.get("numCmd", "")) == query_key
        )
        if exact_command_rows:
            return prefer_grounded_rows(exact_command_rows, move_text)
    return find_matching_rows_standard(
        rows,
        query,
        query_key,
        normalize_fn=normalize_move_token,
        looks_like_fn=_bbcf_notation_query,
        row_keys_fn=row_match_keys,
        dedupe_fn=unique_rows,
    )


def _find_mai_followup_rows(char_key, move_text):
    if char_key != "mai_natsume" or not re.search(r"\bfollow\s*ups?\b", str(move_text or "").lower()):
        return []
    query_key = normalize_move_token(normalize_move_query(move_text))
    if not query_key.startswith("5xa"):
        return []
    rows = BBCF_FRAME_DATA.get(char_key, []) or []
    return unique_rows(
        row
        for row in rows
        if normalize_move_token(row.get("numCmd", "")).startswith("5xa")
        and normalize_move_token(row.get("numCmd", "")) != "5xa"
    )


def _find_terumi_gain_art_rows(char_key, move_text):
    if char_key != "yuuki_terumi" or not re.search(r"\bgain\s+arts?\b", str(move_text or "").lower()):
        return []
    stripped = re.sub(r"\bgain\s+arts?\b", " ", str(move_text or ""), flags=re.IGNORECASE)
    stripped = re.sub(r"\b(?:moves?|in)\b", " ", stripped, flags=re.IGNORECASE).strip()
    if stripped:
        matches = _find_matching_rows_generic(char_key, stripped)
        if matches:
            return matches
    gain_art_commands = {
        "5d", "2d", "2dod", "6d", "6dfollowup", "6dfollowupod", "4d", "jd", "j2d",
        "236d", "236dfollowup", "214d", "632146d", "236236d", "214214d", "222d",
    }
    return unique_rows(
        row
        for row in (BBCF_FRAME_DATA.get(char_key, []) or [])
        if normalize_move_token(row.get("numCmd", "")) in gain_art_commands
    )


def _find_valkenhayn_wolf_rows(char_key, move_text):
    if char_key != "valkenhayn_r_hellsing" or "wolf" not in str(move_text or "").lower():
        return []
    stripped = re.sub(r"\bwolf\b", " ", str(move_text or ""), flags=re.IGNORECASE).strip()
    query_key = normalize_move_token(normalize_move_query(stripped))
    if not query_key:
        return []
    rows = BBCF_FRAME_DATA.get(char_key, []) or []
    wolf_rows = [
        row
        for row in rows
        if "[w]" in str(row.get("numCmd", "")).lower() or "wolf" in str(row.get("moveName", "")).lower()
    ]
    exact = [
        row
        for row in wolf_rows
        if query_key in row_match_keys(row) or _state_stripped_move_key(row.get("numCmd", "")) == query_key
    ]
    if exact:
        return unique_rows(exact)
    generic = _find_matching_rows_generic(char_key, stripped)
    filtered = [
        row
        for row in generic
        if "[w]" in str(row.get("numCmd", "")).lower() or "wolf" in str(row.get("moveName", "")).lower()
    ]
    return unique_rows(filtered)


def _is_multi_row_intent(char_key, move_text):
    text = str(move_text or "").lower()
    return bool(
        (char_key == "mai_natsume" and re.search(r"\bfollow\s*ups?\b", text))
        or (char_key == "yuuki_terumi" and re.search(r"\bgain\s+arts?\b", text))
    )


def find_matching_rows(char_key, move_text):
    for finder in (_find_mai_followup_rows, _find_terumi_gain_art_rows, _find_valkenhayn_wolf_rows):
        matches = finder(char_key, move_text)
        if matches:
            return matches
    return _find_matching_rows_generic(char_key, move_text)


def build_disambiguation_prompt(char_key, rows):
    lines = [f"Multiple BBCF moves match {display_char_name(char_key)}. Reply with the option number:"]
    for index, row in enumerate(rows, start=1):
        move_name = str(row.get("moveName") or "Unknown").strip()
        num_cmd = str(row.get("numCmd") or "?").strip()
        lines.append(f"{index}. {move_name}: `{num_cmd}`")
    return "\n".join(lines)


# Natural-language query entry


def find_moves_in_text(text):
    lowered = normalize_query_terms(text)
    hitbox_query = bool(re.search(r"\b(?:gif|gifs|hitbox|hitboxes|image|images|picture|pictures)\b", lowered))
    frame_query = bool(re.search(r"\b(?:framedata|frame\s*data|frames?|data)\b", lowered))
    game_query = bool(re.search(r"\b(?:bbcf|blazblue|central\s*fiction)\b", lowered))
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
            if _is_multi_row_intent(char_key, move_candidate):
                rows.extend(matches)
                quiz_answer_too_broad = True
                break
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
        "wants_comparison": is_comparison_query(lowered, char_matches),
        "explicit_move_attempt": bool(char_matches and (frame_query or hitbox_query or game_query or query_has_bbcf_notation(lowered))),
        "missing_scrolls_query": bool(char_matches and not rows and (frame_query or hitbox_query or game_query)),
        "quiz_answer_too_broad": quiz_answer_too_broad,
        }


# Discord embed output


def get_notes_text(row):
    char_key = str(row.get("char_key", "")).strip().lower()
    num_cmd_key = normalize_move_token(row.get("numCmd", ""))
    cached_notes = (BBCF_MOVE_NOTES.get(char_key, {}) or {}).get(num_cmd_key)
    return str(cached_notes or row.get("extraInfo") or "").strip()


def get_move_image_url(row):
    return resize_bbcf_image_url(
        BBCF_MOVE_IMAGE_URLS.get((str(row.get("char_key", "")).strip().lower(), normalize_move_token(row.get("numCmd", ""))))
    )


def get_hitbox_links(row, limit=4):
    char_key = str(row.get("char_key", "")).strip().lower()
    num_cmd_key = normalize_move_token(row.get("numCmd", ""))
    links = (BBCF_HITBOX_DATA.get(char_key, {}) or {}).get(num_cmd_key, [])
    clean_links = [resize_bbcf_image_url(link) for link in list(links or []) if str(link or "").strip()]
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
        title=truncate_value(f"BBCF - {char_name}", 256),
        description=truncate_value(f"{move_name} ({num_cmd})", 4096),
        colour=BRAND,
    )
    add_embed_field(embed, "Startup", row.get("startup"), inline=True)
    add_embed_field(embed, "Active", row.get("active"), inline=True)
    add_embed_field(embed, "Recovery", row.get("recovery"), inline=True)
    add_embed_field(embed, "On Block", row.get("onBlock"), inline=True)
    add_embed_field(embed, "On ODR", row.get("onODR"), inline=True)
    add_embed_field(embed, "Damage", row.get("dmg"), inline=True)
    add_embed_field(embed, "Guard", row.get("guardLevel"), inline=True)
    add_embed_field(embed, "Attribute", row.get("attribute"), inline=True)
    add_embed_field(embed, "Cancel", row.get("cancel"), inline=True)
    add_embed_field(embed, "Invuln", row.get("invuln"), inline=True)
    add_embed_field(embed, "Level", row.get("level"), inline=True)
    if show_notes:
        add_long_embed_field(embed, "Notes", get_notes_text(row), inline=False)
    image_url = get_move_image_url(row)
    if image_url:
        embed.set_image(url=image_url)
    return strip_embed_emojis(embed)


class BBCFAllHitboxImagesButton(AllHitboxImagesButton):
    def __init__(self, row):
        super().__init__(get_media_links(row, limit=None))


class BBCFNotesButton(discord.ui.Button):
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
        "bbcf",
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
        sent = await message.reply("I have BBCF frame data for this move but no image link yet.")
        return [sent.id]
    sent = await message.reply("\n".join(links))
    return [sent.id]


def format_frame_data(row, include_notes=False):
    text = (
        f"Move: {row.get('moveName') or row.get('numCmd')} ({row.get('numCmd') or '?'})\n"
        f"Startup: {row.get('startup') or '-'} | Active: {row.get('active') or '-'} | Recovery: {row.get('recovery') or '-'}\n"
        f"On Block: {row.get('onBlock') or '-'} | On ODR: {row.get('onODR') or '-'}\n"
        f"Damage: {row.get('dmg') or '-'} | Guard: {row.get('guardLevel') or '-'} | Attribute: {row.get('attribute') or '-'}\n"
        f"Cancel: {row.get('cancel') or '-'} | Invuln: {row.get('invuln') or '-'}"
    )
    notes = get_notes_text(row)
    if include_notes and notes:
        text += f"\nNotes: {notes}"
    return text


load_move_image_urls()
