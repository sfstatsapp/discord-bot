"""SF6 frame embed formatting and Discord result views (build_frame_result_view wiring)."""

import os
import re

import discord

from bubbot.utils.discord_formatting import (
    add_embed_field as shared_add_embed_field,
    add_long_embed_field as shared_add_long_embed_field,
    clean_value as shared_clean_value,
    is_missing_value,
    truncate_value as shared_truncate_value,
)
from bubbot.utils.image_cache_utils import import_cache_module, merge_nested_url_cache
from bubbot.utils.row_utils import unique_rows
from bubbot.frame_data.sf6_character_stats import (
    build_character_stats_embed,
    character_key_from_row,
)
from bubbot.runtime.config import FRAME_DATA_ERROR_CONTACT_TEXT, MISSING_HITBOX_GIF_TEXT
from bubbot.utils.text_utils import compact_key
from bubbot.frame_data.gif_lookup import DISCORD_ATTACHMENT_LIMIT
from bubbot.utils.sfstats_style import ACTION, ACTIVE, BRAND, toggle_style
from bubbot.utils.emoji_text import strip_embed_emojis

FRAME_STATS = {}
normalize_char_name = None

is_missing_attack_range_value = None
truncate_message = None
send_deleted_message_failsafe = None
get_frame_row_gif_links = None
get_existing_local_gif_asset_paths = None
is_deleted_message_reference_error = None
RANGE_SCROLLS_MISSING_TEXT = "the range of that move is not on the supercombo scrolls"
RANGE_SCROLLS_MISSING_TABLE_TEXT = "missing"
# SuperCombo hitbox images by (character, numCmd), in SuperCombo's order, built by scripts/build_move_image_caches.py.
SF6_HITBOX_IMAGE_URLS = {}
SF6_MOVE_IMAGES_MODULE = "bubbot.data.sf6_move_images"


# Runtime dependency injection


def configure(**deps):
    globals().update(deps)


def load_move_image_urls(module_name=SF6_MOVE_IMAGES_MODULE):
    image_module = import_cache_module(module_name, "sf6-images")
    if not image_module:
        return False
    data = getattr(image_module, "SF6_HITBOX_IMAGE_URLS", {})

    loaded = merge_nested_url_cache(
        SF6_HITBOX_IMAGE_URLS,
        data,
        char_key_fn=normalize_image_key,
        move_key_fn=normalize_image_key,
        url_fn=_url_list,
    )
    print(f"[sf6-images] loaded hitbox images for {loaded} moves", flush=True)
    return True


def get_attack_range_details(row):
    raw_value = str(row.get("range", "")).strip()
    if is_missing_attack_range_value(raw_value):
        return "", False
    return raw_value, True


def format_attack_range_for_table(row):
    range_value, has_numeric_range = get_attack_range_details(row)
    if has_numeric_range:
        return range_value
    return RANGE_SCROLLS_MISSING_TABLE_TEXT

def format_frame_data(row):
    """Format a frame data row into readable text."""
    atk_range = format_attack_range_for_table(row)
    return (
        f"Move: {row['moveName']} ({row['numCmd']})\n"
        f"Startup: {row['startup']}f | Active: {row['active']}f | Recovery: {row['recovery']}f\n"
        f"Range: {atk_range}\n"
        f"On Hit: {row['onHit']} | On Block: {row['onBlock']}\n"
        f"Damage: {row['dmg']} | Attack Type: {format_guard_value(row['atkLvl'])}\n"
        f"Notes: {row.get('extraInfo', '')}"
    )


def normalize_image_key(value):
    return compact_key(value)


def _url_list(links):
    """A cached move's images as a list (older caches held one URL per move)."""
    return [str(link).strip() for link in ([links] if isinstance(links, str) else links or []) if str(link).strip()]


def get_sf6_hitbox_image_urls(row):
    """Every SuperCombo hitbox image for the move, in SuperCombo's order; empty when it has none."""
    char_key = normalize_image_key(row.get("char_name", ""))
    num_cmd_key = normalize_image_key(row.get("numCmd", ""))
    return list(SF6_HITBOX_IMAGE_URLS.get((char_key, num_cmd_key), ()))


def get_sf6_move_image_url(row):
    """The move's first SuperCombo hitbox image, or "" when SuperCombo has none for it: never another image."""
    urls = get_sf6_hitbox_image_urls(row)
    return urls[0] if urls else ""


load_move_image_urls()


# Partial property-only text replies


def format_property_only_lines(lines, limit=1800):
    cleaned_lines = [str(line).strip() for line in (lines or []) if str(line).strip()]
    if not cleaned_lines:
        return ""
    return truncate_message("\n".join(cleaned_lines), limit=limit)


def format_startup_only_reply(rows):
    lines = []
    for row in iter_unique_frame_rows(rows):
        startup_raw = str(row.get("startup", "-")).replace("*", ",").strip()
        startup = startup_raw if startup_raw else "-"
        startup_suffix = "f" if any(ch.isdigit() for ch in startup) and not startup.endswith("f") else ""
        char_name = row.get("char_name", "Unknown")
        move_name = row.get("moveName", "Unknown")
        num_cmd = row.get("numCmd", "?")
        lines.append(f"{char_name}'s {move_name} ({num_cmd}) startup is {startup}{startup_suffix}.")
    return format_property_only_lines(lines)


def format_hitconfirm_only_reply(rows):
    lines = []
    for row in iter_unique_frame_rows(rows):
        char_name = row.get("char_name", "Unknown")
        move_name = row.get("moveName", "Unknown")
        num_cmd = row.get("numCmd", "?")
        hc_sp = str(row.get("hcWinSpCa", "-")).replace("*", ",").strip() or "-"
        hc_tc = str(row.get("hcWinTc", "-")).replace("*", ",").strip() or "-"
        hc_notes = str(row.get("hcWinNotes", "-")).replace("[", "").replace("]", "").replace('"', "").strip() or "-"
        lines.append(
            f"{char_name}'s {move_name} ({num_cmd}) hit confirm window is Sp/Su: {hc_sp}, TC: {hc_tc}. Notes: {hc_notes}"
        )
    return format_property_only_lines(lines)


def format_super_gain_only_reply(rows):
    lines = []
    for row in iter_unique_frame_rows(rows):
        char_name = row.get("char_name", "Unknown")
        move_name = row.get("moveName", "Unknown")
        num_cmd = row.get("numCmd", "?")
        super_hit = str(row.get("SelfSoH", "-")).replace("*", ",").strip() or "-"
        super_block = str(row.get("SelfSoB", "-")).replace("*", ",").strip() or "-"
        lines.append(
            f"{char_name}'s {move_name} ({num_cmd}) super gain is Hit: {super_hit}, Block: {super_block}."
        )
    return format_property_only_lines(lines)


def format_range_only_reply(rows):
    unique_rows = iter_unique_frame_rows(rows)

    if not unique_rows:
        return ""

    if len(unique_rows) == 1:
        row = unique_rows[0]
        range_value, has_numeric_range = get_attack_range_details(row)
        if not has_numeric_range:
            return RANGE_SCROLLS_MISSING_TEXT
        char_name = row.get("char_name", "Unknown")
        move_name = row.get("moveName", "Unknown")
        num_cmd = row.get("numCmd", "?")
        return f"{char_name}'s {move_name} ({num_cmd}) range is {range_value}."

    lines = []
    for row in unique_rows:
        char_name = row.get("char_name", "Unknown")
        move_name = row.get("moveName", "Unknown")
        num_cmd = row.get("numCmd", "?")
        range_value, has_numeric_range = get_attack_range_details(row)
        if has_numeric_range:
            lines.append(f"{char_name}'s {move_name} ({num_cmd}) range is {range_value}.")
        else:
            lines.append(
                f"{char_name}'s {move_name} ({num_cmd}): {RANGE_SCROLLS_MISSING_TEXT}"
            )
    return format_property_only_lines(lines)


def truncate_embed_value(value, limit):
    return shared_truncate_value(value, limit)


def is_missing_embed_value(value):
    return is_missing_value(value)


def clean_embed_value(value, default="", strip_brackets=False):
    return shared_clean_value(value, default, strip_brackets=strip_brackets)


def add_embed_field(embed, name, value, inline=True):
    shared_add_embed_field(embed, name, value, inline=inline)


def add_long_embed_field(embed, name, value, inline=False):
    shared_add_long_embed_field(embed, name, value, inline=inline)


def format_hit_block_value(hit_value, block_value):
    hit = clean_embed_value(hit_value)
    block = clean_embed_value(block_value)
    parts = []
    if hit:
        parts.append(f"Hit: {hit}")
    if block:
        parts.append(f"Block: {block}")
    return " / ".join(parts)


def format_guard_value(value):
    guard = clean_embed_value(value)
    return "OH" if guard.strip().lower() == "m" else guard


def get_notes_text(row):
    return clean_embed_value(row.get("extraInfo", ""), strip_brackets=True)


def sf6_row_has_notes_content(row):
    if get_notes_text(row):
        return True
    for key in ("hcWinSpCa", "hcWinTc", "hcWinNotes"):
        if not is_missing_embed_value(clean_embed_value(row.get(key, ""))):
            return True
    return False


# SF6 frame Discord embeds


def build_frame_embed(row, image_url_override=None, show_notes=False):
    char_name = clean_embed_value(row.get("char_name", "Unknown"), default="Unknown")
    move_name = clean_embed_value(row.get("moveName", "Unknown"), default="Unknown")
    num_cmd = clean_embed_value(row.get("numCmd", "?"), default="?")
    drink_level_suffix = ""
    if str(row.get("char_name", "")).strip().lower() == "jamie":
        try:
            drink_level = int(row.get("_jamie_drink_level", 0) or 0)
        except (TypeError, ValueError):
            drink_level = 0
        drink_level_suffix = f" (drink level {drink_level})"

    embed = discord.Embed(
        title=truncate_embed_value(char_name, 256),
        description=truncate_embed_value(f"{move_name} ({num_cmd}){drink_level_suffix}", 4096),
        colour=BRAND,
    )

    startup = clean_embed_value(row.get("startup", ""))
    active = clean_embed_value(row.get("active", ""))
    recovery = clean_embed_value(row.get("recovery", "")).replace("(", " (Whiff: ")
    cancel = clean_embed_value(row.get("xx", ""))
    damage = clean_embed_value(row.get("dmg", ""))
    guard = format_guard_value(row.get("atkLvl", ""))
    atk_range = format_attack_range_for_table(row)
    on_hit = clean_embed_value(row.get("onHit", ""))
    on_block = clean_embed_value(row.get("onBlock", ""))

    chip_damage = clean_embed_value(row.get("chp", ""))
    drive_hit = clean_embed_value(row.get("DDoH", ""))
    drive_block = clean_embed_value(row.get("DDoB", ""))
    super_hit = clean_embed_value(row.get("SelfSoH", ""))
    super_block = clean_embed_value(row.get("SelfSoB", ""))

    stun_hit = clean_embed_value(row.get("hitstun", ""))
    stun_block = clean_embed_value(row.get("blockstun", ""))

    add_embed_field(embed, "Startup", startup, inline=True)
    add_embed_field(embed, "Active", active, inline=True)
    add_embed_field(embed, "Recovery", recovery, inline=True)

    add_embed_field(embed, "On Hit", on_hit, inline=True)
    add_embed_field(embed, "On Block", on_block, inline=True)
    add_embed_field(embed, "Cancel", cancel, inline=True)

    add_embed_field(embed, "Damage", damage, inline=True)
    add_embed_field(embed, "Guard", guard, inline=True)
    add_embed_field(embed, "Range", atk_range, inline=True)
    add_embed_field(embed, "Chip Damage", chip_damage, inline=True)
    add_embed_field(embed, "Drive Dmg", format_hit_block_value(drive_hit, drive_block), inline=True)
    add_embed_field(embed, "Super Gain", format_hit_block_value(super_hit, super_block), inline=True)
    add_embed_field(embed, "Stun", format_hit_block_value(stun_hit, stun_block), inline=True)

    if show_notes:
        hc_sp = clean_embed_value(row.get("hcWinSpCa", ""))
        hc_tc = clean_embed_value(row.get("hcWinTc", ""))
        hc_notes = clean_embed_value(row.get("hcWinNotes", ""), strip_brackets=True)
        add_embed_field(embed, "Hit Confirm (Sp/Su)", hc_sp, inline=True)
        add_embed_field(embed, "Hit Confirm (TC)", hc_tc, inline=True)
        add_embed_field(embed, "Hit Confirm Notes", hc_notes, inline=False)
        add_long_embed_field(embed, "Notes", get_notes_text(row), inline=False)

    image_url = image_url_override or get_sf6_move_image_url(row)
    if image_url:
        embed.set_image(url=image_url)

    return strip_embed_emojis(embed)


def iter_unique_frame_rows(rows):
    return unique_rows(rows)


def build_frame_embeds(rows):
    embeds = []
    for row in iter_unique_frame_rows(rows):
        embeds.append(build_frame_embed(row))
    return embeds


def sanitize_embed_followup_text(text):
    raw = str(text or "").strip()
    if not raw:
        return "Noted. The relevant frame data is in the embeds above."

    table_markers = [
        "Startup:",
        "Active:",
        "Recovery:",
        "Range:",
        "On Hit:",
        "On Block:",
        "Chip Damage",
        "Drive Dmg",
        "Super Gain",
        "Hit Confirm",
        "Stun Frames",
        "Character:",
    ]
    filtered_lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(marker in stripped for marker in table_markers):
            continue
        if stripped.startswith("**") and stripped.endswith("**"):
            continue
        filtered_lines.append(stripped)

    cleaned = "\n".join(filtered_lines).strip()
    if cleaned:
        return cleaned

    sentence_candidates = re.split(r"(?<=[.!?])\s+", raw)
    for sentence in sentence_candidates:
        sentence = sentence.strip()
        if sentence:
            return sentence
    return "Noted. The relevant frame data is in the embeds above."


# Missing-gif and panel toggle Discord views


class MissingHitboxGifShowFramedataButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Show Framedata", style=ACTIVE)

    async def callback(self, interaction: discord.Interaction):
        parent = self.view
        if not getattr(parent, "rows", None):
            await interaction.response.send_message("No frame data available.", ephemeral=True)
            return
        await interaction.response.defer()
        sent_ids = await send_frame_embeds_with_views(
            interaction.channel,
            parent.rows,
            owner_id=getattr(parent, "owner_id", None) or getattr(interaction.user, "id", None),
        )
        on_frame_sent = getattr(parent, "on_frame_sent", None)
        if on_frame_sent:
            on_frame_sent(sent_ids)
        if not sent_ids:
            await interaction.followup.send("I could not load that framedata table.", ephemeral=True)


class MissingHitboxGifView(discord.ui.View):
    def __init__(self, rows, owner_id=None, on_frame_sent=None, source_message=None, failure_reason="missing_hitbox_gif"):
        super().__init__(timeout=None)
        self.rows = list(rows or [])
        self.owner_id = owner_id
        self.on_frame_sent = on_frame_sent
        self.add_item(MissingHitboxGifShowFramedataButton())
        from bubbot.features.failed_prompt_report import attach_failed_prompt_report_button

        attach_failed_prompt_report_button(
            self,
            source_message,
            bub_response_text=MISSING_HITBOX_GIF_TEXT,
            failure_reason=failure_reason,
        )


async def send_missing_hitbox_gif_reply(
    message,
    rows,
    *,
    include_framedata_button=True,
    reply_and_log_response=None,
    record_frame_data_ids=None,
):
    unique_rows = iter_unique_frame_rows(rows or [])
    view = None
    if include_framedata_button and unique_rows:
        on_frame_sent = record_frame_data_ids
        view = MissingHitboxGifView(
            unique_rows,
            owner_id=getattr(message.author, "id", None),
            on_frame_sent=on_frame_sent,
            source_message=message,
        )
    if reply_and_log_response:
        return await reply_and_log_response(message, MISSING_HITBOX_GIF_TEXT, "missing_hitbox_gif", view=view)
    from bubbot.features.failed_prompt_report import (
        build_failed_prompt_report_view,
        stamp_report_context_on_sent,
    )

    if view is None:
        view = build_failed_prompt_report_view(
            message,
            bub_response_text=MISSING_HITBOX_GIF_TEXT,
            failure_reason="missing_hitbox_gif",
        )
    sent = await message.reply(MISSING_HITBOX_GIF_TEXT, view=view)
    stamp_report_context_on_sent(view, sent)
    return sent


class FrameDataGifButton(discord.ui.Button):
    def __init__(self, row, gif_links, showing_gif=False):
        super().__init__(
            label="Hide Image" if showing_gif else "Show GIF",
            style=toggle_style(showing_gif),
            disabled=not gif_links,
        )
        self.frame_row = row
        self.gif_links = list(gif_links or [])
        self.original_image_url = get_sf6_move_image_url(row)
        self.showing_gif = bool(showing_gif and self.gif_links)

    async def callback(self, interaction: discord.Interaction):
        move_name = str((self.frame_row or {}).get("moveName", "This move")).strip() or "This move"
        if not self.gif_links:
            view = MissingHitboxGifView(
                [self.frame_row],
                owner_id=getattr(interaction.user, "id", None),
            )
            await interaction.response.send_message(
                f"I have frame data for {move_name} but no hitbox gif link yet. "
                f"{FRAME_DATA_ERROR_CONTACT_TEXT}",
                view=view,
                ephemeral=True,
            )
            return

        asset_paths = get_existing_local_gif_asset_paths(self.gif_links, limit=DISCORD_ATTACHMENT_LIMIT)
        if asset_paths:
            if interaction.message and interaction.message.embeds:
                embed = discord.Embed.from_dict(interaction.message.embeds[0].to_dict())
            else:
                embed = build_frame_embed(self.frame_row)

            if self.showing_gif:
                self.label = "Show GIF"
                self.style = ACTION
                self.showing_gif = False
                if hasattr(self.view, "build_embed"):
                    embed = self.view.build_embed()
                else:
                    embed.set_image(url=self.original_image_url)
                    embed.set_thumbnail(url=None)
                await interaction.response.edit_message(embed=embed, attachments=[], view=self.view)
                return

            asset_path = asset_paths[0]
            filename = os.path.basename(asset_path)
            embed.set_thumbnail(url=None)
            embed.set_image(url=f"attachment://{filename}")
            self.label = "Hide Image"
            self.style = ACTIVE
            self.showing_gif = True
            if hasattr(self.view, "build_embed"):
                embed = self.view.build_embed()
            await interaction.response.edit_message(
                embed=embed,
                attachments=[discord.File(asset_path, filename=filename)],
                view=self.view,
            )
            return

        if len(self.gif_links) == 1:
            await interaction.response.send_message(self.gif_links[0])
            return

        await interaction.response.send_message("\n".join(self.gif_links))


async def _edit_frame_data_view(interaction, view, attachments):
    await interaction.response.defer()
    existing_attachments = list(getattr(getattr(interaction, "message", None), "attachments", []) or [])
    next_attachments = existing_attachments if attachments and existing_attachments else attachments
    await interaction.edit_original_response(
        embed=view.build_embed(),
        view=view,
        attachments=next_attachments,
    )


def _set_toggle_button_style(button, active):
    button.label = f"Hide {button.panel_label}" if active else f"Show {button.panel_label}"
    button.style = toggle_style(active)


class SF6PanelToggleButton(discord.ui.Button):
    panel_label = "Panel"
    toggle_attr = ""

    async def callback(self, interaction: discord.Interaction):
        if not hasattr(self.view, "build_embed"):
            await interaction.response.defer()
            return
        if await self.before_toggle(interaction):
            return
        show = not getattr(self.view, self.toggle_attr, False)
        setattr(self.view, self.toggle_attr, show)
        if show:
            self.reset_other_panels()
        _set_toggle_button_style(self, show)
        attachments = self.attachments_for_toggle()
        await _edit_frame_data_view(interaction, self.view, attachments)

    async def before_toggle(self, interaction):
        return False

    def reset_other_panels(self):
        pass

    def attachments_for_toggle(self):
        return []


class SF6ShowStatsButton(SF6PanelToggleButton):
    panel_label = "Stats"

    def __init__(self, disabled=False):
        super().__init__(
            label="Show Stats",
            style=ACTION,
            disabled=disabled,
            row=0,
        )
        self.toggle_attr = "show_stats"

    async def before_toggle(self, interaction):
        if getattr(self.view, "character_stats", None):
            return False
        await interaction.response.send_message(
            "No character stats are loaded for this fighter.",
            ephemeral=True,
        )
        return True

    def reset_other_panels(self):
        self.view.show_notes = False
        if hasattr(self.view, "notes_button"):
            _set_toggle_button_style(self.view.notes_button, False)

    def attachments_for_toggle(self):
        if getattr(self.view, "show_stats", False):
            return []
        if hasattr(self.view, "active_files"):
            return self.view.active_files()
        return []


class SF6NotesButton(SF6PanelToggleButton):
    panel_label = "Notes"

    def __init__(self, row):
        self.frame_row = row
        self.notes_text = get_notes_text(row)
        self.toggle_attr = "show_notes"
        super().__init__(
            label="Show Notes",
            style=ACTION,
            disabled=not sf6_row_has_notes_content(row),
            row=0,
        )

    def reset_other_panels(self):
        self.view.show_stats = False
        if hasattr(self.view, "stats_button"):
            _set_toggle_button_style(self.view.stats_button, False)

    def attachments_for_toggle(self):
        if hasattr(self.view, "active_files"):
            return self.view.active_files()
        return []


# Channel send helpers


async def send_character_stats_response(message, char_keys, stat_keys=None, *, client=None):
    from bubbot.features.failed_prompt_report import (
        build_failed_prompt_report_view,
        stamp_report_context_on_sent,
    )
    from bubbot.utils.embed_source_utils import apply_game_source_footer, source_icon_files

    channel = getattr(message, "channel", message)
    owner_id = getattr(getattr(message, "author", None), "id", None)
    prompt = str(getattr(message, "content", "") or "")
    unique_keys = []
    for char_key in char_keys or []:
        if char_key and char_key not in unique_keys:
            unique_keys.append(char_key)
    sent_ids = []
    for char_key in unique_keys:
        stats = (FRAME_STATS or {}).get(char_key)
        if not stats:
            continue
        embed = apply_game_source_footer(build_character_stats_embed(char_key, stats, stat_keys), "sf6")
        files = source_icon_files("sf6", kind="frame")
        if owner_id:
            from bubbot.features.menu_system import StatsResultView

            view = StatsResultView(
                char_key,
                owner_id,
                stat_keys=stat_keys,
                menu_locked=False,
                source_message=message,
                prompt=prompt,
            )
        else:
            view = build_failed_prompt_report_view(
                message if hasattr(message, "content") else None,
                bub_response_text=f"{char_key} — Character Stats",
                failure_reason="stats_response",
            )
        sent = await channel.send(embed=embed, view=view, files=files)
        stamp_report_context_on_sent(view, sent, client=client)
        sent_ids.append(sent.id)
    return sent_ids


async def send_frame_embeds_with_views(
    channel,
    rows,
    embeds=None,
    owner_id=None,
    *,
    source_message=None,
    prompt="",
    client=None,
):
    from bubbot.features.failed_prompt_report import (
        build_failed_prompt_report_view,
        stamp_report_context_on_sent,
    )
    from bubbot.features.menu_system import build_frame_result_view

    unique_rows = iter_unique_frame_rows(rows or [])
    if not unique_rows:
        return []

    if embeds is not None:
        embed_list = list(embeds)
        sent_ids = []
        for index, embed in enumerate(embed_list):
            if index >= len(unique_rows):
                fallback_view = build_failed_prompt_report_view(
                    source_message,
                    bub_response_text=getattr(embed, "title", None) or "frame data",
                    failure_reason="frame_data_response",
                )
                sent = await channel.send(embed=embed, view=fallback_view)
                stamp_report_context_on_sent(fallback_view, sent, client=client)
                sent_ids.append(sent.id)
                continue
            view = build_frame_result_view(
                "sf6",
                unique_rows[index],
                owner_id=owner_id,
                menu_locked=False,
                source_message=source_message,
                prompt=prompt,
            )
            embed = view.build_embed()
            sent = await channel.send(embed=embed, view=view, files=view.initial_files())
            stamp_report_context_on_sent(view, sent, client=client)
            sent_ids.append(sent.id)
        return sent_ids

    return await _send_sf6_frame_result_messages(
        channel,
        unique_rows,
        owner_id=owner_id,
        source_message=source_message,
        prompt=prompt,
        client=client,
    )


async def _send_sf6_frame_result_messages(
    channel,
    rows,
    *,
    owner_id=None,
    source_message=None,
    prompt="",
    client=None,
):
    from bubbot.features.failed_prompt_report import stamp_report_context_on_sent
    from bubbot.features.menu_system import build_frame_result_view

    sent_ids = []
    for row in rows:
        view = build_frame_result_view(
            "sf6",
            row,
            owner_id=owner_id,
            menu_locked=False,
            source_message=source_message,
            prompt=prompt,
        )
        embed = view.build_embed()
        sent = await channel.send(embed=embed, view=view, files=view.initial_files())
        stamp_report_context_on_sent(view, sent, client=client)
        sent_ids.append(sent.id)
    return sent_ids


async def _send_frame_embed_only(message, embed, *, view=None, files=None, reply=False):
    primary_send = message.reply if reply else message.channel.send
    full_kwargs = {"embed": embed}
    if view is not None:
        full_kwargs["view"] = view
    if files:
        full_kwargs["files"] = files

    attempts = [(primary_send, full_kwargs)]
    if files:
        retry_kwargs = {"embed": embed, "view": view} if view is not None else {"embed": embed}
        attempts.append((primary_send, retry_kwargs))
    if view is not None:
        attempts.append((primary_send, {"embed": embed}))
    if reply:
        if view is not None:
            attempts.append((message.channel.send, {"embed": embed, "view": view}))
        attempts.append((message.channel.send, {"embed": embed}))

    errors = []
    for send, kwargs in attempts:
        try:
            return await send(**kwargs)
        except Exception as error:
            errors.append(str(error))
    print(f"Frame embed send failed after {len(attempts)} attempts: {' | '.join(errors)}", flush=True)
    return None


async def send_frame_table_response(message, rows, data_text=None):
    from bubbot.features.failed_prompt_report import stamp_report_context_on_sent
    from bubbot.features.menu_system import build_frame_result_view

    unique_rows = iter_unique_frame_rows(rows or [])
    if not unique_rows:
        return []
    sent_ids = []
    owner_id = getattr(message.author, "id", None)
    prompt = str(getattr(message, "content", "") or "")
    for index, row in enumerate(unique_rows):
        view = None
        files = []
        try:
            view = build_frame_result_view(
                "sf6",
                row,
                owner_id=owner_id,
                menu_locked=False,
                source_message=message,
                prompt=prompt,
            )
            embed = view.build_embed()
            try:
                files = view.initial_files()
            except Exception as file_error:
                print(f"Frame embed attachment load failed: {file_error}", flush=True)
        except Exception as view_error:
            print(f"Frame result view build failed; sending base embed: {view_error}", flush=True)
            from bubbot.features.failed_prompt_report import attach_frame_report_button

            view = discord.ui.View(timeout=None)
            attach_frame_report_button(
                view,
                game="sf6",
                row=row,
                source_message=message,
                prompt=prompt,
            )
            files = []
            try:
                embed = build_frame_embed(row)
            except Exception as embed_error:
                print(f"Frame embed build failed: {embed_error}", flush=True)
                continue

        sent = await _send_frame_embed_only(
            message,
            embed,
            view=view,
            files=files,
            reply=index == 0,
        )
        if sent is not None:
            if view is not None:
                stamp_report_context_on_sent(view, sent)
            sent_ids.append(sent.id)
    return sent_ids


async def send_gif_links_response(message, gif_links, wants_comparison=False):
    if not gif_links:
        return []
    try:
        remote_links = [
            str(link).strip()
            for link in gif_links
            if str(link or "").strip().lower().startswith(("https://", "http://"))
        ]
        if remote_links:
            links_to_send = remote_links if wants_comparison else remote_links[:1]
            sent = await message.reply("\n".join(links_to_send))
            return [sent.id]

        asset_limit = len(gif_links) if wants_comparison else 1
        asset_paths = get_existing_local_gif_asset_paths(gif_links, limit=asset_limit)
        if asset_paths:
            if wants_comparison and len(asset_paths) > 1:
                sent = await message.reply(
                    files=[discord.File(path, filename=os.path.basename(path)) for path in asset_paths]
                )
            else:
                sent = await message.reply(
                    file=discord.File(asset_paths[0], filename=os.path.basename(asset_paths[0]))
                )
            return [sent.id]

        if wants_comparison and len(gif_links) > 1:
            sent = await message.reply("\n".join(gif_links))
        else:
            sent = await message.reply(gif_links[0])
        return [sent.id]
    except Exception as reply_error:
        if is_deleted_message_reference_error(reply_error):
            print("Hitbox gif reply target deleted. Triggering failsafe.", flush=True)
            await send_deleted_message_failsafe(message.channel)
        else:
            print(f"Hitbox gif reply error: {reply_error}", flush=True)
    return []
