"""Builds generated move image URL caches in bubbot/data/.
Regenerates remote image, hitbox, and notes URL dicts for supported games.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from bubbot.data.ggst_aliases import GGST_PAGE_OVERRIDES
from bubbot.frame_data import ggst_frame_data
from bubbot.utils.mediawiki_images import mediawiki_thumb_url as shared_mediawiki_thumb_url
from bubbot.utils.text_utils import compact_key
from scripts import scraper_utils
from scripts.generation_utils import write_python_constants  # noqa: E402


SF6_API_URL = "https://wiki.supercombo.gg/api.php"
GGST_API_URL = "https://www.dustloop.com/wiki/api.php"
FRAME_IMAGE_THUMB_WIDTH = 220
SF6_HITBOX_IMAGE_WIDTH = 400
GGST_MOVE_SHEET_SUFFIXES = [
    "Dragon Install",
    "Install",
    "Normal",
    "Spells",
    "Items",
    "L2",
    "L3",
    "BR",
]


def normalize_key(value: object) -> str:
    return compact_key(value)


def wiki_request(api_url: str, params: dict[str, object], sleep_seconds: float) -> dict:
    return scraper_utils.wiki_request(api_url, params, sleep_seconds)




def resolve_file_urls(api_url: str, filenames: list[str], thumb_width: int, sleep_seconds: float) -> dict[str, str]:
    return scraper_utils.resolve_file_urls(api_url, filenames, thumb_width, sleep_seconds)



def mediawiki_thumb_url(api_url: str, filename: str, thumb_width: int = FRAME_IMAGE_THUMB_WIDTH) -> str:
    return shared_mediawiki_thumb_url(api_url, filename, thumb_width)


def write_python_cache(path: Path, variable_name: str, data: dict) -> None:
    write_python_constants(path, {variable_name: data}, "build_move_image_caches.py")


# SF6 embeds show SuperCombo's hitbox images. The wiki's SF6_FrameData Cargo table lists each move's
# input, name and hitbox files; FAT writes inputs differently, so each FAT numCmd is translated to
# SuperCombo's notation before matching.


def fetch_sf6_supercombo_rows(sleep_seconds: float) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    while True:
        data = wiki_request(
            SF6_API_URL,
            {
                "action": "cargoquery",
                "tables": "SF6_FrameData",
                "fields": "chara,input,name,hitboxes",
                "limit": 500,
                "offset": len(rows),
                "format": "json",
            },
            sleep_seconds,
        )
        batch = [item.get("title") or {} for item in data.get("cargoquery") or []]
        rows.extend(batch)
        if len(batch) < 500:
            return rows


def sf6_hitbox_files(value: object) -> list[str]:
    # Comma-separated; rows without hitboxes hold the template's own "{{{hitboxes}}}".
    names = (name.strip() for name in str(value or "").split(","))
    return [name for name in names if re.search(r"\.(?:png|webp|gif|jpe?g)$", name, re.IGNORECASE)]


def sf6_input_key(value: object) -> str:
    """Input notation without spaces or dots, keeping [held] buttons, ~ chains and / alternatives."""
    text = html.unescape(str(value or "")).lower().replace(">", "~")
    text = re.sub(r"\(hold ok\)", "", text)
    text = re.sub(r"\s+or\s+", "/", text)
    return re.sub(r"[^a-z0-9\[\]~/+]", "", text)


def _supercombo_segment(segment: str, airborne: bool, after_jump: bool) -> tuple[str, bool]:
    hold = bool(re.search(r"\(hold\)", segment, re.IGNORECASE))
    air = airborne or bool(re.search(r"\(air\)", segment, re.IGNORECASE))
    segment = re.sub(r"\((?:hold|air|cancel|ca)\)", "", segment, flags=re.IGNORECASE).strip()
    segment = re.sub(r"^(4(?=6)|2(?=8))", r"[\1]", segment)  # charge: 46MP is [4]6MP
    segment = re.sub(r"^7\s*or\s*9(?=\D)", "8", segment, flags=re.IGNORECASE)
    # 8LP is j.LP, but after a bare "8 >" jump an 8HK is the air command j.8HK.
    if not after_jump and re.match(r"^[789](?=[A-Z])", segment):
        segment, air = segment[1:], True
    if hold:
        segment = re.sub(r"([LMH]?[PK]{1,3})$", r"[\1]", segment)
    return ("j." if air else "") + segment, air


def _sf6_spellings(text: str) -> list[str]:
    """Other ways SuperCombo writes a FAT input: '2PP > 5K' is 2PP~K, '5KK' is KK, '4268LP' is 360+LP,
    '624LK' is 63214LK, '(proximity)' is '(close)', and '6 or 3KKK' is listed as 6KKK."""
    spellings = [
        re.sub(r">\s*5(?=[PK]\b)", "> ", text),
        re.sub(r"^5(?=(?:[LMH]?[PK])+\b)", "", text),
        re.sub(r"^4268", "360+", text),
        re.sub(r"^624", "63214", text),
        text.replace("(proximity)", "(close)"),
    ]
    either = re.fullmatch(r"([1-9])\s*or\s*([1-9])(\D\S*)", text)
    if either:
        spellings += [f"{either[1]}{either[3]}", f"{either[2]}{either[3]}"]
    return [spelling for spelling in dict.fromkeys(spellings) if spelling != text]


def sf6_fat_input_keys(num_cmd: object) -> list[str]:
    """SuperCombo input keys for a FAT numCmd, best first: as written, then in SuperCombo's notation, then
    for each of SuperCombo's other spellings of it."""
    text = str(num_cmd or "").strip()
    keys = _sf6_notation_keys(text)
    for spelling in _sf6_spellings(text):
        keys += _sf6_notation_keys(spelling)
    return list(dict.fromkeys(key for key in keys if key))


def _sf6_notation_keys(text: str) -> list[str]:
    keys = [sf6_input_key(text)]
    either = re.fullmatch(r"([1-9])\s*or\s*([1-9])(\D\S*)", text)
    if either:
        keys.append(sf6_input_key(f"{either[1]}{either[3]} or {either[2]}{either[3]}"))
    parts = [part for part in re.split(r"\s*>\s*", text) if part]
    after_jump = len(parts) > 1 and bool(re.fullmatch(r"[789]", parts[0]))
    if after_jump:
        parts = parts[1:]
    segments, air = [], after_jump
    for part in parts:
        segment, air = _supercombo_segment(part, air, after_jump)
        segments.append(segment)
    if segments:
        keys.append(sf6_input_key("~".join(segments)))
        # SuperCombo writes follow-ups without the neutral 5 ("5MP~MK"), sometimes without a repeated j.
        keys.append(sf6_input_key("~".join([segments[0]] + [re.sub(r"^(j\.)?5(?=[LMH][PK]|\[)", r"\1", s) for s in segments[1:]])))
        keys.append(sf6_input_key("~".join([segments[0]] + [re.sub(r"^j\.", "", s) for s in segments[1:]])))
    return keys


def _sf6_base_name(name: object) -> str:
    return normalize_key(re.sub(r"\s*(?:\([^)]*\)|lv\.?\s*\d+)\s*$", "", str(name or ""), flags=re.IGNORECASE))


def pick_sf6_supercombo_row(candidates: list[dict[str, str]], move_name: object) -> dict[str, str] | None:
    """The SuperCombo row for a FAT move among those sharing its input, or None when they can't be told apart."""
    if len({row["hitboxes"] for row in candidates}) == 1:
        return candidates[0]
    name = normalize_key(re.sub(r"\(critical art\)", "(CA)", str(move_name or ""), flags=re.IGNORECASE))
    for matches in (
        lambda row: normalize_key(row["name"]) == name,
        lambda row: normalize_key(row["name"]).startswith(name) or _sf6_base_name(row["name"]) == name,
    ):
        hits = [row for row in candidates if matches(row)]
        if hits:
            return hits[0]
    plain = [row for row in candidates if "(" not in row["name"]]
    if len({row["hitboxes"] for row in plain}) == 1:
        return plain[0]
    # Versions of one move (drink levels, Lv.1/Lv.2): the first, base version.
    return candidates[0] if len({_sf6_base_name(row["name"]) for row in candidates}) == 1 else None


def build_sf6_cache(workbook_path: Path, output_path: Path, sleep_seconds: float) -> int:
    index: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in fetch_sf6_supercombo_rows(sleep_seconds):
        if sf6_hitbox_files(row.get("hitboxes")):
            index.setdefault((normalize_key(row.get("chara")), sf6_input_key(row.get("input"))), []).append(row)

    xls = pd.ExcelFile(workbook_path, engine="odf")
    # The bot looks images up by character and compacted numCmd, so rows sharing one get images only
    # when they also share the hitboxes.
    pending: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {}
    conflicts: set[tuple[str, str]] = set()
    for sheet_name in xls.sheet_names:
        if not sheet_name.endswith("Normal"):
            continue
        char_key = normalize_key(sheet_name[: -len("Normal")].rstrip("."))
        for row in pd.read_excel(xls, sheet_name=sheet_name).fillna("").to_dict("records"):
            num_cmd = str(row.get("numCmd", "")).strip()
            candidates = next((index[(char_key, key)] for key in sf6_fat_input_keys(num_cmd) if (char_key, key) in index), [])
            chosen = pick_sf6_supercombo_row(candidates, row.get("moveName")) if candidates else None
            if not chosen:
                continue
            filenames = tuple(sf6_hitbox_files(chosen["hitboxes"]))
            key = (char_key, normalize_key(num_cmd))
            if key in pending and pending[key][1] != filenames:
                conflicts.add(key)
            pending.setdefault(key, (num_cmd, filenames))
    for key in conflicts:
        del pending[key]

    # Resolved through the API: exact URLs, never wider than the file, and missing files drop out.
    urls = resolve_file_urls(SF6_API_URL, [name for _num_cmd, names in pending.values() for name in names], SF6_HITBOX_IMAGE_WIDTH, sleep_seconds)
    # Every hitbox image in SuperCombo's order: the card shows the first, Show All Images the rest.
    cache: dict[str, dict[str, list[str]]] = {}
    for (char_key, _key), (num_cmd, filenames) in pending.items():
        move_urls = [url for url in (scraper_utils.file_url_for_name(urls, name) for name in filenames) if url]
        if move_urls:
            cache.setdefault(char_key, {})[num_cmd] = move_urls

    write_python_cache(output_path, "SF6_HITBOX_IMAGE_URLS", cache)
    print(f"[sf6-images] {len(conflicts)} numCmds left out: their FAT rows have different hitboxes", flush=True)
    return sum(len(moves) for moves in cache.values())


def ggst_page_title(character_label: str) -> str:
    page_name = GGST_PAGE_OVERRIDES.get(character_label, character_label.replace(" ", "_"))
    return f"GGST/{page_name}"


def fetch_ggst_page_images(character_label: str, sleep_seconds: float) -> list[str]:
    data = wiki_request(
        GGST_API_URL,
        {
            "action": "parse",
            "page": ggst_page_title(character_label),
            "prop": "images",
            "format": "json",
        },
        sleep_seconds,
    )
    return list((data.get("parse") or {}).get("images") or [])


def ggst_match_key(value: object) -> str:
    text = str(value or "").lower().replace("hs", "h")
    if "4d" in text and "6d" in text:
        air_throw = "air" in text or "j." in text or "j4d" in text or "j6d" in text
        return "j4dj6d" if air_throw else "4d6d"
    text = re.sub(r"\bor\b", "", text)
    text = re.sub(r"\(air ok\)|\(hold ok\)", "", text)
    return re.sub(r"[^a-z0-9]", "", text)


def split_ggst_filenames(value: object) -> list[str]:
    return [part.strip() for part in re.split(r"\s*;\s*", str(value or "")) if part.strip()]


def clean_ggst_notes_text(value: object) -> str:
    text = str(value or "").replace("�", "'").replace("’", "'").replace("‘", "'").strip()
    if not text:
        return ""
    lines = []
    for line in text.splitlines():
        if line.strip().lower().startswith("gatling options:"):
            continue
        lines.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def build_ggst_notes_text(source_row: dict[str, object]) -> str:
    parts = []
    description = clean_ggst_notes_text(source_row.get("description_text"))
    notes = clean_ggst_notes_text(source_row.get("notes_text"))
    caption = clean_ggst_notes_text(source_row.get("caption_text"))
    if description:
        parts.append(description)
    if notes:
        parts.append(notes)
    if caption and caption not in parts:
        parts.append(caption)
    return "\n\n".join(parts).strip()


def ggst_sheet_character_label(sheet_name: str) -> str:
    if sheet_name in {"IdealSheetNormal", "TemplateNormal"} or sheet_name.endswith("Stats"):
        return ""
    for suffix in GGST_MOVE_SHEET_SUFFIXES:
        if sheet_name.endswith(suffix):
            return sheet_name[: -len(suffix)]
    return ""


def ggst_filename_move_key(filename: str, character_label: str, hitbox: bool) -> str:
    stem = re.sub(r"\.(?:png|webp|gif|jpg|jpeg)$", "", filename, flags=re.IGNORECASE)
    if not stem.lower().startswith(("ggst_", "gggst_")):
        return ""
    if any(token in stem.lower() for token in ("portrait", "icon", "navigation", "color")):
        return ""
    if hitbox != ("hitbox" in stem.lower()):
        return ""
    text = stem.lower()
    character_tokens = [character_label]
    if character_label in GGST_PAGE_OVERRIDES:
        character_tokens.append(GGST_PAGE_OVERRIDES[character_label].replace("_", " "))
    for token in sorted(character_tokens, key=len, reverse=True):
        compact = re.sub(r"[^a-z0-9]+", "_", token.lower()).strip("_")
        text = re.sub(rf"^ggg?st_{re.escape(compact)}_", "", text)
    text = re.sub(r"^ggg?st_[a-z0-9.\-]+_", "", text)
    text = re.sub(r"_?hitbox(?:_preview)?(?:_?\d+)?$", "", text)
    text = re.sub(r"_\d+$", "", text)
    text = text.replace("_", "")
    return ggst_frame_data.normalize_move_token(text)


def ggst_row_match_keys(row: dict[str, object]) -> set[str]:
    keys: set[str] = set()
    values = [row.get("numCmd", ""), row.get("dustloopKey", ""), row.get("moveName", "")]
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        keys.add(ggst_match_key(text))
        keys.add(ggst_match_key(ggst_frame_data.normalize_move_token(text)))
        for alt in ggst_frame_data.expand_or_command_alternatives(text):
            keys.add(ggst_match_key(alt))
            keys.add(ggst_match_key(ggst_frame_data.normalize_move_token(alt)))
    return {key for key in keys if key}


def ggst_image_match_keys(filename_key: str) -> set[str]:
    raw = str(filename_key or "").strip()
    keys = {ggst_match_key(raw)} if raw else set()
    if not raw:
        return keys
    variants = {raw}
    if "x" in raw.lower():
        variants.update(raw.lower().replace("x", button) for button in ("p", "k", "s", "h", "d"))
    for variant in variants:
        keys.add(ggst_match_key(variant))
        keys.add(ggst_match_key(ggst_frame_data.normalize_move_token(variant)))
    return {key for key in keys if key}


def build_ggst_cache(workbook_path: Path, output_path: Path, sleep_seconds: float, resolve_urls: bool) -> tuple[int, int]:
    def fetch(page_title: str, character_label: str) -> str:
        for attempt in range(3):
            try:
                return scraper_utils.fetch_text(page_title, sleep_seconds=sleep_seconds)
            except Exception:
                if attempt == 2:
                    raise
                print(f"[ggst-images] retrying {character_label}: {page_title}", flush=True)
        raise AssertionError("unreachable")

    xls = pd.ExcelFile(workbook_path, engine="odf")
    normal_pending: dict[tuple[str, str], str] = {}
    hitbox_pending: dict[tuple[str, str], list[str]] = {}
    notes_pending: dict[tuple[str, str], str] = {}
    source_cache: dict[str, list[dict[str, str]]] = {}
    filenames: list[str] = []

    for sheet_name in xls.sheet_names:
        character_label = ggst_sheet_character_label(sheet_name)
        if not character_label:
            continue
        char_key = ggst_frame_data.resolve_character_key(character_label) or character_label.strip().lower()
        df = pd.read_excel(xls, sheet_name=sheet_name).fillna("")
        row_index: dict[str, set[str]] = {}
        for row in df.to_dict("records"):
            num_cmd = str(row.get("numCmd", "")).strip()
            move_name = str(row.get("moveName", "")).strip()
            if not num_cmd or not move_name:
                continue
            move_key = ggst_frame_data.normalize_move_token(num_cmd)
            if move_key:
                for match_key in ggst_row_match_keys(row):
                    row_index.setdefault(match_key, set()).add(move_key)
        if not row_index:
            continue
        if {"images", "hitboxes"}.issubset(df.columns):
            for row in df.to_dict("records"):
                num_cmd = str(row.get("numCmd", "")).strip()
                if not num_cmd:
                    continue
                move_key = ggst_frame_data.normalize_move_token(num_cmd)
                image_files = split_ggst_filenames(row.get("images"))
                hitbox_files = split_ggst_filenames(row.get("hitboxes"))
                if image_files and (char_key, move_key) not in normal_pending:
                    normal_pending[(char_key, move_key)] = image_files[0]
                    filenames.append(image_files[0])
                if hitbox_files:
                    pending_hitboxes = hitbox_pending.setdefault((char_key, move_key), [])
                    for hitbox_file in hitbox_files:
                        if hitbox_file not in pending_hitboxes:
                            pending_hitboxes.append(hitbox_file)
                    filenames.extend(hitbox_files)
        try:
            main_page_title = ggst_page_title(character_label)
            source_moves = source_cache.get(main_page_title)
            if source_moves is None:
                data_page_title = f"{main_page_title}/Data"
                overview_raw = fetch(main_page_title, character_label)
                data_raw = fetch(data_page_title, character_label)
                _overview_row, description_rows = scraper_utils.parse_overview_page(
                    character_label,
                    character_label,
                    main_page_title,
                    overview_raw,
                )
                description_index = scraper_utils.build_description_index(description_rows)
                _stat_rows, source_moves = scraper_utils.parse_data_page(
                    character_label,
                    character_label,
                    data_page_title,
                    data_raw,
                    description_index,
                )
                source_cache[main_page_title] = source_moves
        except Exception as exc:
            raise RuntimeError(f"[ggst-images] failed {character_label}: {exc}") from exc

        for source_row in source_moves:
            source_keys = set()
            for value in (source_row.get("input", ""), source_row.get("name", "")):
                if str(value or "").strip():
                    source_keys.add(ggst_match_key(value))
                    source_keys.add(ggst_match_key(ggst_frame_data.normalize_move_token(value)))
            target_move_keys = set()
            for source_key in source_keys:
                target_move_keys.update(row_index.get(source_key, set()))
            if not target_move_keys:
                continue

            image_files = split_ggst_filenames(source_row.get("images"))
            hitbox_files = split_ggst_filenames(source_row.get("hitboxes"))
            notes_text = build_ggst_notes_text(source_row)
            for move_key in target_move_keys:
                if notes_text and (char_key, move_key) not in notes_pending:
                    notes_pending[(char_key, move_key)] = notes_text
                if image_files and (char_key, move_key) not in normal_pending:
                    normal_pending[(char_key, move_key)] = image_files[0]
                    filenames.append(image_files[0])
                if hitbox_files:
                    pending_hitboxes = hitbox_pending.setdefault((char_key, move_key), [])
                    for hitbox_file in hitbox_files:
                        if hitbox_file not in pending_hitboxes:
                            pending_hitboxes.append(hitbox_file)
                    filenames.extend(hitbox_files)

    urls = resolve_file_urls(GGST_API_URL, filenames, thumb_width=FRAME_IMAGE_THUMB_WIDTH, sleep_seconds=sleep_seconds) if resolve_urls else {}
    normal_cache = {}
    hitbox_cache = {}
    for (char_key, move_key), filename in normal_pending.items():
        url = scraper_utils.file_url_for_name(urls, filename) or mediawiki_thumb_url(GGST_API_URL, filename)
        if url:
            normal_cache.setdefault(char_key, {})[move_key] = url
    for (char_key, move_key), filenames_for_move in hitbox_pending.items():
        move_urls = [scraper_utils.file_url_for_name(urls, name) or mediawiki_thumb_url(GGST_API_URL, name) for name in filenames_for_move]
        move_urls = [url for url in move_urls if url]
        if move_urls:
            hitbox_cache.setdefault(char_key, {})[move_key] = move_urls
    notes_cache = {}
    for (char_key, move_key), notes_text in notes_pending.items():
        if notes_text:
            notes_cache.setdefault(char_key, {})[move_key] = notes_text

    write_python_constants(
        output_path,
        {
            "GGST_MOVE_IMAGE_URLS": normal_cache,
            "GGST_HITBOX_DATA": hitbox_cache,
            "GGST_MOVE_NOTES": notes_cache,
        },
        "build_move_image_caches.py",
    )
    return (
        sum(len(moves) for moves in normal_cache.values()),
        sum(len(links) for moves in hitbox_cache.values() for links in moves.values()),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build remote image URL caches for SF6 and GGST frame embeds.")
    parser.add_argument("--sf6-workbook", default="FAT - SF6 Frame Data.ods")
    parser.add_argument("--ggst-workbook", default="GGST Frame Data.ods")
    parser.add_argument("--sf6-output", default="bubbot/data/sf6_move_images.py")
    parser.add_argument("--ggst-output", default="bubbot/data/ggst_move_images.py")
    parser.add_argument("--sleep", type=float, default=0.1)
    parser.add_argument("--resolve-urls", action="store_true", help="GGST: resolve file URLs through the wiki API instead of deriving direct thumbnail URLs locally (SF6 always resolves).")
    parser.add_argument("--skip-sf6", action="store_true")
    parser.add_argument("--skip-ggst", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.skip_sf6:
        count = build_sf6_cache(Path(args.sf6_workbook), Path(args.sf6_output), args.sleep)
        print(f"Wrote {args.sf6_output}: {count} SF6 hitbox image links")
    if not args.skip_ggst:
        normal_count, hitbox_count = build_ggst_cache(Path(args.ggst_workbook), Path(args.ggst_output), args.sleep, args.resolve_urls)
        print(f"Wrote {args.ggst_output}: {normal_count} GGST move image links, {hitbox_count} GGST hitbox links")


if __name__ == "__main__":
    main()
