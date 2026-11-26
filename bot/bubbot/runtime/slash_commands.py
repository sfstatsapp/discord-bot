"""Public slash command registration and global Discord command tree sync.
Registers /sf6, cross-game frame lookups, combos, stats, and /sfstatsmenu handlers once per tree."""

import os
import re

import aiohttp
import discord

from bubbot.runtime import sfstats_bridge
from bubbot.utils.choice_utils import autocomplete_values, character_choices, move_choices
from bubbot.features.fg_glossary import build_glossary_definition_embed, build_glossary_link_view
from bubbot.frame_data.sf6_character_stats import (
    build_character_stats_embed,
    build_slash_stats_query,
    sf6_stat_slash_autocomplete_values,
)
from bubbot.utils.slash_frame_flow import send_slash_frame_result, send_slash_stats_result
from bubbot.utils.sfstats_style import CardView


def _activity_entry_enabled():
    """The Streetfighterdle launcher needs Activities enabled on the app; STREETFIGHTERDLE_ACTIVITY_ENTRY=false leaves it out."""
    return os.getenv("STREETFIGHTERDLE_ACTIVITY_ENTRY", "true").strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


async def _post_activity_entry_command(session, url, payload, authorization_label, authorization_value):
    headers = {"Authorization": authorization_value, "Content-Type": "application/json"}
    async with session.post(url, headers=headers, json=payload) as response:
        body = await response.text()
        if response.status >= 400:
            print(
                f"[streetfighterdle] primary entry command registration failed "
                f"auth={authorization_label} status={response.status}: {body[:300]}",
                flush=True,
            )
            return False
    print(
        f"[streetfighterdle] Primary Activity entry command registered via {authorization_label}.",
        flush=True,
    )
    return True


def _streetfighterdle_activity_entry_payload():
    return {
        "name": "Streetfighterdle",
        "description": "Launch Streetfighterdle Activity",
        "type": 4,
        "handler": 2,
        "integration_types": [0],
        "contexts": [0],
    }


async def _bulk_sync_global_commands_with_activity_entry(client, tree, local_commands, include_activity=True):
    application_id = _env_int("STREETFIGHTERDLE_ACTIVITY_APPLICATION_ID", 0)
    if not application_id:
        application_id = getattr(client, "application_id", None) or getattr(getattr(client, "user", None), "id", None)
    token = os.getenv("DISCORD_TOKEN")
    if not application_id or not token:
        return None

    payload = [command.to_dict(tree) for command in local_commands]
    payload = [command for command in payload if int(command.get("type", 1) or 1) != 4]
    # SF Stats commands join the global set only at cutover (SFSTATS_COMMANDS_GLOBAL).
    payload = sfstats_bridge.merge_global_commands(payload)
    if include_activity:
        payload.append(_streetfighterdle_activity_entry_payload())
    url = f"https://discord.com/api/v10/applications/{application_id}/commands"
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        async with session.put(url, headers=headers, json=payload) as response:
            body = await response.text()
            if response.status >= 400:
                print(
                    f"[menu] Global slash/activity command raw sync failed "
                    f"status={response.status}: {body[:500]}",
                    flush=True,
                )
                return None
            try:
                return await response.json(content_type=None)
            except Exception:
                print(f"[menu] Global slash/activity command raw sync returned invalid JSON: {body[:500]}", flush=True)
                return None


async def _delete_existing_activity_entry_commands(session, application_id, authorization_label, authorization_value):
    base_url = f"https://discord.com/api/v10/applications/{application_id}/commands"
    headers = {"Authorization": authorization_value}
    async with session.get(base_url, headers=headers) as response:
        body = await response.text()
        if response.status >= 400:
            print(
                f"[streetfighterdle] primary entry command lookup failed "
                f"auth={authorization_label} status={response.status}: {body[:300]}",
                flush=True,
            )
            return False
        try:
            commands = await response.json(content_type=None)
        except Exception:
            print(f"[streetfighterdle] primary entry command lookup returned invalid JSON: {body[:300]}", flush=True)
            return False
    deleted_any = False
    for command in commands or []:
        if int(command.get("type", 0) or 0) != 4:
            continue
        command_id = command.get("id")
        if not command_id:
            continue
        async with session.delete(f"{base_url}/{command_id}", headers=headers) as response:
            body = await response.text()
            if response.status >= 400:
                print(
                    f"[streetfighterdle] primary entry command delete failed "
                    f"auth={authorization_label} status={response.status}: {body[:300]}",
                    flush=True,
                )
                return False
        deleted_any = True
    if deleted_any:
        print(f"[streetfighterdle] Deleted existing Primary Activity entry command via {authorization_label}.", flush=True)
    return True


async def _discord_client_credentials_token(session, application_id):
    client_secret = (
        os.getenv("DISCORD_CLIENT_SECRET")
        or os.getenv("DISCORD_APPLICATION_CLIENT_SECRET")
        or ""
    ).strip()
    if not client_secret:
        return None
    data = {
        "grant_type": "client_credentials",
        "scope": "applications.commands.update",
    }
    async with session.post(
        "https://discord.com/api/v10/oauth2/token",
        data=data,
        auth=aiohttp.BasicAuth(str(application_id), client_secret),
    ) as response:
        payload = await response.json(content_type=None)
        if response.status >= 400:
            print(
                f"[streetfighterdle] client-credentials token request failed "
                f"status={response.status}: {str(payload)[:300]}",
                flush=True,
            )
            return None
    return payload.get("access_token")


async def _register_streetfighterdle_activity_entry_command(client):
    application_id = _env_int("STREETFIGHTERDLE_ACTIVITY_APPLICATION_ID", 0)
    if not application_id:
        application_id = getattr(client, "application_id", None) or getattr(getattr(client, "user", None), "id", None)
    token = os.getenv("DISCORD_TOKEN")
    if not application_id or not token:
        return
    payload = _streetfighterdle_activity_entry_payload()
    url = f"https://discord.com/api/v10/applications/{application_id}/commands"
    try:
        async with aiohttp.ClientSession() as session:
            if await _post_activity_entry_command(session, url, payload, "bot", f"Bot {token}"):
                return
            bearer_token = await _discord_client_credentials_token(session, application_id)
            if bearer_token:
                await _post_activity_entry_command(session, url, payload, "client_credentials", f"Bearer {bearer_token}")
    except Exception as error:
        print(f"[streetfighterdle] primary entry command registration error: {error}", flush=True)


async def _delete_streetfighterdle_activity_entry_command(client):
    application_id = _env_int("STREETFIGHTERDLE_ACTIVITY_APPLICATION_ID", 0)
    if not application_id:
        application_id = getattr(client, "application_id", None) or getattr(getattr(client, "user", None), "id", None)
    token = os.getenv("DISCORD_TOKEN")
    if not application_id or not token:
        return
    try:
        async with aiohttp.ClientSession() as session:
            if await _delete_existing_activity_entry_commands(session, application_id, "bot", f"Bot {token}"):
                return
            bearer_token = await _discord_client_credentials_token(session, application_id)
            if bearer_token:
                await _delete_existing_activity_entry_commands(session, application_id, "client_credentials", f"Bearer {bearer_token}")
    except Exception as error:
        print(f"[streetfighterdle] primary entry command cleanup error: {error}", flush=True)


def register_slash_commands(tree, deps):
    """Register public slash commands and autocomplete handlers."""
    if getattr(tree, "_bub_slash_registered", False):
        return
    setattr(tree, "_bub_slash_registered", True)

    frame_data = deps["frame_data"]
    frame_stats = deps["frame_stats"]
    resolve_character_key = deps["resolve_character_key"]
    find_moves_in_text = deps["find_moves_in_text"]
    build_frame_embed = deps["build_frame_embed"]
    frame_output_module = deps["frame_output_module"]
    ggst_module = deps["ggst_module"]
    sfv_module = deps["sfv_module"]
    tuco_module = deps["tuco_module"]
    bbcf_module = deps["bbcf_module"]
    ggacr_module = deps["ggacr_module"]
    cotw_module = deps["cotw_module"]
    third_strike_module = deps["third_strike_module"]
    usfiv_module = deps["usfiv_module"]
    mk1_module = deps["mk1_module"]
    avtl_module = deps["avtl_module"]
    combo_data_module = deps["combo_data_module"]
    menu_system = deps["menu_system"]
    buenavista_extension = deps.get("buenavista_extension")
    if buenavista_extension is None:
        from bubbot.runtime.buenavista_extension import buenavista_extension as _default_bv_extension

        buenavista_extension = _default_bv_extension

    def slash_choices(values):
        return [discord.app_commands.Choice(name=str(value)[:100], value=str(value)[:100]) for value in values[:25]]

    def move_choice_label(row):
        move_name = str(row.get("moveName", "")).strip()
        num_cmd = str(row.get("numCmd", "")).strip()
        move_type = str(row.get("moveType", "")).strip().lower()
        label = f"{move_name} ({num_cmd})" if move_name and num_cmd else move_name or num_cmd
        if move_type and move_type not in {"normal", ""}:
            label = f"{label} [{move_type}]"
        return label

    def sf6_character_choice_values():
        return sorted(display for _char_key, display in character_choices({key: rows for key, rows in frame_data.items() if rows}))

    def ggst_character_choice_values():
        return sorted(display for _char_key, display in character_choices({key: rows for key, rows in ggst_module.GGST_FRAME_DATA.items() if rows}))


    def sfv_character_choice_values():
        return sorted(
            display
            for _char_key, display in character_choices(
                {key: rows for key, rows in sfv_module.SFV_FRAME_DATA.items() if rows},
                display_fn=lambda char_key, _rows: sfv_module.display_char_name(char_key),
            )
        )

    def tuco_character_choice_values():
        return sorted(display for _char_key, display in character_choices({key: rows for key, rows in tuco_module.TUCO_FRAME_DATA.items() if rows}))

    def bbcf_character_choice_values():
        return sorted(display for _char_key, display in character_choices({key: rows for key, rows in bbcf_module.BBCF_FRAME_DATA.items() if rows}))

    def ggacr_character_choice_values():
        return sorted(display for _char_key, display in character_choices({key: rows for key, rows in ggacr_module.GGACR_FRAME_DATA.items() if rows}))

    def cotw_character_choice_values():
        return sorted(display for _char_key, display in character_choices({key: rows for key, rows in cotw_module.COTW_FRAME_DATA.items() if rows}))

    def third_strike_character_choice_values():
        return sorted(display for _char_key, display in character_choices({key: rows for key, rows in third_strike_module.THIRD_STRIKE_FRAME_DATA.items() if rows}))


    def usfiv_character_choice_values():
        return sorted(display for _char_key, display in character_choices({key: rows for key, rows in usfiv_module.USFIV_FRAME_DATA.items() if rows}))

    def avtl_character_choice_values():
        return sorted(display for _char_key, display in character_choices({key: rows for key, rows in avtl_module.AVTL_FRAME_DATA.items() if rows}))

    def mk1_character_choice_values():
        return sorted(
            display
            for _char_key, display in character_choices(
                {key: rows for key, rows in mk1_module.MK1_FRAME_DATA.items() if rows},
                display_fn=lambda char_key, _rows: mk1_module.display_char_name(char_key),
            )
        )

    def sf6_move_choice_values(char_name):
        char_key = resolve_character_key(char_name)
        if not char_key:
            return []
        return [
            label
            for _row, label in move_choices(
                frame_data.get(char_key, []),
                label_fn=move_choice_label,
                key_fields=("moveName", "numCmd", "moveType"),
            )
            if label
        ]

    def sf6_char_state_choice_values(char_name):
        char_key = resolve_character_key(char_name)
        state_map = {
            "ryu": ["denjin"],
            "jamie": ["drink 1", "drink 2", "drink 3", "drink 4"],
            "lily": ["stocked"],
            "mai": ["stocked"],
            "juri": ["stocked"],
        }
        return state_map.get(char_key, [])

    def ggst_move_choice_values(char_name):
        char_key = ggst_module.resolve_character_key(char_name)
        if not char_key:
            return []
        rows = []
        rows.extend(ggst_module.GGST_FRAME_DATA.get(char_key, []))
        rows.extend(ggst_module.GGST_SUPPLEMENTAL_FRAME_DATA.get(char_key, []))
        for state_rows in ggst_module.GGST_STATE_FRAME_DATA.get(char_key, {}).values():
            rows.extend(state_rows)
        values = []
        seen = set()
        for _row, label in move_choices(rows, label_fn=move_choice_label, key_fields=("moveName", "numCmd", "moveType")):
            if label and label not in seen:
                seen.add(label)
                values.append(label)
        return values


    def sfv_move_choice_values(char_name):
        char_key = sfv_module.resolve_character_key(char_name)
        if not char_key:
            return []
        rows = list(sfv_module.SFV_FRAME_DATA.get(char_key, []) or [])
        for state_rows in (sfv_module.SFV_TRIGGER_FRAME_DATA.get(char_key, {}) or {}).values():
            rows.extend(state_rows)
        return [label for _row, label in move_choices(rows, label_fn=move_choice_label, key_fields=("moveName", "numCmd", "state_label")) if label]

    def tuco_move_choice_values(char_name):
        char_key = tuco_module.resolve_character_key(char_name)
        if not char_key:
            return []
        return [label for _row, label in move_choices(tuco_module.TUCO_FRAME_DATA.get(char_key, []), label_fn=move_choice_label, key_fields=("moveName", "numCmd")) if label]

    def bbcf_move_choice_values(char_name):
        char_key = bbcf_module.resolve_character_key(char_name)
        if not char_key:
            return []
        return [label for _row, label in move_choices(bbcf_module.BBCF_FRAME_DATA.get(char_key, []), label_fn=move_choice_label, key_fields=("moveName", "numCmd", "moveType")) if label]

    def ggacr_move_choice_values(char_name):
        char_key = ggacr_module.resolve_character_key(char_name)
        if not char_key:
            return []
        return [label for _row, label in move_choices(ggacr_module.GGACR_FRAME_DATA.get(char_key, []), label_fn=move_choice_label, key_fields=("moveName", "numCmd", "moveType")) if label]

    def cotw_move_choice_values(char_name):
        char_key = cotw_module.resolve_character_key(char_name)
        if not char_key:
            return []
        return [label for _row, label in move_choices(cotw_module.COTW_FRAME_DATA.get(char_key, []), label_fn=move_choice_label, key_fields=("moveName", "numCmd", "moveType")) if label]

    def third_strike_move_choice_values(char_name):
        char_key = third_strike_module.resolve_character_key(char_name)
        if not char_key:
            return []
        return [label for _row, label in move_choices(third_strike_module.THIRD_STRIKE_FRAME_DATA.get(char_key, []), label_fn=move_choice_label, key_fields=("moveName", "numCmd", "version", "moveType")) if label]

    def usfiv_move_choice_values(char_name):
        char_key = usfiv_module.resolve_character_key(char_name)
        if not char_key:
            return []
        return [label for _row, label in move_choices(usfiv_module.USFIV_FRAME_DATA.get(char_key, []), label_fn=move_choice_label, key_fields=("moveName", "numCmd", "version", "moveType")) if label]

    def avtl_move_choice_values(char_name):
        char_key = avtl_module.resolve_character_key(char_name)
        if not char_key:
            return []
        return [label for _row, label in move_choices(avtl_module.AVTL_FRAME_DATA.get(char_key, []), label_fn=move_choice_label, key_fields=("moveName", "numCmd", "moveType")) if label]

    def mk1_move_choice_values(char_name):
        char_key = mk1_module.resolve_character_key(char_name)
        if not char_key:
            return []
        return [label for _row, label in move_choices(mk1_module.MK1_FRAME_DATA.get(char_key, []), label_fn=move_choice_label, key_fields=("moveName", "numCmd", "moveType")) if label]

    def combo_character_choice_values(game):
        return sorted(display for _char_key, display in combo_data_module.combo_character_list(game))

    def combo_group_choice_values(game, char_name):
        char_key = resolve_character_key(char_name) if game == "sf6" else mk1_module.resolve_character_key(char_name)
        if not char_key:
            return []
        return [label for label, _count in combo_data_module.combo_groups(game, char_key)]

    def ggst_char_state_choice_values(char_name):
        char_key = ggst_module.resolve_character_key(char_name)
        if not char_key:
            return []
        values = []
        seen = set()
        for row_map in ggst_module.GGST_STATE_FRAME_DATA.get(char_key, {}).values():
            for row in row_map:
                for value in (str(row.get("state_label", "")).strip(), str(row.get("state_key", "")).strip()):
                    if value and value not in seen:
                        seen.add(value)
                        values.append(value)
        for row in ggst_module.GGST_SUPPLEMENTAL_FRAME_DATA.get(char_key, []):
            for value in (str(row.get("state_label", "")).strip(), str(row.get("state_key", "")).strip()):
                if value and value not in seen:
                    seen.add(value)
                    values.append(value)
        return values


    def sfv_char_state_choice_values(char_name):
        char_key = sfv_module.resolve_character_key(char_name)
        if not char_key:
            return []
        states = sfv_module.SFV_TRIGGER_FRAME_DATA.get(char_key, {}) or {}
        labels = []
        if "trigger1" in states:
            labels.append("vt1")
        if "trigger2" in states:
            labels.append("vt2")
        return labels

    def strip_autocomplete_label(value):
        text = str(value or "").strip()
        text = re.sub(r"\s+\[[^\]]+\]$", "", text).strip()
        match = re.match(r"^(.+)\s+\(([^()]*)\)$", text)
        if match:
            return match.group(2).strip() or match.group(1).strip()
        return text

    def selected_choice_row(move_name, choices):
        selected = str(move_name or "").strip()
        if not selected:
            return None
        selected_lower = selected.lower()
        selected_short = selected[:100].lower()
        for row, label in choices or []:
            label_text = str(label or "").strip()
            if not label_text:
                continue
            if selected_lower in {label_text.lower(), label_text[:100].lower()}:
                return row
            if selected_short == label_text[:100].lower():
                return row
        return None

    def sf6_selected_row(char_name, move_name):
        char_key = resolve_character_key(char_name)
        if not char_key:
            return None, None
        choices = move_choices(
            frame_data.get(char_key, []),
            label_fn=move_choice_label,
            key_fields=("moveName", "numCmd", "moveType"),
        )
        return selected_choice_row(move_name, choices), char_key

    async def send_sf6_slash_stats(interaction, char_name, stat=None):
        char_key = resolve_character_key(char_name)
        stats_row = (frame_stats or {}).get(char_key or "")
        query = build_slash_stats_query(char_name, stat)
        payload = find_moves_in_text(query)
        if not payload.get("stats_query"):
            await interaction.response.send_message(
                f"{char_name} with `{stat or 'stats'}` is not a valid character/stat combination for SF6."
            )
            return
        stat_keys = payload.get("stats_keys")
        if stat_keys is not None:
            stat_keys = tuple(stat_keys)
        await send_slash_stats_result(
            interaction,
            char_name=char_name,
            char_key=char_key,
            stats_row=stats_row,
            build_stats_embed_fn=build_character_stats_embed,
            stat_keys=stat_keys,
            game_label="SF6",
            query=query,
        )

    async def send_sf6_slash_frame(interaction, char_name, move_name, char_state=None):
        if char_state and char_state not in sf6_char_state_choice_values(char_name):
            await interaction.response.send_message(f"{char_name} does not use the `{char_state}` state for SF6 lookups.")
            return
        selected_row, selected_char_key = sf6_selected_row(char_name, move_name)
        query = f"{char_name} {char_state or ''} {strip_autocomplete_label(move_name)} framedata".strip().lower()
        await send_slash_frame_result(
            interaction,
            char_name=char_name,
            move_name=move_name,
            query=query,
            parse_fn=find_moves_in_text,
            embed_fn=build_frame_embed,
            game="sf6",
            game_label="SF6",
            selected_row=None if char_state else selected_row,
            selected_char_key=selected_char_key,
            prompt_predicate=lambda payload: "Special Strength Options" in str(payload.get("data", "") or "") or "Target Combo Options" in str(payload.get("data", "") or ""),
        )

    async def send_ggst_slash_frame(interaction, char_name, move_name, char_state=None):
        query = f"ggst {char_name} {char_state or ''} {strip_autocomplete_label(move_name)} framedata".strip().lower()
        await send_slash_frame_result(interaction, char_name=char_name, move_name=move_name, query=query, parse_fn=ggst_module.find_moves_in_text, embed_fn=ggst_module.build_frame_embed, game="ggst", game_label="GGST", disambiguation_predicate=lambda payload: payload.get("needs_disambiguation"))


    async def send_sfv_slash_frame(interaction, char_name, move_name, char_state=None):
        if not char_state:
            if re.search(r"\[\s*v-?trigger\s*1\s*\]", str(move_name or ""), re.IGNORECASE):
                char_state = "vt1"
            elif re.search(r"\[\s*v-?trigger\s*2\s*\]", str(move_name or ""), re.IGNORECASE):
                char_state = "vt2"
        query = f"sfv {char_name} {char_state or ''} {strip_autocomplete_label(move_name)} framedata".strip().lower()
        await send_slash_frame_result(interaction, char_name=char_name, move_name=move_name, query=query, parse_fn=sfv_module.find_moves_in_text, embed_fn=sfv_module.build_frame_embed, game="sfv", game_label="SFV", disambiguation_predicate=lambda payload: payload.get("needs_disambiguation"))


    async def send_tuco_slash_frame(interaction, char_name, move_name):
        query = f"2xko {char_name} {strip_autocomplete_label(move_name)} framedata".strip().lower()
        await send_slash_frame_result(interaction, char_name=char_name, move_name=move_name, query=query, parse_fn=tuco_module.find_moves_in_text, embed_fn=tuco_module.build_frame_embed, game="tuco", game_label="2XKO", disambiguation_predicate=lambda payload: payload.get("needs_disambiguation"))

    async def send_bbcf_slash_frame(interaction, char_name, move_name):
        query = f"bbcf {char_name} {strip_autocomplete_label(move_name)} framedata".strip().lower()
        await send_slash_frame_result(interaction, char_name=char_name, move_name=move_name, query=query, parse_fn=bbcf_module.find_moves_in_text, embed_fn=bbcf_module.build_frame_embed, game="bbcf", game_label="BBCF", disambiguation_predicate=lambda payload: payload.get("needs_disambiguation"))

    async def send_ggacr_slash_frame(interaction, char_name, move_name):
        query = f"ggacr {char_name} {strip_autocomplete_label(move_name)} framedata".strip().lower()
        await send_slash_frame_result(interaction, char_name=char_name, move_name=move_name, query=query, parse_fn=ggacr_module.find_moves_in_text, embed_fn=ggacr_module.build_frame_embed, game="ggacr", game_label="GGACR", disambiguation_predicate=lambda payload: payload.get("needs_disambiguation"))

    async def send_cotw_slash_frame(interaction, char_name, move_name):
        query = f"cotw {char_name} {strip_autocomplete_label(move_name)} framedata".strip().lower()
        await send_slash_frame_result(
            interaction,
            char_name=char_name,
            move_name=move_name,
            query=query,
            parse_fn=cotw_module.find_moves_in_text,
            embed_fn=cotw_module.build_frame_embed,
            game="cotw",
            game_label="COTW",
            disambiguation_predicate=lambda payload: payload.get("needs_disambiguation"),
        )

    async def send_third_strike_slash_frame(interaction, char_name, move_name):
        query = f"3s {char_name} {strip_autocomplete_label(move_name)} framedata".strip().lower()
        await send_slash_frame_result(interaction, char_name=char_name, move_name=move_name, query=query, parse_fn=third_strike_module.find_moves_in_text, embed_fn=third_strike_module.build_frame_embed, game="third_strike", game_label="Third Strike", disambiguation_predicate=lambda payload: payload.get("needs_disambiguation"))

    async def send_usfiv_slash_frame(interaction, char_name, move_name):
        query = f"usf4 {char_name} {strip_autocomplete_label(move_name)} framedata".strip().lower()
        await send_slash_frame_result(interaction, char_name=char_name, move_name=move_name, query=query, parse_fn=usfiv_module.find_moves_in_text, embed_fn=usfiv_module.build_frame_embed, game="usf4", game_label="USF4", disambiguation_predicate=lambda payload: payload.get("needs_disambiguation"))

    async def send_avtl_slash_frame(interaction, char_name, move_name):
        query = f"avtl {char_name} {strip_autocomplete_label(move_name)} framedata".strip().lower()
        await send_slash_frame_result(interaction, char_name=char_name, move_name=move_name, query=query, parse_fn=avtl_module.find_moves_in_text, embed_fn=avtl_module.build_frame_embed, game="avtl", game_label="AVTL", disambiguation_predicate=lambda payload: payload.get("needs_disambiguation"))

    async def send_mk1_slash_frame(interaction, char_name, move_name):
        query = f"mk1 {char_name} {strip_autocomplete_label(move_name)} framedata".strip().lower()
        await send_slash_frame_result(interaction, char_name=char_name, move_name=move_name, query=query, parse_fn=mk1_module.find_moves_in_text, embed_fn=mk1_module.build_frame_embed, game="mk1", game_label="MK1", disambiguation_predicate=lambda payload: payload.get("needs_disambiguation"))

    async def send_slash_combos(interaction, game, char_name, difficulty=None, position=None, group=None):
        query_parts = [game, char_name, group or "", difficulty or "", position or "", "combos"]
        payload = combo_data_module.find_combo_rows_in_text(game, " ".join(part for part in query_parts if part).lower())
        char_key = payload.get("char_key")
        if not char_key:
            char_key = resolve_character_key(char_name) if game == "sf6" else mk1_module.resolve_character_key(char_name)
        if not char_key:
            await interaction.response.send_message(
                f"No {combo_data_module.game_label(game)} combos found for {char_name} with those filters."
            )
            return
        nav, _details = combo_data_module.combo_entry_nav(
            game,
            char_key,
            group=payload.get("group"),
            rows=payload.get("rows"),
        )
        if nav == "empty":
            await interaction.response.send_message(
                f"No {combo_data_module.game_label(game)} combos found for {char_name} with those filters."
            )
            return
        sent_ids = await combo_data_module.send_combo_entry(
            interaction,
            game,
            char_key,
            payload,
            owner_id=interaction.user.id,
            back_to="game_menu",
        )
        if not sent_ids:
            await interaction.response.send_message(
                f"No {combo_data_module.game_label(game)} combos found for {char_name} with those filters."
            )

    @tree.command(name="sfstatsmenu", description="Open the SF Stats frame data menu")
    async def sfstats_menu_slash_command(interaction: discord.Interaction):
        await interaction.response.send_message(view=menu_system.MainMenuView(interaction.user.id))

    @tree.command(name="readme", description="Show a quick guide to SF Stats")
    async def readme_slash_command(interaction: discord.Interaction):
        from bubbot.features.failed_prompt_report import (
            attach_failed_prompt_report_button,
            build_report_context_from_message,
            stamp_report_context_on_sent,
        )

        view = CardView().set_card(*menu_system.readme_blocks())
        report_context = build_report_context_from_message(
            None,
            bub_response_text="SF Stats Readme",
            failure_reason="readme_response",
            prompt="/readme",
        )
        attach_failed_prompt_report_button(
            view,
            None,
            bub_response_text="SF Stats Readme",
            failure_reason="readme_response",
            report_context=report_context,
        )
        await interaction.response.send_message(view=view)
        try:
            sent = await interaction.original_response()
            stamp_report_context_on_sent(view, sent, client=client)
        except Exception:
            pass

    @tree.command(name="glossary", description="Open Infil's Fighting Game Glossary")
    @discord.app_commands.describe(term="Optional glossary term, like safe jump or option select")
    async def glossary_slash_command(interaction: discord.Interaction, term: str = None):
        from bubbot.features.failed_prompt_report import stamp_report_context_on_sent

        prompt = f"/glossary {term}".strip() if term else "/glossary"
        view = build_glossary_link_view(term, prompt=prompt)
        await interaction.response.send_message(embed=build_glossary_definition_embed(term), view=view)
        try:
            sent = await interaction.original_response()
            stamp_report_context_on_sent(view, sent, client=client)
        except Exception:
            pass

    @tree.command(name="ggst")
    @discord.app_commands.describe(char_name="The characters name", move_name="The move name", char_state="Optional char specific states like Installs.")
    async def ggst(interaction: discord.Interaction, char_name: str, move_name: str, char_state: str = None):
        return await send_ggst_slash_frame(interaction, char_name, move_name, char_state)

    @tree.command(name="sfv")
    @discord.app_commands.describe(char_name="The character name", move_name="The move name or input", char_state="Optional V-Trigger state: vt1 or vt2")
    async def sfv(interaction: discord.Interaction, char_name: str, move_name: str, char_state: str = None):
        return await send_sfv_slash_frame(interaction, char_name, move_name, char_state)

    @tree.command(name="2xko")
    @discord.app_commands.describe(char_name="The champion name", move_name="The move name or input")
    async def tuco(interaction: discord.Interaction, char_name: str, move_name: str):
        return await send_tuco_slash_frame(interaction, char_name, move_name)

    @tree.command(name="bbcf")
    @discord.app_commands.describe(char_name="The character name", move_name="The move name or input")
    async def bbcf(interaction: discord.Interaction, char_name: str, move_name: str):
        return await send_bbcf_slash_frame(interaction, char_name, move_name)

    @tree.command(name="ggacr")
    @discord.app_commands.describe(char_name="The character name", move_name="The move name or input")
    async def ggacr(interaction: discord.Interaction, char_name: str, move_name: str):
        return await send_ggacr_slash_frame(interaction, char_name, move_name)

    @tree.command(name="cotw")
    @discord.app_commands.describe(char_name="The character name", move_name="The move name or input")
    async def cotw(interaction: discord.Interaction, char_name: str, move_name: str):
        return await send_cotw_slash_frame(interaction, char_name, move_name)

    @tree.command(name="third-strike")
    @discord.app_commands.describe(char_name="The character name", move_name="The move name or input")
    async def third_strike(interaction: discord.Interaction, char_name: str, move_name: str):
        return await send_third_strike_slash_frame(interaction, char_name, move_name)

    @tree.command(name="usf4")
    @discord.app_commands.describe(char_name="The character name", move_name="The move name or input")
    async def usf4(interaction: discord.Interaction, char_name: str, move_name: str):
        return await send_usfiv_slash_frame(interaction, char_name, move_name)

    @tree.command(name="mk1")
    @discord.app_commands.describe(char_name="The character or kameo name", move_name="The move name or input")
    async def mk1(interaction: discord.Interaction, char_name: str, move_name: str):
        return await send_mk1_slash_frame(interaction, char_name, move_name)

    @tree.command(name="avtl")
    @discord.app_commands.describe(char_name="The character name", move_name="The move name or input")
    async def avtl(interaction: discord.Interaction, char_name: str, move_name: str):
        return await send_avtl_slash_frame(interaction, char_name, move_name)

    @tree.command(name="mk1-combos")
    @discord.app_commands.describe(
        char_name="The character name",
        group="Optional starter string or section filter",
        difficulty="Optional difficulty filter: easy, medium, or hard",
        position="Optional position filter: midscreen or corner",
    )
    async def mk1_combos(interaction: discord.Interaction, char_name: str, group: str = None, difficulty: str = None, position: str = None):
        return await send_slash_combos(interaction, "mk1", char_name, difficulty, position, group)

    @tree.command(name="sf6-combos")
    @discord.app_commands.describe(
        char_name="The character name",
        group="Optional starter or section filter",
        difficulty="Optional difficulty filter: easy, medium, or hard",
        position="Optional position filter: midscreen or corner",
    )
    async def sf6_combos(interaction: discord.Interaction, char_name: str, group: str = None, difficulty: str = None, position: str = None):
        return await send_slash_combos(interaction, "sf6", char_name, difficulty, position, group)

    @tree.command(name="sf6")
    @discord.app_commands.describe(char_name="The characters name", move_name="The move name", char_state="Optional char specific states like Installs.")
    async def sf6(interaction: discord.Interaction, char_name: str, move_name: str, char_state: str = None):
        return await send_sf6_slash_frame(interaction, char_name, move_name, char_state)

    @tree.command(name="sf6-stats", description="Show SF6 character stats (dash, jump, health, throw, drive rush, etc.)")
    @discord.app_commands.describe(
        char_name="The character name",
        stat="Optional stat filter (e.g. All stats, 66 — Forward dash, Health, Drive rush (dr))",
    )
    async def sf6_stats(interaction: discord.Interaction, char_name: str, stat: str = None):
        return await send_sf6_slash_stats(interaction, char_name, stat)

    @sf6_stats.autocomplete("char_name")
    async def sf6_stats_char_autocomplete(interaction: discord.Interaction, current: str):
        return slash_choices(autocomplete_values(current, sf6_character_choice_values()))

    @sf6_stats.autocomplete("stat")
    async def sf6_stats_stat_autocomplete(interaction: discord.Interaction, current: str):
        return slash_choices(autocomplete_values(current, sf6_stat_slash_autocomplete_values()))

    @sf6.autocomplete("char_state")
    async def sf6_char_state_autocomplete(interaction: discord.Interaction, current: str):
        if not interaction.namespace.char_name:
            return slash_choices([])
        return slash_choices(autocomplete_values(current, sf6_char_state_choice_values(interaction.namespace.char_name)))

    @sf6.autocomplete("char_name")
    async def sf6_char_autocomplete(interaction: discord.Interaction, current: str):
        return slash_choices(autocomplete_values(current, sf6_character_choice_values()))

    @sf6.autocomplete("move_name")
    async def sf6_move_autocomplete(interaction: discord.Interaction, current: str):
        if not interaction.namespace.char_name:
            return slash_choices([])
        return slash_choices(autocomplete_values(current, sf6_move_choice_values(interaction.namespace.char_name)))

    @ggst.autocomplete("char_name")
    async def ggst_char_autocomplete(interaction: discord.Interaction, current: str):
        return slash_choices(autocomplete_values(current, ggst_character_choice_values()))

    @ggst.autocomplete("char_state")
    async def ggst_char_state_autocomplete(interaction: discord.Interaction, current: str):
        if not interaction.namespace.char_name:
            return slash_choices([])
        return slash_choices(autocomplete_values(current, ggst_char_state_choice_values(interaction.namespace.char_name)))

    @ggst.autocomplete("move_name")
    async def ggst_move_autocomplete(interaction: discord.Interaction, current: str):
        if not interaction.namespace.char_name:
            return slash_choices([])
        return slash_choices(autocomplete_values(current, ggst_move_choice_values(interaction.namespace.char_name)))

    @sfv.autocomplete("char_name")
    async def sfv_char_autocomplete(interaction: discord.Interaction, current: str):
        return slash_choices(autocomplete_values(current, sfv_character_choice_values()))

    @sfv.autocomplete("char_state")
    async def sfv_char_state_autocomplete(interaction: discord.Interaction, current: str):
        if not interaction.namespace.char_name:
            return slash_choices([])
        return slash_choices(autocomplete_values(current, sfv_char_state_choice_values(interaction.namespace.char_name)))

    @sfv.autocomplete("move_name")
    async def sfv_move_autocomplete(interaction: discord.Interaction, current: str):
        if not interaction.namespace.char_name:
            return slash_choices([])
        return slash_choices(autocomplete_values(current, sfv_move_choice_values(interaction.namespace.char_name)))

    @tuco.autocomplete("char_name")
    async def tuco_char_autocomplete(interaction: discord.Interaction, current: str):
        return slash_choices(autocomplete_values(current, tuco_character_choice_values()))

    @tuco.autocomplete("move_name")
    async def tuco_move_autocomplete(interaction: discord.Interaction, current: str):
        if not interaction.namespace.char_name:
            return slash_choices([])
        return slash_choices(autocomplete_values(current, tuco_move_choice_values(interaction.namespace.char_name)))

    @bbcf.autocomplete("char_name")
    async def bbcf_char_autocomplete(interaction: discord.Interaction, current: str):
        return slash_choices(autocomplete_values(current, bbcf_character_choice_values()))

    @bbcf.autocomplete("move_name")
    async def bbcf_move_autocomplete(interaction: discord.Interaction, current: str):
        if not interaction.namespace.char_name:
            return slash_choices([])
        return slash_choices(autocomplete_values(current, bbcf_move_choice_values(interaction.namespace.char_name)))

    @ggacr.autocomplete("char_name")
    async def ggacr_char_autocomplete(interaction: discord.Interaction, current: str):
        return slash_choices(autocomplete_values(current, ggacr_character_choice_values()))

    @ggacr.autocomplete("move_name")
    async def ggacr_move_autocomplete(interaction: discord.Interaction, current: str):
        if not interaction.namespace.char_name:
            return slash_choices([])
        return slash_choices(autocomplete_values(current, ggacr_move_choice_values(interaction.namespace.char_name)))

    @cotw.autocomplete("char_name")
    async def cotw_char_autocomplete(interaction: discord.Interaction, current: str):
        return slash_choices(autocomplete_values(current, cotw_character_choice_values()))

    @cotw.autocomplete("move_name")
    async def cotw_move_autocomplete(interaction: discord.Interaction, current: str):
        if not interaction.namespace.char_name:
            return slash_choices([])
        return slash_choices(autocomplete_values(current, cotw_move_choice_values(interaction.namespace.char_name)))

    @third_strike.autocomplete("char_name")
    async def third_strike_char_autocomplete(interaction: discord.Interaction, current: str):
        return slash_choices(autocomplete_values(current, third_strike_character_choice_values()))

    @third_strike.autocomplete("move_name")
    async def third_strike_move_autocomplete(interaction: discord.Interaction, current: str):
        if not interaction.namespace.char_name:
            return slash_choices([])
        return slash_choices(autocomplete_values(current, third_strike_move_choice_values(interaction.namespace.char_name)))

    @usf4.autocomplete("char_name")
    async def usf4_char_autocomplete(interaction: discord.Interaction, current: str):
        return slash_choices(autocomplete_values(current, usfiv_character_choice_values()))

    @usf4.autocomplete("move_name")
    async def usf4_move_autocomplete(interaction: discord.Interaction, current: str):
        if not interaction.namespace.char_name:
            return slash_choices([])
        return slash_choices(autocomplete_values(current, usfiv_move_choice_values(interaction.namespace.char_name)))

    @mk1.autocomplete("char_name")
    async def mk1_char_autocomplete(interaction: discord.Interaction, current: str):
        return slash_choices(autocomplete_values(current, mk1_character_choice_values()))

    @mk1.autocomplete("move_name")
    async def mk1_move_autocomplete(interaction: discord.Interaction, current: str):
        if not interaction.namespace.char_name:
            return slash_choices([])
        return slash_choices(autocomplete_values(current, mk1_move_choice_values(interaction.namespace.char_name)))

    @avtl.autocomplete("char_name")
    async def avtl_char_autocomplete(interaction: discord.Interaction, current: str):
        return slash_choices(autocomplete_values(current, avtl_character_choice_values()))

    @avtl.autocomplete("move_name")
    async def avtl_move_autocomplete(interaction: discord.Interaction, current: str):
        if not interaction.namespace.char_name:
            return slash_choices([])
        return slash_choices(autocomplete_values(current, avtl_move_choice_values(interaction.namespace.char_name)))

    @mk1_combos.autocomplete("char_name")
    async def mk1_combo_char_autocomplete(interaction: discord.Interaction, current: str):
        return slash_choices(autocomplete_values(current, combo_character_choice_values("mk1")))

    @mk1_combos.autocomplete("group")
    async def mk1_combo_group_autocomplete(interaction: discord.Interaction, current: str):
        if not interaction.namespace.char_name:
            return slash_choices([])
        return slash_choices(autocomplete_values(current, combo_group_choice_values("mk1", interaction.namespace.char_name)))

    @sf6_combos.autocomplete("char_name")
    async def sf6_combo_char_autocomplete(interaction: discord.Interaction, current: str):
        return slash_choices(autocomplete_values(current, combo_character_choice_values("sf6")))

    @sf6_combos.autocomplete("group")
    async def sf6_combo_group_autocomplete(interaction: discord.Interaction, current: str):
        if not interaction.namespace.char_name:
            return slash_choices([])
        return slash_choices(autocomplete_values(current, combo_group_choice_values("sf6", interaction.namespace.char_name)))

    @sf6_combos.autocomplete("difficulty")
    async def sf6_combo_difficulty_autocomplete(interaction: discord.Interaction, current: str):
        return slash_choices(autocomplete_values(current, ["easy", "medium", "hard"]))

    @sf6_combos.autocomplete("position")
    async def sf6_combo_position_autocomplete(interaction: discord.Interaction, current: str):
        return slash_choices(autocomplete_values(current, ["midscreen", "corner"]))

    @mk1_combos.autocomplete("difficulty")
    async def mk1_combo_difficulty_autocomplete(interaction: discord.Interaction, current: str):
        return slash_choices(autocomplete_values(current, ["easy", "medium", "hard"]))

    @mk1_combos.autocomplete("position")
    async def mk1_combo_position_autocomplete(interaction: discord.Interaction, current: str):
        return slash_choices(autocomplete_values(current, ["midscreen", "corner"]))

async def sync_public_slash_commands(client, tree):
    """Sync Bub's commands, then the SF Stats test-guild commands (after guild copies are cleared)."""
    synced = await _sync_bub_slash_commands(client, tree)
    application_id = getattr(client, "application_id", None) or getattr(getattr(client, "user", None), "id", None)
    await sfstats_bridge.sync_guild_commands(application_id, os.getenv("DISCORD_TOKEN"))
    return synced


async def _sync_bub_slash_commands(client, tree):
    """
    Sync global slash commands and clear stale guild-scoped copies.

    Duplicate /command entries in Discord usually mean the same commands were
    synced globally and to a guild during an earlier deploy, not duplicate
    registrations in our CommandTree.
    """
    local_commands = list(tree.get_commands())
    local_names = [cmd.name for cmd in local_commands]
    if len(local_names) != len(set(local_names)):
        print(f"[menu] WARNING: duplicate slash names in local tree: {local_names}", flush=True)

    guild_ids_to_clear = set()
    channel_id = os.getenv("CHANNEL_ID")
    if channel_id:
        try:
            channel = client.get_channel(int(channel_id))
            if channel and getattr(channel, "guild", None):
                guild_ids_to_clear.add(int(channel.guild.id))
        except (TypeError, ValueError):
            pass
    for env_name in ("DISCORD_GUILD_ID", "GUILD_ID"):
        raw_guild_id = os.getenv(env_name)
        if not raw_guild_id:
            continue
        try:
            guild_ids_to_clear.add(int(raw_guild_id))
        except ValueError:
            print(f"[menu] Ignoring invalid {env_name}={raw_guild_id!r}", flush=True)

    for guild_id in sorted(guild_ids_to_clear):
        guild_obj = discord.Object(id=guild_id)
        tree.clear_commands(guild=guild_obj)
        await tree.sync(guild=guild_obj)
        print(f"[menu] Cleared guild-scoped slash commands for guild {guild_id}", flush=True)

    include_activity = _activity_entry_enabled()
    raw_synced = await _bulk_sync_global_commands_with_activity_entry(client, tree, local_commands, include_activity)
    if raw_synced is None and include_activity:
        # An app without Activities rejects the launcher, and with it the whole list: retry without it.
        raw_synced = await _bulk_sync_global_commands_with_activity_entry(client, tree, local_commands, False)
    if raw_synced is not None:
        synced_names = sorted(
            f"{command.get('name')}" + (" (activity)" if int(command.get("type", 1) or 1) == 4 else "")
            for command in raw_synced
            if command.get("name")
        )
        print(
            f"[menu] Global slash/activity commands synced ({len(synced_names)}): "
            + ", ".join(f"/{name}" for name in synced_names),
            flush=True,
        )
        return raw_synced

    if sfstats_bridge.enabled() and sfstats_bridge.CONFIG.global_commands:
        # tree.sync() would replace the global list with Bub's commands alone and delete SF Stats'.
        print("[menu] Global command sync failed; leaving the registered commands as they are", flush=True)
        return None
    await _delete_streetfighterdle_activity_entry_command(client)
    synced = await tree.sync()
    synced_names = sorted(cmd.name for cmd in synced)
    print(
        f"[menu] Global slash commands synced ({len(synced_names)}): "
        + ", ".join(f"/{name}" for name in synced_names),
        flush=True,
    )
    if len(synced_names) != len(set(synced_names)):
        print("[menu] WARNING: Discord sync returned duplicate command names", flush=True)
    if include_activity:
        await _register_streetfighterdle_activity_entry_command(client)
    return synced
