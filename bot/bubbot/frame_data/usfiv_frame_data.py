"""Ultra Street Fighter IV frame-data loading, lookup, embeds, and media helpers."""

from __future__ import annotations

import os
import re

import discord
import pandas as pd
from bubbot.utils.frame_match_utils import prefer_grounded_rows

from bubbot.data.usfiv_aliases import USFIV_CHARACTER_ALIASES, USFIV_LOOKUP_WORDS, USFIV_MOVE_ALIASES
from bubbot.utils.character_lookup import find_alias_positions_in_text, resolve_alias_key
from bubbot.utils.comparison_utils import find_comparison_rows, is_comparison_query
from bubbot.utils.frame_match_utils import filter_rows_by_strength, match_rows_by_fuzzy_keys, normalized_query_words, parse_strength_qualifier, row_strengths
from bubbot.utils.image_cache_utils import import_cache_module, merge_nested_url_cache
from bubbot.utils.notation_match_utils import find_rows_by_notation_prefix, looks_like_notation_query, query_has_jump_motion_notation
from bubbot.utils.row_utils import unique_rows
from bubbot.utils.text_utils import compact_key, correct_alias_typos, normalize_query_terms, query_suffix_candidates, strip_noise_words, strip_query_terms
from bubbot.utils.sfstats_style import ACTION, BRAND, toggle_style
from bubbot.utils.emoji_text import strip_embed_emojis


USFIV_FRAME_DATA_FILE = "USFIV Frame Data.ods"
USFIV_FRAME_DATA = {}
USFIV_MOVE_IMAGE_URLS = {}
USFIV_HITBOX_DATA = {}
USFIV_MOVE_NOTES = {}
USFIV_MOVE_IMAGES_MODULE = "bubbot.data.usfiv_move_images"


def normalize_key(value):
    return compact_key(value).replace("ultrastreetfighteriv", "").replace("usfiv", "").replace("usf4", "")


def normalize_move_token(value):
    text = str(value or "").lower().strip()
    text = text.replace("jumping", "j")
    text = re.sub(r"\b(?:jump|air)\s*\.?,?", "j.", text)
    text = re.sub(r"\bclose\b", "cl", text)
    text = re.sub(r"\bfar\b", "far", text)
    text = re.sub(r"\bcrouch(?:ing)?\b", "2", text)
    text = re.sub(r"\bst(?:anding)?\s*", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def _cache_move_key(row):
    return normalize_move_token(f"{row.get('numCmd', '')} {row.get('version', '')}")


def query_has_usf4_game_tag(text):
    return bool(re.search(r"\b(?:usf4|usfiv|sf4|ultra\s*street\s*fighter\s*(?:4|iv)|street\s*fighter\s*(?:4|iv))\b", str(text or ""), re.IGNORECASE))


def query_has_usf4_notation(text):
    lowered = str(text or "").lower()
    return bool(
        re.search(r"(?:^|\s)(?:cl|close|far|f|cr|crouch|j|jump)\s*\.?\s*[lmh][pk]\b", lowered)
        or re.search(r"(?:^|\s)(?:[1-9][0-9]{0,5}[lmh]?[pk]|[1-9]?[lmh]?[pk](?:\+[lmh]?[pk])+)(?:\s|$)", lowered)
        or re.search(r"\b(?:qcf|qcb|dp|rdp|hcf|hcb|360|720)\b", lowered)
        or query_has_jump_motion_notation(lowered)
    )


def load_move_image_urls(module_name=USFIV_MOVE_IMAGES_MODULE):
    image_module = import_cache_module(module_name, "usf4-images")
    if not image_module:
        return False
    loaded_images = merge_nested_url_cache(
        USFIV_MOVE_IMAGE_URLS,
        getattr(image_module, "USFIV_MOVE_IMAGE_URLS", {}),
        char_key_fn=lambda value: str(value or "").strip().lower(),
        move_key_fn=normalize_move_token,
    )
    USFIV_HITBOX_DATA.clear()
    loaded_hitboxes = 0
    for char_key, moves in (getattr(image_module, "USFIV_HITBOX_DATA", {}) or {}).items():
        if not isinstance(moves, dict):
            continue
        links_for_char = USFIV_HITBOX_DATA.setdefault(str(char_key or "").strip().lower(), {})
        for move_key, links in moves.items():
            clean_links = [str(link or "").strip() for link in (links if isinstance(links, list) else [links]) if str(link or "").strip()]
            if clean_links:
                links_for_char[normalize_move_token(move_key)] = clean_links
                loaded_hitboxes += len(clean_links)
    USFIV_MOVE_NOTES.clear()
    loaded_notes = 0
    for char_key, moves in (getattr(image_module, "USFIV_MOVE_NOTES", {}) or {}).items():
        if not isinstance(moves, dict):
            continue
        notes_for_char = USFIV_MOVE_NOTES.setdefault(str(char_key or "").strip().lower(), {})
        for move_key, notes in moves.items():
            clean_notes = str(notes or "").strip()
            if clean_notes:
                notes_for_char[normalize_move_token(move_key)] = clean_notes
                loaded_notes += 1
    print(f"[usf4-images] loaded {loaded_images} move image links, {loaded_hitboxes} hitbox links, and {loaded_notes} notes", flush=True)
    return True


def load_frame_data(filename=None):
    global USFIV_FRAME_DATA
    USFIV_FRAME_DATA = {}
    filename = filename or USFIV_FRAME_DATA_FILE
    if not os.path.exists(filename):
        print(f"[usf4] frame data file not found: {filename}", flush=True)
        return False
    xls = pd.ExcelFile(filename, engine="odf")
    loaded = 0
    for sheet_name in xls.sheet_names:
        if not sheet_name.endswith("Normal"):
            continue
        frame = pd.read_excel(xls, sheet_name=sheet_name).fillna("")
        rows = []
        for row in frame.to_dict("records"):
            char_key = str(row.get("char_key") or row.get("char_name") or sheet_name[: -len("Normal")]).strip().lower()
            move_name = str(row.get("moveName", "")).strip()
            num_cmd = str(row.get("numCmd", "")).strip()
            if not move_name and not num_cmd:
                continue
            row["char_key"] = char_key
            row["char_name"] = str(row.get("char_name") or sheet_name[: -len("Normal")]).strip()
            rows.append(row)
        if rows:
            key = rows[0]["char_key"]
            USFIV_FRAME_DATA[key] = rows
            USFIV_CHARACTER_ALIASES.setdefault(key.replace("_", " "), key)
            USFIV_CHARACTER_ALIASES.setdefault(str(rows[0]["char_name"]).lower(), key)
            loaded += 1
    print(f"[usf4] Total characters loaded: {loaded}", flush=True)
    return bool(USFIV_FRAME_DATA)


def resolve_character_key(text):
    return resolve_alias_key(text, USFIV_CHARACTER_ALIASES, USFIV_FRAME_DATA.keys(), normalize_fn=normalize_key)


def display_char_name(char_key):
    rows = USFIV_FRAME_DATA.get(char_key) or []
    if rows:
        return str(rows[0].get("char_name") or char_key).strip()
    return str(char_key or "Unknown").replace("_", " ").title()


def find_characters_in_text(text):
    return find_alias_positions_in_text(text, USFIV_CHARACTER_ALIASES, USFIV_FRAME_DATA.keys())


def normalize_move_query(query, char_key=None):
    text = str(query or "").lower().strip()
    text = strip_query_terms(text)
    text = re.sub(r"<@!?\d+>", " ", text)
    text = re.sub(r"\b(?:usf4|usfiv|sf4|ultra\s*street\s*fighter\s*(?:4|iv)|street\s*fighter\s*(?:4|iv))\b", " ", text)
    text = re.sub(r"\b(?:framedata|frame\s*data|frames?|data|gif|gifs|hitbox(?:es)?|images?|pictures?|notes?|start\s*up|startup|active|recovery|total|on\s+hit|on\s+block|damage|dmg|guard|cancel(?:l?able)?|meter\s*gain|meter|stun|blockstun|hitstun|invuln(?:erability)?|armor|airborne|juggle)\b", " ", text)
    text = strip_noise_words(text)
    if text in USFIV_MOVE_ALIASES:
        return USFIV_MOVE_ALIASES[text]
    query_key = normalize_move_token(text)
    for alias, target in USFIV_MOVE_ALIASES.items():
        if query_key == normalize_move_token(alias):
            return target
    corrected = correct_alias_typos(text, {}, USFIV_MOVE_ALIASES)
    return USFIV_MOVE_ALIASES.get(corrected, corrected)


def row_match_keys(row):
    keys = set()
    values = (
        row.get("moveName"), row.get("nickname"), row.get("numCmd"), row.get("version"),
        f"{row.get('moveName', '')} {row.get('version', '')}",
        f"{row.get('nickname', '')} {row.get('version', '')}",
        f"{row.get('numCmd', '')} {row.get('version', '')}",
    )
    for value in values:
        key = normalize_move_token(value)
        if key:
            keys.add(key)
    return keys


def row_is_ex_variant(row):
    strengths = row_strengths(row)
    if strengths:
        return "EX" in strengths
    return any(
        normalize_move_token(row.get(field, "")).startswith("ex")
        for field in ("moveName", "version")
    )


def unique_usfiv_rows(rows):
    return unique_rows(
        rows,
        key_fn=lambda row: (
            str(row.get("char_key") or row.get("char_name") or "").strip().lower(),
            normalize_move_token(row.get("moveName", "")),
            normalize_move_token(row.get("numCmd", "")),
            normalize_move_token(row.get("version", "")),
        ),
    )


def prefer_ex_variant(rows, query):
    unique = unique_usfiv_rows(rows)
    if len(unique) <= 1:
        return unique
    ex_rows = [row for row in unique if row_is_ex_variant(row)]
    if re.search(r"\b(?:ex|od)\b", str(query or ""), re.IGNORECASE):
        return ex_rows or unique
    non_ex_rows = [row for row in unique if not row_is_ex_variant(row)]
    return non_ex_rows or unique


def _usfiv_notation_query(query_key):
    return looks_like_notation_query(query_key, "third_strike")


def _usfiv_normal_notation_keys(row):
    if str(row.get("moveType", "")).strip().lower() != "normal":
        return set()
    keys = set()
    for value in (row.get("numCmd"), row.get("moveName")):
        key = normalize_move_token(value)
        button_match = re.fullmatch(r"(?:cl)?([lmh][pk])", key)
        if button_match:
            button = button_match.group(1)
            keys.update((button, f"5{button}"))
        elif re.fullmatch(r"[1-9][lmh][pk]", key):
            keys.add(key)
    return keys


def _find_matching_rows_unfiltered(char_key, query):
    query_key = normalize_move_token(query)
    if not query_key:
        return []
    rows = USFIV_FRAME_DATA.get(char_key, []) or []
    notation_matches = find_rows_by_notation_prefix(
        rows,
        query_key,
        normalize_fn=normalize_move_token,
        looks_like_fn=_usfiv_notation_query,
        extra_row_keys_fn=_usfiv_normal_notation_keys,
    )
    if notation_matches:
        return notation_matches
    exact = [row for row in rows if query_key in row_match_keys(row)]
    if exact:
        prefixed_variants = [
            row
            for row in rows
            if normalize_move_token(row.get("moveName", "")) == f"ex{query_key}"
        ]
        return [*exact, *prefixed_variants]
    if _usfiv_notation_query(query_key):
        return []
    partial = [
        row
        for row in rows
        if any(key and (query_key in key or (len(query_key) >= 3 and key in query_key)) for key in row_match_keys(row))
    ]
    if partial:
        return partial
    query_words = normalized_query_words(query)
    name_matches = [
        row for row in rows
        if query_words and query_words in " ".join(str(row.get(field, "")).lower() for field in ("moveName", "nickname", "numCmd", "version"))
    ]
    if name_matches:
        return name_matches
    fuzzy = match_rows_by_fuzzy_keys(rows, query_key, value_fields=("moveName", "nickname", "numCmd", "version"), normalize_fn=normalize_move_token, cutoff=0.84, n=4)
    return fuzzy


def find_matching_rows(char_key, move_text):
    query = normalize_move_query(move_text, char_key=char_key)
    base_query, strengths, embedded_notation = parse_strength_qualifier(query)
    candidates = [query, base_query] if embedded_notation else [base_query]
    for candidate in candidates:
        matches = _find_matching_rows_unfiltered(char_key, candidate)
        matches = filter_rows_by_strength(matches, strengths)
        matches = prefer_grounded_rows(matches, f"{move_text} {query}")
        matches = prefer_ex_variant(matches, move_text)
        if matches:
            return matches
    return []


def build_disambiguation_prompt(char_key, rows):
    lines = [f"Multiple USF4 moves match {display_char_name(char_key)}. Reply with the option number:"]
    for index, row in enumerate(rows, start=1):
        move_name = str(row.get("moveName") or row.get("numCmd") or "Unknown").strip()
        num_cmd = str(row.get("numCmd") or "?").strip()
        version = str(row.get("version") or "").strip()
        lines.append(f"{index}. {move_name}: `{num_cmd}`{f' [{version}]' if version else ''}")
    return "\n".join(lines)


def find_moves_in_text(text):
    lowered = normalize_query_terms(text)
    image_query = bool(re.search(r"\b(?:gif|gifs|hitbox|hitboxes|image|images|picture|pictures)\b", lowered))
    frame_query = bool(re.search(r"\b(?:framedata|frame\s*data|frames?|data)\b", lowered))
    game_query = query_has_usf4_game_tag(lowered)
    notation_query = query_has_usf4_notation(lowered)
    notes_query = bool(re.search(r"\bnotes?\b", lowered))
    char_matches = find_characters_in_text(lowered)
    matched_char_key = char_matches[0][0] if char_matches else None
    comparison = find_comparison_rows(lowered, char_matches, find_characters_in_text=find_characters_in_text, find_rows_for_char=find_matching_rows)
    if comparison:
        rows = comparison.get("rows", [])
        if comparison.get("needs_disambiguation"):
            return _payload("options", rows, image_query, frame_query, game_query, notes_query, comparison.get("char_key"), True, True, notation_query=notation_query)
        return _payload("gif" if image_query else "frame", rows, image_query, frame_query, game_query, notes_query, comparison.get("char_key") or matched_char_key, False, True, notation_query=notation_query)
    for char_key, start, end, _alias in char_matches:
        move_text = (lowered[:start] + " " + lowered[end:]).strip()
        matches = []
        for candidate in query_suffix_candidates(move_text):
            matches = find_matching_rows(char_key, candidate)
            if matches:
                break
        if len(matches) > 1:
            return _payload("options", matches, image_query, frame_query, game_query, notes_query, char_key, True, False, notation_query=notation_query)
        if matches:
            return _payload("gif" if image_query else "frame", matches, image_query, frame_query, game_query, notes_query, char_key, False, False, notation_query=notation_query)
    return _payload("none", [], image_query, frame_query, game_query, notes_query, matched_char_key, False, is_comparison_query(lowered, char_matches), char_matches=char_matches, notation_query=notation_query)


def _payload(mode, rows, image_query, frame_query, game_query, notes_query, char_key, needs_disambiguation, wants_comparison, *, char_matches=None, notation_query=False):
    return {
        "mode": mode,
        "rows": rows,
        "data": build_disambiguation_prompt(char_key, rows) if needs_disambiguation else "\n\n".join(format_frame_data(row, include_notes=notes_query) for row in rows),
        "gif_query": image_query,
        "frame_query": frame_query,
        "game_query": game_query,
        "notes_query": notes_query,
        "needs_disambiguation": needs_disambiguation,
        "char_found": bool(char_matches) if char_matches is not None else bool(char_key),
        "char_key": char_key,
        "wants_comparison": wants_comparison,
        "explicit_move_attempt": bool(char_key and (image_query or frame_query or game_query or notation_query)),
        "missing_scrolls_query": bool(char_key and not rows and (image_query or frame_query or game_query or notation_query)),
    }


def get_notes_text(row):
    char_key = str(row.get("char_key", "")).strip().lower()
    cached = (USFIV_MOVE_NOTES.get(char_key, {}) or {}).get(_cache_move_key(row))
    return str(cached or row.get("extraInfo") or "").strip()


def get_move_image_url(row):
    return USFIV_MOVE_IMAGE_URLS.get((str(row.get("char_key", "")).strip().lower(), _cache_move_key(row)), "")


def get_hitbox_links(row, limit=4):
    links = (USFIV_HITBOX_DATA.get(str(row.get("char_key", "")).strip().lower(), {}) or {}).get(_cache_move_key(row), [])
    clean_links = [str(link or "").strip() for link in links if str(link or "").strip()]
    return clean_links[:limit] if limit is not None else clean_links


def get_media_links(row, limit=4):
    links = get_hitbox_links(row, limit=None)
    image_url = get_move_image_url(row)
    if image_url and image_url not in links:
        links.append(image_url)
    return links[:limit] if limit is not None else links


def _clean(value):
    return str(value or "").strip()


def _add_field(embed, name, value, *, inline=True):
    text = _clean(value)
    if text:
        embed.add_field(name=name, value=text[:1024], inline=inline)


def build_frame_embed(row, show_notes=False):
    char_name = _clean(row.get("char_name")) or "Unknown"
    move_name = _clean(row.get("moveName")) or _clean(row.get("numCmd")) or "Unknown"
    num_cmd = _clean(row.get("numCmd")) or "?"
    version = _clean(row.get("version"))
    embed = discord.Embed(
        title=f"Ultra Street Fighter IV - {char_name}"[:256],
        description=f"{move_name} ({num_cmd}){f' [{version}]' if version else ''}"[:4096],
        colour=BRAND,
    )
    _add_field(embed, "Startup", row.get("startup"))
    _add_field(embed, "Active", row.get("active"))
    _add_field(embed, "Recovery", row.get("recovery"))
    _add_field(embed, "Total", row.get("total"))
    _add_field(embed, "On Block", row.get("onBlock"))
    _add_field(embed, "On Hit", row.get("onHit"))
    _add_field(embed, "Damage", row.get("dmg"))
    _add_field(embed, "Stun", row.get("stun"))
    _add_field(embed, "Meter Gain", row.get("meterGain"))
    _add_field(embed, "Hit Level", row.get("guardLevel"))
    _add_field(embed, "Cancel", row.get("cancel"))
    invulnerability = "; ".join(
        f"{label}: {_clean(row.get(field))}"
        for label, field in (("Full", "fullInvuln"), ("Strike", "strikeInvuln"), ("Projectile", "projInvuln"), ("Throw", "throwInvuln"))
        if _clean(row.get(field))
    )
    _add_field(embed, "Invulnerability", invulnerability, inline=False)
    if show_notes:
        notes = get_notes_text(row)
        if notes:
            embed.add_field(name="Notes", value=notes[:1024], inline=False)
    image_url = get_move_image_url(row)
    if image_url:
        embed.set_image(url=image_url)
    return strip_embed_emojis(embed)


class USFIVNotesButton(discord.ui.Button):
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

    return await send_frame_result_messages(message.channel, "usf4", rows, owner_id=getattr(message.author, "id", None), menu_locked=False, source_message=message, prompt=str(getattr(message, "content", "") or ""))


async def send_hitbox_response(message, rows):
    links = [link for row in rows or [] for link in get_media_links(row)]
    if links:
        sent = await message.reply("\n".join(dict.fromkeys(links)))
    else:
        sent = await message.reply("I have USF4 frame data for this move but no dedicated image or hitbox link yet.")
    return [sent.id]


def format_frame_data(row, include_notes=False):
    text = (
        f"Move: {row.get('moveName') or row.get('numCmd')} ({row.get('numCmd') or '?'})\n"
        f"Startup: {row.get('startup') or '-'} | Active: {row.get('active') or '-'} | Recovery: {row.get('recovery') or '-'}\n"
        f"On Block: {row.get('onBlock') or '-'} | On Hit: {row.get('onHit') or '-'} | Damage: {row.get('dmg') or '-'}"
    )
    if include_notes and get_notes_text(row):
        text += f"\nNotes: {get_notes_text(row)}"
    return text


load_move_image_urls()
