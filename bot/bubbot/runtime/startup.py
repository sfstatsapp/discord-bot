"""Discord on_ready bootstrap: static assets, slash sync, schedulers, data load, module configure.
Called from message_router once the bot connects; deps dict carries injected callables and state.
Returns the reminder loop task handle when created or reused."""

async def handle_ready(deps):
    import asyncio as _asyncio
    import functools as _functools

    client = deps["client"]
    tree = deps["tree"]
    reminder_manager = deps["reminder_manager"]
    buenavista_extension = deps["buenavista_extension"]
    load_frame_data = deps["load_frame_data"]
    configure_extracted_modules = deps["configure_extracted_modules"]
    quiz_module = deps["quiz_module"]
    menu_system = deps["menu_system"]
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
    from bubbot.data.ggacr_aliases import GGACR_GAME_TERMS

    # The SF Stats frame-data helper (bubbot.render_service) never logs in: only the game data loads.
    frame_only = deps.get("frame_only", False)
    reminder_task_handle = deps.get("reminder_task_handle")
    if not frame_only:
        print(f"Logged in as {client.user}")
        try:
            from bubbot.runtime.static_assets import start_static_asset_server

            await start_static_asset_server()
        except Exception as error:
            print(f"[static-assets] startup error: {error}", flush=True)
        try:
            from bubbot.runtime.slash_commands import sync_public_slash_commands

            await sync_public_slash_commands(client, tree)
        except Exception as error:
            print(f"[menu] Global slash command sync error: {error}", flush=True)

        await buenavista_extension.start_background_tasks(client=client)

        if reminder_task_handle is None or reminder_task_handle.done():
            reminder_task_handle = _asyncio.create_task(reminder_manager.reminder_loop())
            print("Reminder loop task created.", flush=True)

        print("[runtime] Public framedata startup complete; private schedulers are owned by Buenavista.", flush=True)

    def _load_all_game_data():
        load_frame_data()
        sfv_module.load_frame_data()
        ggst_module.load_frame_data()
        tuco_module.load_frame_data()
        bbcf_module.load_frame_data()
        ggacr_module.load_frame_data()
        cotw_module.load_frame_data()
        third_strike_module.load_frame_data()
        usfiv_module.load_frame_data()
        mk1_module.load_frame_data()
        avtl_module.load_frame_data()
        configure_extracted_modules()
        combo_data_module.load_combo_data()

    loop = _asyncio.get_running_loop()
    await loop.run_in_executor(None, _load_all_game_data)

    frame_data = deps["FRAME_DATA"]
    character_aliases = deps["CHARACTER_ALIASES"]
    resolve_character_key = deps["resolve_character_key"]
    normalize_char_name = deps["normalize_char_name"]
    lookup_frame_data = deps["lookup_frame_data"]
    find_moves_in_text = deps["find_moves_in_text"]
    is_missing_attack_range_value = deps["is_missing_attack_range_value"]
    clean_embed_value = deps["clean_embed_value"]
    truncate_embed_value = deps["truncate_embed_value"]
    build_frame_embed = deps["build_frame_embed"]
    strip_discord_mentions = deps["strip_discord_mentions"]
    send_frame_embeds_with_views = deps["send_frame_embeds_with_views"]

    quiz_module.configure(
        FRAME_DATA=frame_data,
        CHARACTER_ALIASES=character_aliases,
        GAME_QUIZ_CONFIGS={
            "sf6": {
                "label": "Street Fighter 6",
                "data": frame_data,
                "aliases": character_aliases,
                "resolve_character_key": resolve_character_key,
                "lookup_frame_data": lookup_frame_data,
                "find_moves_in_text": find_moves_in_text,
                "build_frame_embed": build_frame_embed,
                "get_notes_text": frame_output_module.get_notes_text,
                "game_terms": ("sf6", "street fighter 6"),
            },
            "ggst": {
                "label": "Guilty Gear Strive",
                "data": ggst_module.GGST_FRAME_DATA,
                "aliases": ggst_module.GGST_CHARACTER_ALIASES,
                "resolve_character_key": ggst_module.resolve_character_key,
                "find_moves_in_text": ggst_module.find_moves_in_text,
                "build_frame_embed": ggst_module.build_frame_embed,
                "get_notes_text": ggst_module.get_notes_text,
                "game_terms": ("ggst", "guilty gear strive"),
            },
            "sfv": {
                "label": "Street Fighter V",
                "data": {
                    char_key: list(rows or []) + [trigger_row for state_rows in (sfv_module.SFV_TRIGGER_FRAME_DATA.get(char_key, {}) or {}).values() for trigger_row in state_rows]
                    for char_key, rows in sfv_module.SFV_FRAME_DATA.items()
                },
                "aliases": sfv_module.SFV_CHARACTER_ALIASES,
                "resolve_character_key": sfv_module.resolve_character_key,
                "find_moves_in_text": sfv_module.find_moves_in_text,
                "build_frame_embed": sfv_module.build_frame_embed,
                "get_notes_text": sfv_module.get_notes_text,
                "game_terms": ("sfv", "street fighter v"),
            },
            "tuco": {
                "label": "2XKO",
                "data": tuco_module.TUCO_FRAME_DATA,
                "aliases": tuco_module.TUCO_CHARACTER_ALIASES,
                "resolve_character_key": tuco_module.resolve_character_key,
                "find_moves_in_text": tuco_module.find_moves_in_text,
                "build_frame_embed": tuco_module.build_frame_embed,
                "get_notes_text": tuco_module.get_notes_text,
                "game_terms": ("2xko", "tuco"),
            },
            "bbcf": {
                "label": "BlazBlue Central Fiction",
                "data": bbcf_module.BBCF_FRAME_DATA,
                "aliases": bbcf_module.BBCF_CHARACTER_ALIASES,
                "resolve_character_key": bbcf_module.resolve_character_key,
                "find_moves_in_text": bbcf_module.find_moves_in_text,
                "build_frame_embed": bbcf_module.build_frame_embed,
                "get_notes_text": bbcf_module.get_notes_text,
                "game_terms": ("bbcf", "blazblue central fiction"),
            },
            "ggacr": {
                "label": "Guilty Gear Accent Core Plus R",
                "data": ggacr_module.GGACR_FRAME_DATA,
                "aliases": ggacr_module.GGACR_CHARACTER_ALIASES,
                "resolve_character_key": ggacr_module.resolve_character_key,
                "find_moves_in_text": ggacr_module.find_moves_in_text,
                "build_frame_embed": ggacr_module.build_frame_embed,
                "get_notes_text": ggacr_module.get_notes_text,
                "game_terms": GGACR_GAME_TERMS,
            },
            "cotw": {
                "label": "City of the Wolves",
                "data": cotw_module.COTW_FRAME_DATA,
                "aliases": cotw_module.COTW_CHARACTER_ALIASES,
                "resolve_character_key": cotw_module.resolve_character_key,
                "find_moves_in_text": cotw_module.find_moves_in_text,
                "build_frame_embed": cotw_module.build_frame_embed,
                "get_notes_text": cotw_module.get_notes_text,
                "game_terms": ("cotw", "city of the wolves"),
            },
            "third_strike": {
                "label": "Third Strike",
                "data": third_strike_module.THIRD_STRIKE_FRAME_DATA,
                "aliases": third_strike_module.THIRD_STRIKE_CHARACTER_ALIASES,
                "resolve_character_key": third_strike_module.resolve_character_key,
                "find_moves_in_text": third_strike_module.find_moves_in_text,
                "build_frame_embed": third_strike_module.build_frame_embed,
                "get_notes_text": third_strike_module.get_notes_text,
                "game_terms": ("3s", "third strike"),
            },
            "usf4": {
                "label": "Ultra Street Fighter IV",
                "data": usfiv_module.USFIV_FRAME_DATA,
                "aliases": usfiv_module.USFIV_CHARACTER_ALIASES,
                "resolve_character_key": usfiv_module.resolve_character_key,
                "find_moves_in_text": usfiv_module.find_moves_in_text,
                "build_frame_embed": usfiv_module.build_frame_embed,
                "get_notes_text": usfiv_module.get_notes_text,
                "game_terms": ("usf4", "ultra street fighter iv"),
            },
            "mk1": {
                "label": "Mortal Kombat 1",
                "data": mk1_module.MK1_FRAME_DATA,
                "aliases": mk1_module.MK1_CHARACTER_ALIASES,
                "resolve_character_key": mk1_module.resolve_character_key,
                "find_moves_in_text": mk1_module.find_moves_in_text,
                "build_frame_embed": mk1_module.build_frame_embed,
                "get_notes_text": mk1_module.get_notes_text,
                "game_terms": ("mk1", "mortal kombat 1"),
            },
            "avtl": {
                "label": "Avatar Legends",
                "data": avtl_module.AVTL_FRAME_DATA,
                "aliases": avtl_module.AVTL_CHARACTER_ALIASES,
                "resolve_character_key": avtl_module.resolve_character_key,
                "find_moves_in_text": avtl_module.find_moves_in_text,
                "build_frame_embed": avtl_module.build_frame_embed,
                "get_notes_text": avtl_module.get_notes_text,
                "game_terms": ("avtl", "avatar legends"),
            },
        },
        resolve_character_key=resolve_character_key,
        normalize_char_name=normalize_char_name,
        lookup_frame_data=lookup_frame_data,
        find_moves_in_text=find_moves_in_text,
        is_missing_attack_range_value=is_missing_attack_range_value,
        clean_embed_value=clean_embed_value,
        truncate_embed_value=truncate_embed_value,
        build_frame_embed=build_frame_embed,
        strip_discord_mentions=strip_discord_mentions,
    )
    quiz_module.load_quiz_leaderboard()
    print(f"[quiz] global leaderboard loaded entries={len(quiz_module.QUIZ_GLOBAL_LEADERBOARD)}", flush=True)

    menu_system.configure(
        frame_data=frame_data,
        frame_stats=deps.get("FRAME_STATS") or {},
        character_aliases=character_aliases,
        ggst_frame_data=ggst_module.GGST_FRAME_DATA,
        ggst_character_aliases=ggst_module.GGST_CHARACTER_ALIASES,
        ggst_supplemental_frame_data=ggst_module.GGST_SUPPLEMENTAL_FRAME_DATA,
        ggst_state_frame_data=ggst_module.GGST_STATE_FRAME_DATA,
        sfv_frame_data=sfv_module.SFV_FRAME_DATA,
        sfv_character_aliases=sfv_module.SFV_CHARACTER_ALIASES,
        sfv_trigger_frame_data=sfv_module.SFV_TRIGGER_FRAME_DATA,
        tuco_frame_data=tuco_module.TUCO_FRAME_DATA,
        tuco_character_aliases=tuco_module.TUCO_CHARACTER_ALIASES,
        bbcf_frame_data=bbcf_module.BBCF_FRAME_DATA,
        bbcf_character_aliases=bbcf_module.BBCF_CHARACTER_ALIASES,
        ggacr_frame_data=ggacr_module.GGACR_FRAME_DATA,
        ggacr_character_aliases=ggacr_module.GGACR_CHARACTER_ALIASES,
        cotw_frame_data=cotw_module.COTW_FRAME_DATA,
        cotw_character_aliases=cotw_module.COTW_CHARACTER_ALIASES,
        third_strike_frame_data=third_strike_module.THIRD_STRIKE_FRAME_DATA,
        third_strike_character_aliases=third_strike_module.THIRD_STRIKE_CHARACTER_ALIASES,
        usfiv_frame_data=usfiv_module.USFIV_FRAME_DATA,
        usfiv_character_aliases=usfiv_module.USFIV_CHARACTER_ALIASES,
        mk1_frame_data=mk1_module.MK1_FRAME_DATA,
        mk1_character_aliases=mk1_module.MK1_CHARACTER_ALIASES,
        avtl_frame_data=avtl_module.AVTL_FRAME_DATA,
        avtl_character_aliases=avtl_module.AVTL_CHARACTER_ALIASES,
        quiz_module_ref=quiz_module,
        build_sf6_frame_embed_fn=build_frame_embed,
        build_ggst_frame_embed_fn=ggst_module.build_frame_embed,
        build_sfv_frame_embed_fn=sfv_module.build_frame_embed,
        build_tuco_frame_embed_fn=tuco_module.build_frame_embed,
        build_bbcf_frame_embed_fn=bbcf_module.build_frame_embed,
        build_ggacr_frame_embed_fn=ggacr_module.build_frame_embed,
        build_cotw_frame_embed_fn=cotw_module.build_frame_embed,
        build_third_strike_frame_embed_fn=third_strike_module.build_frame_embed,
        build_usfiv_frame_embed_fn=usfiv_module.build_frame_embed,
        build_avtl_frame_embed_fn=avtl_module.build_frame_embed,
        send_frame_embeds_with_views_fn=send_frame_embeds_with_views,
    )
    print("[menu] Menu system configured.", flush=True)

    return reminder_task_handle
