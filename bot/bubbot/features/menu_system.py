"""Discord /sfstatsmenu menu: game/char/move selectors, frame results, combos, quiz launch.

Menus are SF Stats cards (``MenuView``, see bubbot.utils.sfstats_style) and results stay embeds.
A card can't become an embed, so menus only edit into menus (``_show_menu``), picking a result
posts it as a new message, and a result's buttons open menus as new private cards (``_open_menu``).
"""

import io
import math
import re

import discord

from bubbot.features.fg_glossary import build_glossary_definition_embed, build_glossary_link_button, normalize_glossary_term
from bubbot.utils.choice_utils import character_choices, move_choices
from bubbot.utils.sfstats_style import ACTION, ACTIVE, DIVIDER, CardView, card_text, embed_card_blocks

FRAME_DATA = {}
FRAME_STATS = {}
CHARACTER_ALIASES = {}
GGST_FRAME_DATA = {}
GGST_CHARACTER_ALIASES = {}
GGST_SUPPLEMENTAL_FRAME_DATA = {}
GGST_STATE_FRAME_DATA = {}
SFV_FRAME_DATA = {}
SFV_CHARACTER_ALIASES = {}
SFV_TRIGGER_FRAME_DATA = {}
TUCO_FRAME_DATA = {}
TUCO_CHARACTER_ALIASES = {}
BBCF_FRAME_DATA = {}
BBCF_CHARACTER_ALIASES = {}
GGACR_FRAME_DATA = {}
GGACR_CHARACTER_ALIASES = {}
COTW_FRAME_DATA = {}
COTW_CHARACTER_ALIASES = {}
THIRD_STRIKE_FRAME_DATA = {}
THIRD_STRIKE_CHARACTER_ALIASES = {}
USFIV_FRAME_DATA = {}
USFIV_CHARACTER_ALIASES = {}
MK1_FRAME_DATA = {}
MK1_CHARACTER_ALIASES = {}
AVTL_FRAME_DATA = {}
AVTL_CHARACTER_ALIASES = {}

quiz_module = None
build_sf6_frame_embed = None
build_ggst_frame_embed = None
build_sfv_frame_embed = None
build_tuco_frame_embed = None
build_avtl_frame_embed = None
build_bbcf_frame_embed = None
build_ggacr_frame_embed = None
build_cotw_frame_embed = None
build_third_strike_frame_embed = None
build_usfiv_frame_embed = None
send_frame_embeds_with_views = None


# Runtime injection from bot startup
def configure(
    frame_data=None,
    frame_stats=None,
    character_aliases=None,
    ggst_frame_data=None,
    ggst_character_aliases=None,
    ggst_supplemental_frame_data=None,
    ggst_state_frame_data=None,
    sfv_frame_data=None,
    sfv_character_aliases=None,
    sfv_trigger_frame_data=None,
    tuco_frame_data=None,
    tuco_character_aliases=None,
    bbcf_frame_data=None,
    bbcf_character_aliases=None,
    ggacr_frame_data=None,
    ggacr_character_aliases=None,
    cotw_frame_data=None,
    cotw_character_aliases=None,
    third_strike_frame_data=None,
    third_strike_character_aliases=None,
    usfiv_frame_data=None,
    usfiv_character_aliases=None,
    mk1_frame_data=None,
    mk1_character_aliases=None,
    avtl_frame_data=None,
    avtl_character_aliases=None,
    quiz_module_ref=None,
    build_sf6_frame_embed_fn=None,
    build_ggst_frame_embed_fn=None,
    build_sfv_frame_embed_fn=None,
    build_tuco_frame_embed_fn=None,
    build_bbcf_frame_embed_fn=None,
    build_ggacr_frame_embed_fn=None,
    build_cotw_frame_embed_fn=None,
    build_third_strike_frame_embed_fn=None,
    build_usfiv_frame_embed_fn=None,
    build_avtl_frame_embed_fn=None,
    send_frame_embeds_with_views_fn=None,
):
    global FRAME_DATA, FRAME_STATS, CHARACTER_ALIASES, GGST_FRAME_DATA, GGST_CHARACTER_ALIASES, GGST_SUPPLEMENTAL_FRAME_DATA, GGST_STATE_FRAME_DATA, SFV_FRAME_DATA, SFV_CHARACTER_ALIASES, SFV_TRIGGER_FRAME_DATA, TUCO_FRAME_DATA, TUCO_CHARACTER_ALIASES, BBCF_FRAME_DATA, BBCF_CHARACTER_ALIASES, GGACR_FRAME_DATA, GGACR_CHARACTER_ALIASES, COTW_FRAME_DATA, COTW_CHARACTER_ALIASES, THIRD_STRIKE_FRAME_DATA, THIRD_STRIKE_CHARACTER_ALIASES, USFIV_FRAME_DATA, USFIV_CHARACTER_ALIASES, MK1_FRAME_DATA, MK1_CHARACTER_ALIASES, AVTL_FRAME_DATA, AVTL_CHARACTER_ALIASES
    global quiz_module, build_sf6_frame_embed, build_ggst_frame_embed, build_sfv_frame_embed, build_tuco_frame_embed, build_avtl_frame_embed, build_bbcf_frame_embed, build_ggacr_frame_embed, build_cotw_frame_embed, build_third_strike_frame_embed, build_usfiv_frame_embed, send_frame_embeds_with_views
    FRAME_DATA = frame_data or {}
    FRAME_STATS = frame_stats or {}
    CHARACTER_ALIASES = character_aliases or {}
    GGST_FRAME_DATA = ggst_frame_data or {}
    GGST_CHARACTER_ALIASES = ggst_character_aliases or {}
    GGST_SUPPLEMENTAL_FRAME_DATA = ggst_supplemental_frame_data or {}
    GGST_STATE_FRAME_DATA = ggst_state_frame_data or {}
    SFV_FRAME_DATA = sfv_frame_data or {}
    SFV_CHARACTER_ALIASES = sfv_character_aliases or {}
    SFV_TRIGGER_FRAME_DATA = sfv_trigger_frame_data or {}
    TUCO_FRAME_DATA = tuco_frame_data or {}
    TUCO_CHARACTER_ALIASES = tuco_character_aliases or {}
    BBCF_FRAME_DATA = bbcf_frame_data or {}
    BBCF_CHARACTER_ALIASES = bbcf_character_aliases or {}
    GGACR_FRAME_DATA = ggacr_frame_data or {}
    GGACR_CHARACTER_ALIASES = ggacr_character_aliases or {}
    COTW_FRAME_DATA = cotw_frame_data or {}
    COTW_CHARACTER_ALIASES = cotw_character_aliases or {}
    THIRD_STRIKE_FRAME_DATA = third_strike_frame_data or {}
    THIRD_STRIKE_CHARACTER_ALIASES = third_strike_character_aliases or {}
    USFIV_FRAME_DATA = usfiv_frame_data or {}
    USFIV_CHARACTER_ALIASES = usfiv_character_aliases or {}
    MK1_FRAME_DATA = mk1_frame_data or {}
    MK1_CHARACTER_ALIASES = mk1_character_aliases or {}
    AVTL_FRAME_DATA = avtl_frame_data or {}
    AVTL_CHARACTER_ALIASES = avtl_character_aliases or {}
    quiz_module = quiz_module_ref
    build_sf6_frame_embed = build_sf6_frame_embed_fn
    build_ggst_frame_embed = build_ggst_frame_embed_fn
    build_sfv_frame_embed = build_sfv_frame_embed_fn
    build_tuco_frame_embed = build_tuco_frame_embed_fn
    build_bbcf_frame_embed = build_bbcf_frame_embed_fn
    build_ggacr_frame_embed = build_ggacr_frame_embed_fn
    build_cotw_frame_embed = build_cotw_frame_embed_fn
    build_third_strike_frame_embed = build_third_strike_frame_embed_fn
    build_usfiv_frame_embed = build_usfiv_frame_embed_fn
    build_avtl_frame_embed = build_avtl_frame_embed_fn
    send_frame_embeds_with_views = send_frame_embeds_with_views_fn


MENU_SELECT_LIMIT = 25

# Full display names for menu dropdown and card titles (no abbreviations)
MENU_GAMES = (
    ("avtl", "Avatar Legends"),
    ("tuco", "2XKO"),
    ("bbcf", "BlazBlue Central Fiction"),
    ("cotw", "Fatal Fury: City of the Wolves"),
    ("ggacr", "Guilty Gear Accent Core Plus R"),
    ("ggst", "Guilty Gear Strive"),
    ("mk1", "Mortal Kombat 1"),
    ("sf6", "Street Fighter 6"),
    ("third_strike", "Street Fighter III: 3rd Strike"),
    ("usf4", "Ultra Street Fighter IV"),
    ("sfv", "Street Fighter V"),
)
_MENU_GAME_LABELS = dict(MENU_GAMES)


# Base views: owner lock for menus and the results they open
async def _owner_allows(view, interaction):
    if (interaction.data or {}).get("custom_id") == "bub_failed_prompt_report":
        return True
    if not view.menu_locked or view.owner_id is None or interaction.user.id == view.owner_id:
        return True
    await interaction.response.send_message(
        "Only the person who opened this menu can control it.",
        ephemeral=True,
    )
    return False


class OwnedView(discord.ui.View):
    """Buttons on a result (an embed)."""

    def __init__(self, owner_id, *, menu_locked=False, timeout=None):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.menu_locked = bool(menu_locked)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _owner_allows(self, interaction)


class MenuView(CardView):
    """A menu: an SF Stats card with its controls below."""

    def __init__(self, owner_id, *, menu_locked=False, timeout=None):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.menu_locked = bool(menu_locked)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _owner_allows(self, interaction)


async def _show_menu(interaction, view):
    """Replace the menu this interaction came from with another menu."""
    await interaction.response.edit_message(view=view, attachments=[])


async def _open_menu(interaction, view):
    """From a result: it stays an embed, so the menu opens as a new card only the clicker sees."""
    await interaction.response.send_message(view=view, ephemeral=True)


def _notice_menu(owner_id, *lines, title=None):
    """A menu with no controls left: what happened, as one short card."""
    return MenuView(owner_id, menu_locked=True).set_card(card_text(title, *lines))


def _menu_locked_owner_id(view):
    if getattr(view, "menu_locked", False):
        return getattr(view, "owner_id", None)
    return None


# Game rosters, colours, move lists, search filters
def _sf6_character_list():
    return character_choices(FRAME_DATA, display_fn=lambda char_key, _rows: str(char_key).title())


def _sf6_stats_character_list():
    return character_choices(
        FRAME_STATS,
        display_fn=lambda char_key, _rows: str(char_key).replace(".", " ").replace("_", " ").title(),
    )


def _game_menu_view(game, owner_id):
    if game == "sf6":
        return SF6GameMenuView(owner_id)
    return GameMenuView(game, owner_id)


async def _edit_to_game_menu(interaction, game, owner_id):
    await _show_menu(interaction, _game_menu_view(game, owner_id))


async def _open_framedata_character_select(interaction, game, owner_id, *, compare_row=None, compare_char_key=None):
    chars = _character_list(game)
    if not chars:
        await interaction.response.send_message("No character data loaded.", ephemeral=True)
        return
    await _show_menu(
        interaction,
        CharacterSelectView(game, chars, owner_id, page=0, compare_row=compare_row, compare_char_key=compare_char_key),
    )


async def _open_sf6_stats_character_select(interaction, owner_id):
    chars = _sf6_stats_character_list()
    if not chars:
        await interaction.response.send_message("No character stats loaded.", ephemeral=True)
        return
    await _show_menu(interaction, CharacterSelectView("sf6", chars, owner_id, page=0, stats_mode=True))


async def _open_quiz_difficulty(interaction, game, owner_id):
    await _show_menu(interaction, QuizDifficultyView(game, owner_id))


async def _open_combo_character_select(interaction, game, owner_id):
    from bubbot.frame_data import combo_data

    chars = combo_data.combo_character_list(game)
    if not chars:
        await interaction.response.send_message(f"No {combo_data.game_label(game)} combo data loaded.", ephemeral=True)
        return
    await _show_menu(interaction, ComboCharacterSelectView(game, chars, owner_id, page=0))


async def _open_game_combos_menu(interaction, game, owner_id):
    from bubbot.frame_data import combo_data

    game_key = str(game or "").strip().lower()
    if game_key not in combo_data.COMBO_GAMES:
        await _show_menu(
            interaction,
            BackToGameMenuView(game_key, owner_id, card_text(f"{_game_label(game_key)} · Combos", "Combos will be added to the scrolls soon.")),
        )
        return
    combo_data.ensure_combo_data_loaded(game_key)
    if combo_data.has_combos(game_key):
        await _open_combo_character_select(interaction, game_key, owner_id)
        return
    missing_file = combo_data.SF6_COMBOS_FILE if game_key == "sf6" else combo_data.MK1_COMBOS_FILE
    await _show_menu(
        interaction,
        BackToGameMenuView(
            game_key,
            owner_id,
            card_text(
                f"{combo_data.game_label(game_key)} · Combos",
                "Combo data did not load.",
                footer=f"Deploy `{missing_file}` next to `bot.py` on the server and restart the bot",
            ),
        ),
    )


async def _post_sf6_stats(interaction, char_key, owner_id, stat_keys=None):
    from bubbot.features.failed_prompt_report import stamp_report_context_on_sent

    stats_row = FRAME_STATS.get(char_key)
    if not stats_row:
        await interaction.response.send_message("No stats loaded for this character.", ephemeral=True)
        return
    from bubbot.frame_data.sf6_character_stats import build_character_stats_embed

    embed = build_character_stats_embed(char_key, stats_row, stat_keys)
    view = StatsResultView(char_key, owner_id, stat_keys=stat_keys, menu_locked=True)
    await interaction.response.send_message(embed=embed, view=view)
    try:
        stamp_report_context_on_sent(view, await interaction.original_response())
    except Exception:
        pass


async def _post_sf6_stats_compare(interaction, char_key_a, char_key_b, owner_id, stat_keys=None, *, menu_locked=True):
    from bubbot.features.failed_prompt_report import stamp_report_context_on_sent

    stats_a = FRAME_STATS.get(char_key_a)
    stats_b = FRAME_STATS.get(char_key_b)
    if not stats_a or not stats_b:
        await interaction.response.send_message("No stats loaded for one of those characters.", ephemeral=True)
        return
    from bubbot.frame_data.sf6_character_stats import build_character_stats_comparison_embed

    embed = build_character_stats_comparison_embed(char_key_a, stats_a, char_key_b, stats_b, stat_keys)
    view = StatsResultView(
        char_key_a,
        owner_id,
        stat_keys=stat_keys,
        compare_char_key=char_key_b,
        menu_locked=menu_locked,
    )
    await interaction.response.send_message(embed=embed, view=view)
    try:
        stamp_report_context_on_sent(view, await interaction.original_response())
    except Exception:
        pass


def _ggst_character_list():
    return character_choices(GGST_FRAME_DATA)


def _sfv_character_list():
    from bubbot.frame_data.sfv_frame_data import display_char_name
    return character_choices(SFV_FRAME_DATA, display_fn=lambda char_key, _rows: display_char_name(char_key))


def _tuco_character_list():
    return character_choices(TUCO_FRAME_DATA)


def _bbcf_character_list():
    return character_choices(BBCF_FRAME_DATA)


def _ggacr_character_list():
    return character_choices(GGACR_FRAME_DATA)


def _cotw_character_list():
    return character_choices(COTW_FRAME_DATA)


def _third_strike_character_list():
    return character_choices(THIRD_STRIKE_FRAME_DATA)


def _usfiv_character_list():
    return character_choices(USFIV_FRAME_DATA)


def _avtl_character_list():
    return character_choices(AVTL_FRAME_DATA)


def _mk1_character_list():
    from bubbot.frame_data.mk1_frame_data import display_char_name

    def display_fn(char_key, _rows):
        label = display_char_name(char_key)
        if str(char_key).startswith("kameo_"):
            label = re.sub(r"^Kameo\s+", "", label).strip()
            return f"{label} (Kameo)"
        return label

    return character_choices(MK1_FRAME_DATA, display_fn=display_fn)


_GAME_ONLY_MENTION_PATTERNS = (
    ("sf6", re.compile(r"^(?:sf6|street\s*fighter\s*6)$", re.IGNORECASE)),
    ("sfv", re.compile(r"^(?:sfv|sf5|street\s*fighter\s*(?:v|5))$", re.IGNORECASE)),
    # Guilty Gear defaults to Strive unless +R / Accent Core is explicit
    ("ggacr", None),  # resolved via match_ggacr_game_only()
    ("ggst", re.compile(r"^(?:ggst|guilty\s*gear(?:\s*strive)?|strive|guilty\s*gear)$", re.IGNORECASE)),
    ("tuco", re.compile(r"^(?:2xko|tuco)$", re.IGNORECASE)),
    ("bbcf", re.compile(r"^(?:bbcf|blazblue|central\s*fiction)$", re.IGNORECASE)),
    ("cotw", re.compile(r"^(?:cotw|city\s+of\s+the\s+wolves|fatal\s+fury)$", re.IGNORECASE)),
    (
        "third_strike",
        re.compile(r"^(?:3s|third\s*strike|street\s*fighter\s*(?:3|iii)|sf3|sfiii)$", re.IGNORECASE),
    ),
    ("usf4", re.compile(r"^(?:usf4|usfiv|sf4|ultra\s*street\s*fighter\s*(?:4|iv)|street\s*fighter\s*(?:4|iv))$", re.IGNORECASE)),
    ("mk1", re.compile(r"^(?:mk1|mortal\s+kombat(?:\s*(?:1|one))?)$", re.IGNORECASE)),
    ("avtl", re.compile(r"^(?:avtl|avatar\s*legends|avatar)$", re.IGNORECASE)),
)


def parse_game_only_mention(text):
    """Return game key when message is only a game tag (e.g. '@bub sf6')."""
    from bubbot.data.ggacr_aliases import match_ggacr_game_only

    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    if not normalized:
        return None
    if match_ggacr_game_only(normalized):
        return "ggacr"
    for game_key, pattern in _GAME_ONLY_MENTION_PATTERNS:
        if pattern is not None and pattern.fullmatch(normalized):
            return game_key
    return None


def _game_label(game):
    return _MENU_GAME_LABELS.get(str(game or "").strip().lower(), str(game or "").replace("_", " ").title())


def _character_list(game):
    if game == "sf6":
        return _sf6_character_list()
    if game == "ggst":
        return _ggst_character_list()
    if game == "sfv":
        return _sfv_character_list()
    if game == "tuco":
        return _tuco_character_list()
    if game == "bbcf":
        return _bbcf_character_list()
    if game == "ggacr":
        return _ggacr_character_list()
    if game == "cotw":
        return _cotw_character_list()
    if game == "third_strike":
        return _third_strike_character_list()
    if game == "usf4":
        return _usfiv_character_list()
    if game == "mk1":
        return _mk1_character_list()
    if game == "avtl":
        return _avtl_character_list()
    return []


def _sf6_move_list(char_key):
    return move_choices(FRAME_DATA.get(char_key, []))


def _ggst_move_list(char_key):
    def label_fn(row):
        move_name = str(row.get("moveName", "")).strip()
        num_cmd = str(row.get("numCmd", "")).strip()
        state_label = str(row.get("state_label", "")).strip()
        if state_label:
            return f"{move_name} ({num_cmd}) [{state_label}]"
        if num_cmd:
            return f"{move_name} ({num_cmd})"
        return move_name

    normal_rows = GGST_FRAME_DATA.get(char_key, [])
    supplemental_rows = GGST_SUPPLEMENTAL_FRAME_DATA.get(char_key, [])
    state_rows = GGST_STATE_FRAME_DATA.get(char_key, {})
    state_row_list = []
    for state_key_rows in state_rows.values():
        state_row_list.extend(state_key_rows)
    all_rows = normal_rows + supplemental_rows + state_row_list
    if not supplemental_rows and not state_row_list:
        return move_choices(normal_rows, label_fn=label_fn, key_fields=("moveName", "numCmd", "state_label"))
    seen = set()
    deduped = []
    for row in all_rows:
        key = (str(row.get("moveName", "")), str(row.get("numCmd", "")), str(row.get("state_key", "")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return move_choices(deduped, label_fn=label_fn, key_fields=("moveName", "numCmd", "state_label"))


def _tuco_move_list(char_key):
    return move_choices(TUCO_FRAME_DATA.get(char_key, []), key_fields=("moveName", "numCmd"))


def _sfv_move_list(char_key):
    def label_fn(row):
        move_name = str(row.get("moveName", "")).strip()
        num_cmd = str(row.get("numCmd", "")).strip()
        state_label = str(row.get("state_label", "")).strip()
        label = f"{move_name} ({num_cmd})" if move_name and num_cmd else move_name or num_cmd
        return f"{label} [{state_label}]" if state_label else label

    rows = list(SFV_FRAME_DATA.get(char_key, []) or [])
    for state_rows in (SFV_TRIGGER_FRAME_DATA.get(char_key, {}) or {}).values():
        rows.extend(state_rows)
    return move_choices(rows, label_fn=label_fn, key_fields=("moveName", "numCmd", "state_label"))


def _bbcf_move_list(char_key):
    return move_choices(BBCF_FRAME_DATA.get(char_key, []), key_fields=("moveName", "numCmd", "moveType"))


def _ggacr_move_list(char_key):
    return move_choices(GGACR_FRAME_DATA.get(char_key, []), key_fields=("moveName", "numCmd", "moveType"))


def _cotw_move_list(char_key):
    return move_choices(COTW_FRAME_DATA.get(char_key, []), key_fields=("moveName", "numCmd", "moveType"))


def _third_strike_move_list(char_key):
    return move_choices(THIRD_STRIKE_FRAME_DATA.get(char_key, []), key_fields=("moveName", "numCmd", "version", "moveType"))


def _usfiv_move_list(char_key):
    return move_choices(USFIV_FRAME_DATA.get(char_key, []), key_fields=("moveName", "numCmd", "version", "moveType"))


def _mk1_move_list(char_key):
    return move_choices(MK1_FRAME_DATA.get(char_key, []), key_fields=("moveName", "numCmd", "moveType"))


def _avtl_move_list(char_key):
    return move_choices(AVTL_FRAME_DATA.get(char_key, []), key_fields=("moveName", "numCmd", "moveType"))


def _move_list(game, char_key):
    if game == "sf6":
        return _sf6_move_list(char_key)
    if game == "ggst":
        return _ggst_move_list(char_key)
    if game == "sfv":
        return _sfv_move_list(char_key)
    if game == "tuco":
        return _tuco_move_list(char_key)
    if game == "bbcf":
        return _bbcf_move_list(char_key)
    if game == "ggacr":
        return _ggacr_move_list(char_key)
    if game == "cotw":
        return _cotw_move_list(char_key)
    if game == "third_strike":
        return _third_strike_move_list(char_key)
    if game == "usf4":
        return _usfiv_move_list(char_key)
    if game == "mk1":
        return _mk1_move_list(char_key)
    if game == "avtl":
        return _avtl_move_list(char_key)
    return []


def _character_display_name(game, char_key):
    chars = _character_list(game)
    return dict(chars).get(char_key, str(char_key).title())


def _normalize_search_text(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _search_matches(label, query):
    normalized_label = _normalize_search_text(label)
    normalized_query = _normalize_search_text(query)
    if not normalized_query:
        return True
    compact_label = re.sub(r"[^a-z0-9]+", "", normalized_label)
    compact_query = re.sub(r"[^a-z0-9]+", "", normalized_query)
    if compact_query and compact_query in compact_label:
        return True
    return all(term in normalized_label for term in normalized_query.split())


def _filter_character_choices(chars, query):
    if not str(query or "").strip():
        return list(chars)
    return [(char_key, display) for char_key, display in chars if _search_matches(display, query)]


def _filter_move_choices(moves, query):
    if not str(query or "").strip():
        return list(moves)
    return [(row, label) for row, label in moves if _search_matches(label, query)]


def _row_character_key(game, row, fallback=None):
    if fallback:
        return fallback
    for key_name in ("char_key", "character_key"):
        value = str((row or {}).get(key_name, "")).strip()
        if value:
            return value
    row_name = str((row or {}).get("char_name", "")).strip().lower()
    if not row_name:
        return None
    normalized_row_name = _normalize_search_text(row_name)
    for char_key, display in _character_list(game):
        if _normalize_search_text(display) == normalized_row_name:
            return char_key
        if _normalize_search_text(char_key) == normalized_row_name:
            return char_key
    return None


# Frame result helpers and compare buttons
class StatsCompareButton(discord.ui.Button):
    def __init__(self, char_key, owner_id, stat_keys=None):
        super().__init__(label="Compare", style=ACTION)
        self.char_key = char_key
        self.owner_id = owner_id
        self.stat_keys = tuple(stat_keys) if stat_keys else None

    async def callback(self, interaction: discord.Interaction):
        if getattr(self.view, "menu_locked", False) and interaction.user.id != getattr(self.view, "owner_id", None):
            await interaction.response.send_message(
                "Only the person who opened this menu can control it.",
                ephemeral=True,
            )
            return
        chars = _sf6_stats_character_list()
        if not chars:
            await interaction.response.send_message("No character stats loaded.", ephemeral=True)
            return
        await _open_menu(
            interaction,
            CharacterSelectView(
                "sf6",
                chars,
                _menu_locked_owner_id(self.view) or interaction.user.id,
                page=0,
                stats_mode=True,
                compare_char_key=self.char_key,
                compare_stat_keys=self.stat_keys,
                menu_locked=getattr(self.view, "menu_locked", False),
            ),
        )


class CompareFrameButton(discord.ui.Button):
    def __init__(self, game, row, owner_id, char_key=None):
        super().__init__(label="Compare", style=ACTION)
        self.game = game
        self.frame_row = row
        self.owner_id = owner_id
        self.char_key = char_key

    async def callback(self, interaction: discord.Interaction):
        if getattr(self.view, "menu_locked", False) and interaction.user.id != getattr(self.view, "owner_id", None):
            await interaction.response.send_message(
                "Only the person who opened this menu can control it.",
                ephemeral=True,
            )
            return
        char_key = _row_character_key(self.game, self.frame_row, self.char_key)
        if not char_key:
            await interaction.response.send_message("I could not find that character's move list.", ephemeral=True)
            return
        chars = _character_list(self.game)
        if not chars:
            await interaction.response.send_message("No characters found for this game.", ephemeral=True)
            return
        await _open_menu(
            interaction,
            CharacterSelectView(
                self.game,
                chars,
                _menu_locked_owner_id(self.view) or interaction.user.id,
                page=0,
                compare_row=self.frame_row,
                compare_char_key=char_key,
                menu_locked=getattr(self.view, "menu_locked", False),
            ),
        )


class FrameReturnMenuButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Return to Menu", style=ACTION, custom_id="frame_return_menu", row=1)

    async def callback(self, interaction: discord.Interaction):
        menu_owner = getattr(self.view, "owner_id", None) if getattr(self.view, "menu_locked", False) else interaction.user.id
        await _open_menu(interaction, MainMenuView(menu_owner))


class FrameBackToMovesButton(discord.ui.Button):
    def __init__(self, game, char_key):
        super().__init__(label="Back to Moves", style=ACTION, custom_id="frame_back_moves", row=1)
        self.game = game
        self.char_key = char_key

    async def callback(self, interaction: discord.Interaction):
        moves = _move_list(self.game, self.char_key)
        owner_id = _menu_locked_owner_id(self.view) or interaction.user.id
        await _open_menu(interaction, MoveSelectView(self.game, self.char_key, moves, owner_id, page=0, menu_locked=True))


def build_frame_result_view(
    game,
    row,
    owner_id=None,
    char_key=None,
    *,
    menu_locked=False,
    show_back_to_moves=None,
    source_message=None,
    prompt="",
):
    resolved_char_key = char_key or _row_character_key(game, row)
    if show_back_to_moves is None:
        show_back_to_moves = menu_locked
    return FrameResultView(
        game,
        resolved_char_key,
        row,
        owner_id,
        menu_locked=menu_locked,
        show_back_to_moves=show_back_to_moves,
        show_return_menu=True,
        source_message=source_message,
        prompt=prompt,
    )


async def prepare_cotw_frame_view(view):
    if getattr(view, "game", None) != "cotw":
        return
    from bubbot.frame_data.cotw_frame_data import build_image_attachment

    file, attachment_url = await build_image_attachment(view.row)
    if file and attachment_url:
        view.image_url_override = attachment_url
        view.cotw_image_bytes = file.fp.getvalue()
        view.cotw_image_filename = file.filename


async def send_frame_result_messages(
    channel,
    game,
    rows,
    *,
    owner_id=None,
    menu_locked=False,
    source_message=None,
    prompt="",
    client=None,
):
    from bubbot.features.failed_prompt_report import stamp_report_context_on_sent

    sent_ids = []
    for row in rows or []:
        view = build_frame_result_view(
            game,
            row,
            owner_id=owner_id,
            menu_locked=menu_locked,
            source_message=source_message,
            prompt=prompt,
        )
        await prepare_cotw_frame_view(view)
        sent = await channel.send(embed=view.build_embed(), view=view, files=view.initial_files())
        stamp_report_context_on_sent(view, sent, client=client)
        sent_ids.append(sent.id)
    return sent_ids


def attach_compare_button(view, game, row, owner_id=None, char_key=None):
    resolved_char_key = _row_character_key(game, row, char_key)
    if not resolved_char_key or not _move_list(game, resolved_char_key):
        return
    view.add_item(CompareFrameButton(game, row, owner_id, char_key=resolved_char_key))


# Main menu and per-game submenus
class MainGameSelect(discord.ui.Select):
    def __init__(self, owner_id):
        options = [
            discord.SelectOption(label=label, value=game_key)
            for game_key, label in MENU_GAMES
        ]
        super().__init__(
            placeholder="Choose a game",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="menu_game_select",
        )
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        await _show_menu(interaction, _game_menu_view(self.values[0], self.owner_id))


class MainMenuView(MenuView):
    def __init__(self, owner_id):
        super().__init__(owner_id=owner_id, menu_locked=True, timeout=None)
        self.add_item(MainGameSelect(owner_id))
        self.set_card(_main_menu_card())

    @discord.ui.button(label="Readme", style=ACTION, custom_id="menu_readme", row=1)
    async def readme_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _show_menu(interaction, ReadmeView(self.owner_id))

    @discord.ui.button(label="Glossary", style=ACTION, custom_id="menu_glossary", row=1)
    async def glossary_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _show_menu(interaction, GlossaryMenuView(self.owner_id))


class ReadmeView(MenuView):
    def __init__(self, owner_id):
        super().__init__(owner_id=owner_id, menu_locked=True, timeout=None)
        self.set_card(*readme_blocks())

    @discord.ui.button(label="Back", style=ACTION, custom_id="readme_back")
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _show_menu(interaction, MainMenuView(self.owner_id))


class GlossaryMenuView(MenuView):
    def __init__(self, owner_id, term=None):
        super().__init__(owner_id=owner_id, menu_locked=True, timeout=None)
        self.term = normalize_glossary_term(term)
        self.add_item(build_glossary_link_button(self.term))
        self.set_card(*embed_card_blocks(build_glossary_definition_embed(self.term)))

    @discord.ui.button(label="Search Term", style=ACTIVE, custom_id="glossary_search", row=1)
    async def search_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GlossarySearchModal(self.owner_id))

    @discord.ui.button(label="Back", style=ACTION, custom_id="glossary_back", row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _show_menu(interaction, MainMenuView(self.owner_id))


class GlossarySearchModal(discord.ui.Modal):
    def __init__(self, owner_id):
        super().__init__(title="Search Glossary")
        self.owner_id = owner_id
        self.query = discord.ui.TextInput(
            label="Glossary term",
            placeholder="Example: safe jump, option select, meaty",
            required=False,
            max_length=100,
        )
        self.add_item(self.query)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the person who opened this menu can search it.", ephemeral=True)
            return
        await _show_menu(interaction, GlossaryMenuView(self.owner_id, self.query.value))


class GameMenuView(MenuView):
    def __init__(self, game, owner_id):
        super().__init__(owner_id=owner_id, menu_locked=True, timeout=None)
        self.game = game
        self.set_card(_game_menu_card(game))

    @discord.ui.button(label="Frame Data", style=ACTIVE, custom_id="game_framedata", row=0)
    async def framedata_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _open_framedata_character_select(interaction, self.game, self.owner_id)

    @discord.ui.button(label="Combos", style=ACTION, custom_id="game_combos", row=0)
    async def combos_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _open_game_combos_menu(interaction, self.game, self.owner_id)

    @discord.ui.button(label="Quiz", style=ACTION, custom_id="game_quiz", row=0)
    async def quiz_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _open_quiz_difficulty(interaction, self.game, self.owner_id)

    @discord.ui.button(label="Back", style=ACTION, custom_id="game_back", row=0)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _show_menu(interaction, MainMenuView(self.owner_id))


class SF6GameMenuView(MenuView):
    def __init__(self, owner_id):
        super().__init__(owner_id=owner_id, menu_locked=True, timeout=None)
        self.game = "sf6"
        self.set_card(_game_menu_card(self.game))

    @discord.ui.button(label="Frame Data", style=ACTIVE, custom_id="sf6_game_framedata", row=0)
    async def framedata_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _open_framedata_character_select(interaction, self.game, self.owner_id)

    @discord.ui.button(label="Stats", style=ACTION, custom_id="sf6_game_stats", row=0)
    async def stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _open_sf6_stats_character_select(interaction, self.owner_id)

    @discord.ui.button(label="Combos", style=ACTION, custom_id="sf6_game_combos", row=0)
    async def combos_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _open_game_combos_menu(interaction, self.game, self.owner_id)

    @discord.ui.button(label="Quiz", style=ACTION, custom_id="sf6_game_quiz", row=0)
    async def quiz_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _open_quiz_difficulty(interaction, self.game, self.owner_id)

    @discord.ui.button(label="Back", style=ACTION, custom_id="sf6_game_back", row=0)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _show_menu(interaction, MainMenuView(self.owner_id))


# Character and move select views (paginated Discord selects)
class CharacterSelectView(MenuView):
    def __init__(
        self,
        game,
        chars,
        owner_id,
        page=0,
        compare_row=None,
        compare_char_key=None,
        stats_mode=False,
        compare_stat_keys=None,
        menu_locked=True,
        search_query=None,
    ):
        super().__init__(owner_id=owner_id, menu_locked=menu_locked, timeout=None)
        self.game = game
        self.chars = chars
        self.page = page
        self.compare_row = compare_row
        self.compare_char_key = compare_char_key
        self.stats_mode = stats_mode
        self.compare_stat_keys = compare_stat_keys
        self.search_query = search_query
        self._add_select()
        self._update_page_buttons()
        self.set_card(
            _character_select_card(
                game,
                page,
                self._page_count(),
                search_query=search_query,
                compare_row=compare_row,
                stats_mode=stats_mode,
                compare_stat_label=self._stats_compare_label(),
            )
        )

    def _page_count(self):
        return max(1, math.ceil(len(self.chars) / MENU_SELECT_LIMIT))

    def _update_page_buttons(self):
        single_page = self._page_count() == 1
        self.previous_button.disabled = single_page
        self.next_button.disabled = single_page
        if self.compare_row is not None:
            self.back_button.label = "Back to Moves"
        elif self.stats_mode and self.compare_char_key:
            # Opened privately from a stats card: there is nothing to go back to.
            self.back_button.label = "Close"

    def _add_select(self):
        start = self.page * MENU_SELECT_LIMIT
        page_chars = self.chars[start : start + MENU_SELECT_LIMIT]
        options = [
            discord.SelectOption(label=display, value=char_key)
            for char_key, display in page_chars
        ]
        select = CharacterSelect(
            self.game,
            self.chars,
            self.page,
            self.owner_id,
            options,
            compare_row=self.compare_row,
            compare_char_key=self.compare_char_key,
            stats_mode=self.stats_mode,
            compare_stat_keys=self.compare_stat_keys,
        )
        self.add_item(select)

    def _child_character_select_view(self, page):
        return CharacterSelectView(
            self.game,
            self.chars,
            self.owner_id,
            page=page,
            compare_row=self.compare_row,
            compare_char_key=self.compare_char_key,
            stats_mode=self.stats_mode,
            compare_stat_keys=self.compare_stat_keys,
            menu_locked=self.menu_locked,
            search_query=self.search_query,
        )

    @discord.ui.button(label="Previous", style=ACTION, row=4)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        max_page = self._page_count() - 1
        await _show_menu(interaction, self._child_character_select_view(self.page - 1 if self.page > 0 else max_page))

    @discord.ui.button(label="Next", style=ACTION, row=4)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        max_page = self._page_count() - 1
        await _show_menu(interaction, self._child_character_select_view(self.page + 1 if self.page < max_page else 0))

    def _stats_compare_label(self):
        if not self.stats_mode or not self.compare_char_key:
            return None
        from bubbot.frame_data.sf6_character_stats import display_character_name

        stats_row = FRAME_STATS.get(self.compare_char_key) or {}
        return display_character_name(self.compare_char_key, stats_row)

    @discord.ui.button(label="Search", style=ACTION, row=4)
    async def search_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            CharacterSearchModal(
                self.game,
                self.owner_id,
                compare_row=self.compare_row,
                compare_char_key=self.compare_char_key,
                stats_mode=self.stats_mode,
                compare_stat_keys=self.compare_stat_keys,
                menu_locked=self.menu_locked,
            )
        )

    @discord.ui.button(label="Back", style=ACTION, row=4)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.stats_mode and self.compare_char_key:
            await _show_menu(interaction, _notice_menu(self.owner_id, "Compare closed."))
            return
        if self.compare_row is not None:
            char_key = self.compare_char_key or self.chars[0][0]
            await _show_menu(
                interaction,
                MoveSelectView(
                    self.game,
                    char_key,
                    _move_list(self.game, char_key),
                    self.owner_id,
                    page=0,
                    compare_row=self.compare_row,
                    compare_char_key=self.compare_char_key,
                    menu_locked=self.menu_locked,
                ),
            )
            return
        await _show_menu(interaction, _game_menu_view(self.game, self.owner_id))


class CharacterSearchModal(discord.ui.Modal):
    def __init__(
        self,
        game,
        owner_id,
        compare_row=None,
        compare_char_key=None,
        stats_mode=False,
        compare_stat_keys=None,
        menu_locked=True,
    ):
        super().__init__(title="Search Characters")
        self.game = game
        self.owner_id = owner_id
        self.compare_row = compare_row
        self.compare_char_key = compare_char_key
        self.stats_mode = stats_mode
        self.compare_stat_keys = compare_stat_keys
        self.menu_locked = menu_locked
        self.query = discord.ui.TextInput(
            label="Character search",
            placeholder="Example: ryu, sol, happy chaos",
            required=False,
            max_length=80,
        )
        self.add_item(self.query)

    async def on_submit(self, interaction: discord.Interaction):
        if self.menu_locked and interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the person who opened this menu can search it.", ephemeral=True)
            return
        chars = _sf6_stats_character_list() if self.stats_mode else _character_list(self.game)
        filtered = _filter_character_choices(chars, str(self.query.value))
        if not filtered:
            await interaction.response.send_message("No characters matched that search.", ephemeral=True)
            return
        await _show_menu(
            interaction,
            CharacterSelectView(
                self.game,
                filtered,
                self.owner_id,
                page=0,
                compare_row=self.compare_row,
                compare_char_key=self.compare_char_key,
                stats_mode=self.stats_mode,
                compare_stat_keys=self.compare_stat_keys,
                menu_locked=self.menu_locked,
                search_query=str(self.query.value).strip(),
            ),
        )


class CharacterSelect(discord.ui.Select):
    def __init__(
        self,
        game,
        chars,
        page,
        owner_id,
        options,
        compare_row=None,
        compare_char_key=None,
        stats_mode=False,
        compare_stat_keys=None,
    ):
        self.game = game
        self.chars = chars
        self.page = page
        self.compare_row = compare_row
        self.compare_char_key = compare_char_key
        self.stats_mode = stats_mode
        self.compare_stat_keys = compare_stat_keys
        placeholder = "Select a character"
        super().__init__(placeholder=placeholder, options=options, custom_id=f"char_select:{game}:{page}:{owner_id}")

    async def callback(self, interaction: discord.Interaction):
        char_key = self.values[0]
        if self.stats_mode:
            if self.compare_char_key:
                await _post_sf6_stats_compare(
                    interaction,
                    self.compare_char_key,
                    char_key,
                    self.view.owner_id,
                    stat_keys=self.compare_stat_keys,
                    menu_locked=getattr(self.view, "menu_locked", True),
                )
            else:
                await _post_sf6_stats(interaction, char_key, self.view.owner_id)
            return
        moves = _move_list(self.game, char_key)
        if not moves:
            await interaction.response.send_message("No moves found for this character.", ephemeral=True)
            return
        await _show_menu(
            interaction,
            MoveSelectView(
                self.game,
                char_key,
                moves,
                self.view.owner_id,
                page=0,
                compare_row=self.compare_row,
                compare_char_key=self.compare_char_key,
                menu_locked=getattr(self.view, "menu_locked", True),
            ),
        )


class MoveSelectView(MenuView):
    def __init__(
        self,
        game,
        char_key,
        moves,
        owner_id,
        page=0,
        compare_row=None,
        compare_char_key=None,
        menu_locked=True,
        search_query=None,
    ):
        super().__init__(owner_id=owner_id, menu_locked=menu_locked, timeout=None)
        self.game = game
        self.char_key = char_key
        self.moves = moves
        self.page = page
        self.compare_row = compare_row
        self.compare_char_key = compare_char_key
        self.search_query = search_query
        self._add_select()
        self._update_page_buttons()
        self.set_card(
            _move_select_card(
                _character_display_name(game, char_key),
                page,
                self._page_count(),
                search_query=search_query,
                compare_row=compare_row,
            )
        )

    def _page_count(self):
        return max(1, math.ceil(len(self.moves) / MENU_SELECT_LIMIT))

    def _update_page_buttons(self):
        single_page = self._page_count() == 1
        self.previous_button.disabled = single_page
        self.next_button.disabled = single_page
        if self.compare_row is not None:
            self.back_button.label = "Back to Characters"

    def _add_select(self):
        start = self.page * MENU_SELECT_LIMIT
        page_moves = self.moves[start : start + MENU_SELECT_LIMIT]
        options = []
        for i, (row, label) in enumerate(page_moves):
            truncated = label[:100] if len(label) > 100 else label
            options.append(discord.SelectOption(label=truncated, value=f"{self.char_key}|{start + i}"))
        select = MoveSelect(
            self.game,
            self.char_key,
            self.moves,
            self.page,
            options,
            compare_row=self.compare_row,
            compare_char_key=self.compare_char_key,
        )
        self.add_item(select)

    def _child_move_select_view(self, page):
        return MoveSelectView(
            self.game,
            self.char_key,
            self.moves,
            self.owner_id,
            page=page,
            compare_row=self.compare_row,
            compare_char_key=self.compare_char_key,
            menu_locked=self.menu_locked,
            search_query=self.search_query,
        )

    @discord.ui.button(label="Previous", style=ACTION, row=4)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        max_page = self._page_count() - 1
        await _show_menu(interaction, self._child_move_select_view(self.page - 1 if self.page > 0 else max_page))

    @discord.ui.button(label="Next", style=ACTION, row=4)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        max_page = self._page_count() - 1
        await _show_menu(interaction, self._child_move_select_view(self.page + 1 if self.page < max_page else 0))

    @discord.ui.button(label="Search", style=ACTION, row=4)
    async def search_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            MoveSearchModal(
                self.game,
                self.char_key,
                self.owner_id,
                compare_row=self.compare_row,
                compare_char_key=self.compare_char_key,
                menu_locked=self.menu_locked,
            )
        )

    @discord.ui.button(label="Back", style=ACTION, row=4)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _show_menu(
            interaction,
            CharacterSelectView(
                self.game,
                _character_list(self.game),
                self.owner_id,
                page=0,
                compare_row=self.compare_row,
                compare_char_key=self.compare_char_key,
                menu_locked=self.menu_locked,
            ),
        )


class MoveSearchModal(discord.ui.Modal):
    def __init__(self, game, char_key, owner_id, compare_row=None, compare_char_key=None, menu_locked=True):
        super().__init__(title="Search Moves")
        self.game = game
        self.char_key = char_key
        self.owner_id = owner_id
        self.compare_row = compare_row
        self.compare_char_key = compare_char_key
        self.menu_locked = menu_locked
        self.query = discord.ui.TextInput(
            label="Move search",
            placeholder="Example: 5hp, fireball, volcanic viper",
            required=False,
            max_length=80,
        )
        self.add_item(self.query)

    async def on_submit(self, interaction: discord.Interaction):
        if self.menu_locked and interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the person who opened this menu can search it.", ephemeral=True)
            return
        moves = _move_list(self.game, self.char_key)
        filtered = _filter_move_choices(moves, str(self.query.value))
        if not filtered:
            await interaction.response.send_message("No moves matched that search.", ephemeral=True)
            return
        await _show_menu(
            interaction,
            MoveSelectView(
                self.game,
                self.char_key,
                filtered,
                self.owner_id,
                page=0,
                compare_row=self.compare_row,
                compare_char_key=self.compare_char_key,
                menu_locked=self.menu_locked,
                search_query=str(self.query.value).strip(),
            ),
        )


class MoveSelect(discord.ui.Select):
    def __init__(self, game, char_key, moves, page, options, compare_row=None, compare_char_key=None):
        self.game = game
        self.char_key = char_key
        self.moves = moves
        self.page = page
        self.compare_row = compare_row
        self.compare_char_key = compare_char_key
        placeholder = "Select a move"
        safe_char_key = re.sub(r"[^a-z0-9_.-]", "", str(char_key).lower())[:32]
        super().__init__(placeholder=placeholder, options=options, custom_id=f"move_select:{game}:{safe_char_key}:{page}")

    async def callback(self, interaction: discord.Interaction):
        selected_char, raw_idx = self.values[0].split("|", 1)
        if selected_char != self.char_key:
            await interaction.response.send_message("That move list is stale. Pick the character again.", ephemeral=True)
            return
        row, _label = self.moves[int(raw_idx)]
        # The card is posted below and the menu stays, so another move (or one to compare) is one pick away.
        from bubbot.features.failed_prompt_report import stamp_report_context_on_sent

        view = build_frame_result_view(self.game, row, self.view.owner_id, self.char_key, menu_locked=True)
        await prepare_cotw_frame_view(view)
        await interaction.response.send_message(embed=view.build_embed(), view=view, files=view.initial_files())
        try:
            stamp_report_context_on_sent(view, await interaction.original_response())
        except Exception:
            pass


async def _send_frame_result_message(channel, game, char_key, row, owner_id):
    from bubbot.features.failed_prompt_report import stamp_report_context_on_sent

    view = build_frame_result_view(game, row, owner_id, char_key, menu_locked=True)
    await prepare_cotw_frame_view(view)
    embed = view.build_embed()
    sent = await channel.send(embed=embed, view=view, files=view.initial_files())
    stamp_report_context_on_sent(view, sent)
    return sent


# SF6 stats compare flow
class StatsShowFramedataButton(discord.ui.Button):
    def __init__(self, char_key, owner_id):
        super().__init__(label="Show Framedata", style=ACTION)
        self.char_key = char_key
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        if getattr(self.view, "menu_locked", False) and interaction.user.id != getattr(self.view, "owner_id", None):
            await interaction.response.send_message(
                "Only the person who opened this menu can control it.",
                ephemeral=True,
            )
            return
        moves = _move_list("sf6", self.char_key)
        if not moves:
            await interaction.response.send_message("No moves found for this character.", ephemeral=True)
            return
        await _open_menu(
            interaction,
            MoveSelectView(
                "sf6",
                self.char_key,
                moves,
                getattr(self.view, "owner_id", self.owner_id),
                page=0,
                menu_locked=getattr(self.view, "menu_locked", True),
            ),
        )


class StatsResultView(OwnedView):
    def __init__(
        self,
        char_key,
        owner_id,
        stat_keys=None,
        compare_char_key=None,
        *,
        menu_locked=False,
        source_message=None,
        prompt="",
    ):
        super().__init__(owner_id=owner_id, menu_locked=menu_locked, timeout=None)
        self.char_key = char_key
        self.stat_keys = tuple(stat_keys) if stat_keys else None
        self.compare_char_key = compare_char_key
        self.add_item(StatsCompareButton(char_key, owner_id, stat_keys=self.stat_keys))
        self.add_item(StatsShowFramedataButton(char_key, owner_id))
        self.add_item(FrameReturnMenuButton())
        if menu_locked:
            self.add_item(StatsBackToCharactersButton(char_key, owner_id))
        from bubbot.features.failed_prompt_report import attach_frame_report_button

        attach_frame_report_button(
            self,
            game="sf6",
            row={"char_key": char_key, "char_name": char_key, "moveName": "Character Stats"},
            source_message=source_message,
            prompt=prompt,
            failure_reason="stats_response",
        )


class StatsBackToCharactersButton(discord.ui.Button):
    def __init__(self, char_key, owner_id):
        super().__init__(label="Back to Characters", style=ACTION, custom_id="stats_back_characters", row=1)
        self.char_key = char_key
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        chars = _sf6_stats_character_list()
        if not chars:
            await interaction.response.send_message("No character stats loaded.", ephemeral=True)
            return
        await _open_menu(interaction, CharacterSelectView("sf6", chars, self.owner_id, page=0, stats_mode=True, menu_locked=True))


def _preferred_frame_image_url(game, row):
    game_key = str(game or "").strip().lower()
    if game_key == "ggst":
        from bubbot.frame_data.ggst_frame_data import get_hitbox_links

        links = get_hitbox_links(row)
        return links[0] if links else ""
    if game_key == "bbcf":
        from bubbot.frame_data.bbcf_frame_data import get_hitbox_links

        links = get_hitbox_links(row)
        return links[0] if links else ""
    if game_key == "ggacr":
        from bubbot.frame_data.ggacr_frame_data import get_hitbox_links

        links = get_hitbox_links(row)
        return links[0] if links else ""
    if game_key == "third_strike":
        from bubbot.frame_data.third_strike_frame_data import get_hitbox_links

        links = get_hitbox_links(row)
        return links[0] if links else ""
    if game_key == "avtl":
        from bubbot.frame_data.avtl_frame_data import get_hitbox_links

        links = get_hitbox_links(row)
        return links[0] if links else ""
    return ""


# FrameResultView: per-game notes, hitbox, gif buttons
class FrameResultView(OwnedView):
    def __init__(
        self,
        game,
        char_key,
        row,
        owner_id,
        *,
        menu_locked=False,
        show_back_to_moves=False,
        show_return_menu=True,
        source_message=None,
        prompt="",
    ):
        super().__init__(owner_id=owner_id, menu_locked=menu_locked, timeout=None)
        self.game = game
        self.char_key = char_key
        self.row = row
        self.source_message = source_message
        self.prompt = prompt
        self.show_notes = False
        self.show_stats = False
        if game == "sf6":
            from bubbot.frame_data.frame_output import SF6NotesButton, SF6ShowStatsButton, get_sf6_hitbox_image_urls
            from bubbot.utils.sfstats_style import AllHitboxImagesButton
            self.character_stats = FRAME_STATS.get(char_key) or {}
            self.stats_button = SF6ShowStatsButton(disabled=not self.character_stats)
            self.notes_button = SF6NotesButton(row)
            self.add_item(self.stats_button)
            self.add_item(self.notes_button)
            self.all_hitbox_images_button = AllHitboxImagesButton(get_sf6_hitbox_image_urls(row))
            if len(self.all_hitbox_images_button.hitbox_links) > 1:
                self.add_item(self.all_hitbox_images_button)
        elif game == "ggst":
            from bubbot.frame_data.ggst_frame_data import GGSTAllHitboxImagesButton, GGSTNotesButton
            self.all_hitbox_images_button = GGSTAllHitboxImagesButton(row)
            if len(self.all_hitbox_images_button.hitbox_links) > 1:
                self.add_item(self.all_hitbox_images_button)
            self.notes_button = GGSTNotesButton(row)
            self.add_item(self.notes_button)
        elif game == "sfv":
            from bubbot.frame_data.sfv_frame_data import SFVNotesButton
            self.notes_button = SFVNotesButton(row)
            self.add_item(self.notes_button)
        elif game == "tuco":
            from bubbot.frame_data.tuco_frame_data import TUCONotesButton
            self.notes_button = TUCONotesButton(row)
            self.add_item(self.notes_button)
        elif game == "bbcf":
            from bubbot.frame_data.bbcf_frame_data import BBCFAllHitboxImagesButton, BBCFNotesButton
            self.all_hitbox_images_button = BBCFAllHitboxImagesButton(row)
            if len(self.all_hitbox_images_button.hitbox_links) > 1:
                self.add_item(self.all_hitbox_images_button)
            self.notes_button = BBCFNotesButton(row)
            self.add_item(self.notes_button)
        elif game == "ggacr":
            from bubbot.frame_data.ggacr_frame_data import GGACRAllHitboxImagesButton, GGACRNotesButton
            self.all_hitbox_images_button = GGACRAllHitboxImagesButton(row)
            if len(self.all_hitbox_images_button.hitbox_links) > 1:
                self.add_item(self.all_hitbox_images_button)
            self.notes_button = GGACRNotesButton(row)
            self.add_item(self.notes_button)
        elif game == "third_strike":
            from bubbot.frame_data.third_strike_frame_data import ThirdStrikeAllHitboxImagesButton, ThirdStrikeNotesButton
            self.all_hitbox_images_button = ThirdStrikeAllHitboxImagesButton(row)
            if len(self.all_hitbox_images_button.hitbox_links) > 1:
                self.add_item(self.all_hitbox_images_button)
            self.notes_button = ThirdStrikeNotesButton(row)
            self.add_item(self.notes_button)
        elif game == "usf4":
            from bubbot.frame_data.usfiv_frame_data import USFIVNotesButton

            self.notes_button = USFIVNotesButton(row)
            self.add_item(self.notes_button)
        elif game == "mk1":
            from bubbot.frame_data.mk1_frame_data import MK1NotesButton
            self.notes_button = MK1NotesButton(row)
            self.add_item(self.notes_button)
        elif game == "avtl":
            from bubbot.frame_data.avtl_frame_data import AVTLAllHitboxImagesButton, AVTLNotesButton
            self.all_hitbox_images_button = AVTLAllHitboxImagesButton(row)
            if len(self.all_hitbox_images_button.hitbox_links) > 1:
                self.add_item(self.all_hitbox_images_button)
            self.notes_button = AVTLNotesButton(row)
            self.add_item(self.notes_button)
        else:
            from bubbot.frame_data.cotw_frame_data import COTWNotesButton
            self.image_url_override = ""
            self.cotw_image_bytes = None
            self.cotw_image_filename = None
            self.notes_button = COTWNotesButton(row)
            self.add_item(self.notes_button)
        attach_compare_button(self, game, row, owner_id=owner_id, char_key=char_key)
        if show_return_menu:
            self.add_item(FrameReturnMenuButton())
        if show_back_to_moves:
            self.add_item(FrameBackToMovesButton(game, char_key))
        from bubbot.features.failed_prompt_report import attach_frame_report_button

        attach_frame_report_button(
            self,
            game=game,
            row=row,
            source_message=source_message,
            prompt=prompt,
        )

    def build_embed(self):
        from bubbot.utils.embed_source_utils import apply_game_source_footer
        if self.game == "sf6":
            if getattr(self, "show_stats", False) and getattr(self, "character_stats", None):
                from bubbot.frame_data.sf6_character_stats import build_character_stats_embed

                return apply_game_source_footer(
                    build_character_stats_embed(self.char_key, self.character_stats),
                    self.game,
                )
            # SuperCombo's hitbox image only (frame_output.get_sf6_move_image_url), never a GIF.
            return apply_game_source_footer(
                build_sf6_frame_embed(self.row, show_notes=getattr(self, "show_notes", False)),
                self.game,
            )
        if self.game == "ggst":
            from bubbot.frame_data.ggst_frame_data import build_frame_embed
        elif self.game == "sfv":
            from bubbot.frame_data.sfv_frame_data import build_frame_embed
        elif self.game == "tuco":
            from bubbot.frame_data.tuco_frame_data import build_frame_embed
        elif self.game == "bbcf":
            from bubbot.frame_data.bbcf_frame_data import build_frame_embed
        elif self.game == "ggacr":
            from bubbot.frame_data.ggacr_frame_data import build_frame_embed
        elif self.game == "third_strike":
            from bubbot.frame_data.third_strike_frame_data import build_frame_embed
        elif self.game == "usf4":
            from bubbot.frame_data.usfiv_frame_data import build_frame_embed
        elif self.game == "mk1":
            from bubbot.frame_data.mk1_frame_data import build_frame_embed
        elif self.game == "avtl":
            from bubbot.frame_data.avtl_frame_data import build_frame_embed
        else:
            from bubbot.frame_data.cotw_frame_data import build_frame_embed
        embed = build_frame_embed(self.row, show_notes=getattr(self, "show_notes", False))
        preferred_image_url = _preferred_frame_image_url(self.game, self.row)
        if preferred_image_url:
            embed.set_image(url=preferred_image_url)
        if self.game == "cotw" and getattr(self, "image_url_override", ""):
            embed.set_image(url=self.image_url_override)
        return apply_game_source_footer(embed, self.game)

    def initial_files(self):
        return self.active_files()

    def active_files(self):
        from bubbot.utils.embed_source_utils import source_icon_files

        files = source_icon_files(self.game, kind="frame")
        if self.game == "cotw" and getattr(self, "cotw_image_bytes", None) and getattr(self, "cotw_image_filename", None):
            files.append(discord.File(io.BytesIO(self.cotw_image_bytes), filename=self.cotw_image_filename))
        return files


# Quiz difficulty picker (launches quiz_module.start_quiz)
class QuizDifficultyView(MenuView):
    def __init__(self, game, owner_id):
        super().__init__(owner_id=owner_id, menu_locked=True, timeout=None)
        self.game = game
        self.set_card(card_text(f"{_game_label(game)} · Quiz", "Pick a difficulty."))

    @discord.ui.button(label="Easy", style=ACTION, custom_id="quiz_easy")
    async def easy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._start_quiz(interaction, "easy")

    @discord.ui.button(label="Medium", style=ACTION, custom_id="quiz_medium")
    async def medium_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._start_quiz(interaction, "medium")

    @discord.ui.button(label="Hard", style=ACTION, custom_id="quiz_hard")
    async def hard_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._start_quiz(interaction, "hard")

    @discord.ui.button(label="Back", style=ACTION, custom_id="quiz_back")
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _show_menu(interaction, _game_menu_view(self.game, self.owner_id))

    async def _start_quiz(self, interaction, difficulty):
        if quiz_module is None:
            await interaction.response.send_message("Quiz system is not available.", ephemeral=True)
            return
        await _show_menu(
            interaction,
            _notice_menu(self.owner_id, f"A **{difficulty}** quiz is starting below. Good luck!", title=f"{_game_label(self.game)} · Quiz"),
        )
        fake_message = QuizFakeMessage(interaction)
        await quiz_module.start_quiz(fake_message, mode=difficulty, game=self.game)


class BackToGameMenuView(MenuView):
    def __init__(self, game, owner_id, card):
        super().__init__(owner_id=owner_id, menu_locked=True, timeout=None)
        self.game = game
        self.set_card(card)

    @discord.ui.button(label="Back", style=ACTION, custom_id="combos_back")
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _show_menu(interaction, _game_menu_view(self.game, self.owner_id))


# Combo section and subsection navigation
class ComboCharacterSelectView(MenuView):
    def __init__(self, game, chars, owner_id, page=0):
        super().__init__(owner_id=owner_id, menu_locked=True, timeout=None)
        self.game = game
        self.chars = chars
        self.page = page
        self._add_select()
        self._update_page_buttons()
        self.set_card(_combo_character_select_card(game, page, self._page_count()))

    def _page_count(self):
        return max(1, math.ceil(len(self.chars) / MENU_SELECT_LIMIT))

    def _update_page_buttons(self):
        single_page = self._page_count() == 1
        self.previous_button.disabled = single_page
        self.next_button.disabled = single_page

    def _add_select(self):
        start = self.page * MENU_SELECT_LIMIT
        options = [
            discord.SelectOption(label=display[:100], value=char_key)
            for char_key, display in self.chars[start : start + MENU_SELECT_LIMIT]
        ]
        self.add_item(ComboCharacterSelect(self.game, self.chars, self.page, options))

    @discord.ui.button(label="Previous", style=ACTION, row=4)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        max_page = self._page_count() - 1
        new_page = self.page - 1 if self.page > 0 else max_page
        await _show_menu(interaction, ComboCharacterSelectView(self.game, self.chars, self.owner_id, page=new_page))

    @discord.ui.button(label="Next", style=ACTION, row=4)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        max_page = self._page_count() - 1
        new_page = self.page + 1 if self.page < max_page else 0
        await _show_menu(interaction, ComboCharacterSelectView(self.game, self.chars, self.owner_id, page=new_page))

    @discord.ui.button(label="Back", style=ACTION, row=4)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _show_menu(interaction, _game_menu_view(self.game, self.owner_id))


class ComboCharacterSelect(discord.ui.Select):
    def __init__(self, game, chars, page, options):
        self.game = game
        self.chars = chars
        self.page = page
        super().__init__(placeholder="Select a character", options=options, custom_id=f"combo_char_select:{game}:{page}")

    async def callback(self, interaction: discord.Interaction):
        from bubbot.frame_data import combo_data

        char_key = self.values[0]
        sections = combo_data.combo_sections(self.game, char_key)
        if not sections:
            await interaction.response.send_message("No combos found for that character.", ephemeral=True)
            return
        await _show_menu(interaction, ComboSectionView(self.game, char_key, sections, self.view.owner_id, page=0))


class ComboSectionView(MenuView):
    """Buttons (one per SuperCombo page heading) that open that heading's combo list."""

    SECTIONS_PER_PAGE = 20

    def __init__(self, game, char_key, sections, owner_id, page=0, back_to="character_select"):
        super().__init__(owner_id=owner_id, menu_locked=True, timeout=None)
        self.game = game
        self.char_key = char_key
        self.sections = list(sections or [])
        self.page = page
        self.back_to = back_to
        self._build()
        self.set_card(_combo_section_select_card(game, char_key, page, self.page_count(self.sections)))

    @classmethod
    def page_count(cls, sections):
        return max(1, math.ceil(len(sections) / cls.SECTIONS_PER_PAGE))

    def _build(self):
        page_count = self.page_count(self.sections)
        start = self.page * self.SECTIONS_PER_PAGE
        page_sections = self.sections[start : start + self.SECTIONS_PER_PAGE]
        for offset, (section_label, count) in enumerate(page_sections):
            label = f"{section_label} ({count})"
            self.add_item(
                ComboSectionButton(
                    section_label,
                    label[:80],
                    row=offset // 5,
                )
            )
        if page_count > 1:
            self.add_item(ComboSectionPageButton(self.game, self.char_key, self.sections, "prev", self.page))
            self.add_item(ComboSectionPageButton(self.game, self.char_key, self.sections, "next", self.page))
        self.add_item(ComboSectionBackButton())


async def _post_combo_list(interaction, view, rows, *, section, subsection=None):
    """A section's combos are a result: posted below the menu, which stays."""
    from bubbot.frame_data import combo_data

    pages = combo_data.build_combo_pages(view.game, view.char_key, rows, section=section, subsection=subsection)
    list_view = combo_data.ComboListView(
        view.game,
        view.char_key,
        pages,
        view.owner_id,
        menu_locked=True,
        section=section,
        subsection=subsection,
        back_to=view.back_to,
    )
    from bubbot.features.failed_prompt_report import stamp_report_context_on_sent

    await interaction.response.send_message(embed=pages[0], view=list_view, files=list_view.message_attachments())
    try:
        stamp_report_context_on_sent(list_view, await interaction.original_response())
    except Exception:
        pass


class ComboSectionButton(discord.ui.Button):
    def __init__(self, section_label, label, row):
        super().__init__(label=label, style=ACTION, row=row)
        self.section_label = section_label

    async def callback(self, interaction: discord.Interaction):
        from bubbot.frame_data import combo_data

        view = self.view
        if combo_data.section_needs_subsection_menu(view.game, view.char_key, self.section_label):
            subsections = combo_data.combo_subsections(view.game, view.char_key, self.section_label)
            await _show_menu(
                interaction,
                ComboSubsectionView(
                    view.game,
                    view.char_key,
                    self.section_label,
                    subsections,
                    view.owner_id,
                    page=0,
                    back_to=view.back_to,
                ),
            )
            return
        rows = combo_data.rows_for_section(view.game, view.char_key, self.section_label)
        await _post_combo_list(interaction, view, rows, section=self.section_label)


class ComboSectionPageButton(discord.ui.Button):
    def __init__(self, game, char_key, sections, direction, page):
        page_count = ComboSectionView.page_count(sections)
        if direction == "prev":
            target = page - 1 if page > 0 else page_count - 1
            label = "Previous"
        else:
            target = page + 1 if page < page_count - 1 else 0
            label = "Next"
        super().__init__(label=label, style=ACTION, row=4)
        self.game = game
        self.char_key = char_key
        self.sections = sections
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        await _show_menu(
            interaction,
            ComboSectionView(
                self.game,
                self.char_key,
                self.sections,
                self.view.owner_id,
                page=self.target,
                back_to=self.view.back_to,
            ),
        )


class ComboSectionBackButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Back", style=ACTION, row=4)

    async def callback(self, interaction: discord.Interaction):
        from bubbot.frame_data import combo_data

        view = self.view
        if view.back_to == "game_menu":
            await _edit_to_game_menu(interaction, view.game, view.owner_id)
            return
        chars = combo_data.combo_character_list(view.game)
        await _show_menu(interaction, ComboCharacterSelectView(view.game, chars, view.owner_id, page=0))


class ComboSubsectionView(MenuView):
    """Sub-heading buttons when a section has more than 10 combos."""

    SUBSECTIONS_PER_PAGE = 20

    def __init__(self, game, char_key, section_label, subsections, owner_id, page=0, back_to="character_select"):
        super().__init__(owner_id=owner_id, menu_locked=True, timeout=None)
        self.game = game
        self.char_key = char_key
        self.section_label = section_label
        self.subsections = list(subsections or [])
        self.page = page
        self.back_to = back_to
        self._build()
        self.set_card(_combo_subsection_select_card(game, char_key, section_label, page, self.page_count(self.subsections)))

    @classmethod
    def page_count(cls, subsections):
        return max(1, math.ceil(len(subsections) / cls.SUBSECTIONS_PER_PAGE))

    def _build(self):
        page_count = self.page_count(self.subsections)
        start = self.page * self.SUBSECTIONS_PER_PAGE
        page_subsections = self.subsections[start : start + self.SUBSECTIONS_PER_PAGE]
        for offset, (subsection_label, count) in enumerate(page_subsections):
            label = f"{subsection_label} ({count})"
            self.add_item(
                ComboSubsectionButton(
                    self.section_label,
                    subsection_label,
                    label[:80],
                    row=offset // 5,
                )
            )
        if page_count > 1:
            self.add_item(
                ComboSubsectionPageButton(
                    self.game,
                    self.char_key,
                    self.section_label,
                    self.subsections,
                    "prev",
                    self.page,
                )
            )
            self.add_item(
                ComboSubsectionPageButton(
                    self.game,
                    self.char_key,
                    self.section_label,
                    self.subsections,
                    "next",
                    self.page,
                )
            )
        self.add_item(ComboSubsectionBackButton())


class ComboSubsectionButton(discord.ui.Button):
    def __init__(self, section_label, subsection_label, label, row):
        super().__init__(label=label, style=ACTION, row=row)
        self.section_label = section_label
        self.subsection_label = subsection_label

    async def callback(self, interaction: discord.Interaction):
        from bubbot.frame_data import combo_data

        view = self.view
        rows = combo_data.rows_for_subsection(view.game, view.char_key, self.section_label, self.subsection_label)
        await _post_combo_list(interaction, view, rows, section=self.section_label, subsection=self.subsection_label)


class ComboSubsectionPageButton(discord.ui.Button):
    def __init__(self, game, char_key, section_label, subsections, direction, page):
        page_count = ComboSubsectionView.page_count(subsections)
        if direction == "prev":
            target = page - 1 if page > 0 else page_count - 1
            label = "Previous"
        else:
            target = page + 1 if page < page_count - 1 else 0
            label = "Next"
        super().__init__(label=label, style=ACTION, row=4)
        self.game = game
        self.char_key = char_key
        self.section_label = section_label
        self.subsections = subsections
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        await _show_menu(
            interaction,
            ComboSubsectionView(
                self.game,
                self.char_key,
                self.section_label,
                self.subsections,
                self.view.owner_id,
                page=self.target,
                back_to=self.view.back_to,
            ),
        )


class ComboSubsectionBackButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Back", style=ACTION, row=4)

    async def callback(self, interaction: discord.Interaction):
        from bubbot.frame_data import combo_data

        view = self.view
        sections = combo_data.combo_sections(view.game, view.char_key)
        await _show_menu(interaction, ComboSectionView(view.game, view.char_key, sections, view.owner_id, page=0, back_to=view.back_to))


class QuizFakeMessage:
    def __init__(self, interaction):
        self.channel = interaction.channel
        self.author = interaction.user
        self.content = ""
        self.id = interaction.id
        self.reference = None
        self.embeds = []
        self.attachments = []

    async def reply(self, content=None, **kwargs):
        return await self.channel.send(content, **kwargs)


# Menu cards (SF Stats style) and public send entrypoints
def _main_menu_card():
    return card_text(
        "SF Stats",
        "Frame data, hitboxes, combos and quizzes for 11 fighting games.",
        footer=["Pick a game below", "Readme for a quick guide", "player stats: `/help`"],
    )


def readme_blocks():
    """The /readme guide as SF Stats card blocks, laid out like SF Stats' own /help."""
    return [
        card_text(
            "SF Stats",
            "Street Fighter 6 player stats from sfstats.app, plus frame data, hitboxes, combos and quizzes for 11 fighting games.",
            footer=["`/help` has the full player stats guide", "menus answer only the person who opened them"],
        ),
        DIVIDER,
        "**Player stats**\n"
        "`/profile` (also `/player`, `/cfn`) · rank, matchups, rating, habits and replays · `/me` · your own card\n"
        "`/vs` · `/leaderboard` · `/server-leaderboard` · `/character` · `/replay` · `/queue` · `/alias`\n"
        "`/link` · say which CFN profile is you in this server · right-click a member → **Apps** → *SF Stats profile* or *Compare with me*\n"
        "Or just ask, like `@SF Stats my rank` · server admins: `/setup` for rank-up and replay posts",
        "**Frame data**\n"
        "Mention SF Stats, then a character and move: `@SF Stats ryu 5hp` · `@SF Stats ky far slash` · "
        "`@SF Stats amane 5b` · `@SF Stats ashrah heavens palm`\n"
        "Adding `framedata` is optional. Plain-language lookups are still a work in progress outside SF6: "
        "use the menu or slash commands for installs, stances and other esoteric moves",
        "**Game tags**\n"
        "Add `sfv`, `3s`, `ggst`, `ggacr`, `bbcf`, `cotw`, `2xko`, `mk1` or `avtl` when a name could mean more than one game, "
        "like `@SF Stats 3s ken hadouken`. Bare `guilty gear` means Strive; `+r`, `plus r`, `gg +r`, `accent core`, `acpr` "
        "or `guilty gear accent core` mean Accent Core Plus R",
        "**Images, notes and hitboxes**\n"
        "Ask for `hitbox`, `image`, `gif` or `notes`. Cards show hitbox images when there are any; "
        "Show Notes toggles notes, and some games add Show All Images for multi-image moves",
        "**Menus and slash commands**\n"
        "`/sfstatsmenu` · the frame data menu · `/sf6` · `/sf6-stats` · `/sf6-combos` · `/sfv` · `/usf4` · `/ggst` · "
        "`/ggacr` · `/2xko` · `/bbcf` · `/cotw` · `/third-strike` · `/mk1` · `/mk1-combos` · `/avtl` · `/glossary`",
        "**Glossary, quiz and compare**\n"
        "`/glossary safe jump` or the menu's Glossary button · definitions from Infil's Fighting Game Glossary\n"
        "Every game menu has Combos (SF6 and MK1) and a Quiz; SF6 also has Stats. Compare on a card puts another move from the same game next to it",
        card_text(None, footer="Something wrong? Use Report Issue on the card, with the exact query you used"),
    ]


def _game_menu_card(game):
    features = "Frame data, character stats, combos and a quiz." if game == "sf6" else "Frame data, combos and a quiz."
    return card_text(_game_label(game), features)


def _row_move_label(row):
    move_name = str((row or {}).get("moveName", "")).strip()
    num_cmd = str((row or {}).get("numCmd", "")).strip()
    if move_name and num_cmd:
        return f"{move_name} ({num_cmd})"
    return move_name or num_cmd or "selected move"


def _page_footer(page, total_pages, search_query=None):
    """"Page 2 of 3 · Search: ken", leaving out what doesn't apply."""
    search = str(search_query or "").replace("`", "'").strip()
    return [
        f"Page {page + 1} of {total_pages}" if total_pages and total_pages > 1 else None,
        f"Search: `{search}`" if search else None,
    ]


def _character_select_card(
    game,
    page,
    total_pages,
    search_query=None,
    compare_row=None,
    stats_mode=False,
    compare_stat_label=None,
):
    label = _game_label(game)
    if stats_mode and compare_stat_label:
        title, line = f"{label} · Compare stats", f"Pick a character to compare with **{compare_stat_label}**."
    elif stats_mode:
        title, line = f"{label} · Character stats", "Pick a character to see their stat sheet."
    elif compare_row:
        title, line = f"{label} · Compare", f"Pick a character, then a move to compare with **{_row_move_label(compare_row)}**."
    else:
        title, line = f"{label} · Frame data", "Pick a character."
    return card_text(title, line, footer=_page_footer(page, total_pages, search_query))


def _move_select_card(char_display, page, total_pages, search_query=None, compare_row=None):
    line = f"Pick a move to compare with **{_row_move_label(compare_row)}**." if compare_row else "Pick a move: its card is posted below."
    return card_text(char_display, line, footer=_page_footer(page, total_pages, search_query))


def _combo_character_select_card(game, page, total_pages):
    from bubbot.frame_data import combo_data

    return card_text(
        f"{combo_data.game_label(game)} · Combos",
        "Pick a character to see their combo routes.",
        footer=_page_footer(page, total_pages),
    )


def _combo_section_select_card(game, char_key, page, total_pages):
    from bubbot.frame_data import combo_data

    return card_text(
        f"{combo_data.display_char_name(game, char_key)} · {combo_data.game_label(game)} combos",
        "Pick a section: its combos are posted below.",
        footer=_page_footer(page, total_pages),
    )


def _combo_subsection_select_card(game, char_key, section_label, page, total_pages):
    from bubbot.frame_data import combo_data

    return card_text(
        f"{combo_data.display_char_name(game, char_key)} · {section_label}",
        "Pick a sub-section.",
        footer=_page_footer(page, total_pages),
    )


async def send_main_menu(destination, owner_id=None):
    await destination.send(view=MainMenuView(owner_id))


async def send_game_menu(destination, game, owner_id=None):
    await destination.send(view=_game_menu_view(str(game or "").strip().lower(), owner_id))


async def send_character_moves_menu(destination, game, char_key, owner_id=None):
    moves = _move_list(game, char_key)
    if not moves:
        await destination.send("No moves found for that character.")
        return False
    await destination.send(view=MoveSelectView(game, char_key, moves, owner_id, page=0, menu_locked=True))
    return True
