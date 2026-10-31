"""SF6 natural-language frame/gif query orchestration. Character-specific rules live in sf6_* helper modules."""

import difflib
import re
from bubbot.utils.frame_match_utils import prefer_grounded_rows

from bubbot.frame_data.sf6_cammy_followups import query_has_hooligan_followup
from bubbot.frame_data.sf6_character_aliases import (
    adjust_character_specific_keyword_inputs,
    apply_character_specific_result_filters,
    collect_character_followup_contexts,
    collect_character_specific_aliases,
    collect_late_character_specific_aliases,
    collect_character_specific_rows,
    create_character_filter_state,
    expand_character_candidate_names,
    filter_character_specific_final_results,
    infer_character_mentions_from_terms,
    jamie_drink_level_from_text,
    query_requires_character_variant_state,
    row_matches_character_variant_state,
    should_append_single_token_candidate,
    should_skip_keyword_input,
)
from bubbot.frame_data.sf6_character_stats import apply_stats_context, match_stat_keys_in_text
from bubbot.frame_data.sf6_stance import apply_stance_rows
from bubbot.frame_data.sf6_parser_helpers import collect_normal_notation_inputs
from bubbot.frame_data.sf6_parser_helpers import normalize_button_word_notation
from bubbot.frame_data.sf6_parser_helpers import normalize_charge_button_notation
from bubbot.frame_data.sf6_parser_helpers import normalize_charge_up_motion_notation
from bubbot.frame_data.sf6_parser_helpers import normalize_direction_word_motion_notation
from bubbot.frame_data.sf6_parser_helpers import query_has_directional_normal_notation
from bubbot.frame_data.sf6_parser_helpers import query_has_charge_button_notation
from bubbot.frame_data.sf6_parser_helpers import query_has_grounded_normal_notation
from bubbot.frame_data.sf6_parser_helpers import query_mentions_loaded_move_name
from bubbot.frame_data.sf6_special_prompt_rules import (
    choose_character_special_variant,
    should_skip_ambiguous_special_key,
    should_skip_special_prompt_base,
)
from bubbot.utils.character_lookup import find_fuzzy_aliases_in_text
from bubbot.utils.notation_match_utils import query_has_jump_motion_notation
from bubbot.utils.text_utils import normalize_query_terms


def find_moves_in_text(deps, text):
    CHARACTER_ALIASES = deps["CHARACTER_ALIASES"]
    FRAME_DATA = deps["FRAME_DATA"]
    FRAME_STATS = deps["FRAME_STATS"]
    strip_discord_mentions = deps["strip_discord_mentions"]
    normalize_jump_normal_text = deps["normalize_jump_normal_text"]
    word_tokens = deps["word_tokens"]
    contains_token_sequence = deps["contains_token_sequence"]
    has_explicit_gif_lookup_intent = deps["has_explicit_gif_lookup_intent"]
    lookup_frame_data = deps["lookup_frame_data"]
    normalize_char_name = deps["normalize_char_name"]
    resolve_character_key = deps["resolve_character_key"]
    normalize_num_cmd_token = deps["normalize_num_cmd_token"]
    is_missing_attack_range_value = deps["is_missing_attack_range_value"]
    get_attack_range_details = deps["get_attack_range_details"]
    format_attack_range_for_table = deps["format_attack_range_for_table"]
    format_frame_data = deps["format_frame_data"]
    check_punish = deps["check_punish"]
    """Extract character/move mentions and return context payload with mode."""
    found_data = []
    text_lower = normalize_query_terms(strip_discord_mentions(text).lower())
    # Shared typo normalization rewrites "stats" to "start"; SF6 has no bare
    # "start" move wording, so restore it for the stats-aware matching below.
    text_lower = re.sub(r"\bstart\b", "stats", text_lower)
    charge_button_query = query_has_charge_button_notation(text_lower)
    text_lower = normalize_charge_button_notation(text_lower)
    text_lower = normalize_charge_up_motion_notation(text_lower)
    text_lower = normalize_jump_normal_text(text_lower)
    text_lower = normalize_button_word_notation(text_lower)
    text_lower = normalize_direction_word_motion_notation(text_lower)
    text_lower = re.sub(r"\bdivekick\b", "dive kick", text_lower)
    tc_prompt_blocks = []
    special_prompt_blocks = []
    allow_explicit_special_prompt = False
    tc_ambiguous_inputs = set()
    text_tokens = word_tokens(text_lower)

    def build_sf6_disambiguation_prompt(option_type, char_name, detail, option_lines):
        return "\n".join(
            [
                f"Multiple SF6 moves match {char_name}. Reply with the option number:",
                f"**{option_type} ({char_name})**",
                f"{detail}:",
                *option_lines,
            ]
        )

    def format_move_options(rows):
        return [
            f"{index}. {row.get('moveName', '?')}: `{row.get('numCmd', '?')}`"
            for index, row in enumerate(rows, start=1)
        ]

    def tokens_in_haystack(haystack_tokens, needle_tokens):
        return contains_token_sequence(haystack_tokens, needle_tokens)

    def tokens_in_text(needle_tokens):
        return tokens_in_haystack(text_tokens, needle_tokens)

    # 1. Identify exact, compact, or clearly misspelled character names.
    mentioned_chars = find_fuzzy_aliases_in_text(text_lower, CHARACTER_ALIASES, FRAME_DATA.keys())

    infer_character_mentions_from_terms(text_lower, FRAME_DATA, mentioned_chars)
    jamie_drink_level = jamie_drink_level_from_text(text_lower) if "jamie" in mentioned_chars else None

    move_query_stop_tokens = {
        "frame", "frames", "framedata", "data", "startup", "recovery", "active",
        "on", "hit", "block", "compare", "comparison", "versus", "vs", "which",
        "is", "faster", "better", "tc", "target", "combo", "combos", "how", "fast",
        "quick", "speed", "of", "the", "a", "an", "for", "with", "please", "show",
        "tell", "me", "about", "can", "i", "punish", "punishable", "stats",
        "send", "post", "drop", "give", "link", "gif", "gifs", "hitbox", "hitboxes",
        "advantage", "counter", "pc", "ch", "range", "length", "super", "art",
        "level", "lvl", "lv", "sa1", "sa2", "sa3", "lv1", "lv2", "lv3",
        "lvl1", "lvl2", "lvl3",
    }

    def remove_token_sequence(tokens, sequence):
        sequence_length = len(sequence)
        for index in range(len(tokens) - sequence_length + 1):
            if tokens[index:index + sequence_length] == sequence:
                return tokens[:index] + tokens[index + sequence_length:]
        return tokens

    def residual_move_candidate(char):
        residual_tokens = [token for token in text_tokens if token not in move_query_stop_tokens]
        character_names = [str(char)]
        character_names.extend(
            alias
            for alias, canonical in CHARACTER_ALIASES.items()
            if normalize_char_name(canonical) == normalize_char_name(char)
        )
        name_sequences = sorted(
            (re.findall(r"[a-z0-9]+", name.lower()) for name in character_names),
            key=len,
            reverse=True,
        )
        for sequence in name_sequences:
            if sequence:
                residual_tokens = remove_token_sequence(residual_tokens, sequence)
        return " ".join(residual_tokens).strip(), residual_tokens

    if "jamie" in mentioned_chars:
        move_query_stop_tokens.update(
            {
                "drink",
                "drinks",
                *(f"d{level}" for level in range(0, 5)),
                *(f"dl{level}" for level in range(0, 5)),
            }
        )
        if jamie_drink_level is not None:
            move_query_stop_tokens.add(str(jamie_drink_level))

    frame_keywords = [
        "frame data",
        "framedata",
        "startup",
        "start up",
        "recovery",
        "active",
        "on block",
        "on hit",
        "hitstun",
        "blockstun",
        "frames",
    ]
    gif_query = has_explicit_gif_lookup_intent(text_lower)
    air_throw_move_query = bool(
        re.search(r"\b(?:air|aerial)\s+throw\b", text_lower)
        or re.search(r"\bairthrows?\b", text_lower)
    )
    jump_normal_move_query = bool(
        re.search(r"\b[789][lmh][pk]\b", text_lower)
        or re.search(r"\bj\.?[789]?[lmh][pk]\b", text_lower)
        or re.search(r"\b(?:neutral|n)\s+j(?:ump)?\s*\.?\s*[lmh][pk]\b", text_lower)
        or re.search(r"\bjump\s+[lmh][pk]\b", text_lower)
    )
    air_special_move_query = query_has_jump_motion_notation(text_lower)
    directional_normal_move_query = query_has_directional_normal_notation(text_lower)
    grounded_normal_move_query = query_has_grounded_normal_notation(text_lower)
    startup_alias_query = bool(
        re.search(r"\bhow\s+fast\b", text_lower)
        or re.search(r"\bhow\s+quick\b", text_lower)
        or re.search(r"\bspeed\s+of\b", text_lower)
        or (
            re.search(r"\bfast\b", text_lower)
            and re.search(r"\b[1-9][0-9]*[a-zA-Z]{1,3}\b", text_lower)
        )
    )
    hitconfirm_alias_query = bool(
        re.search(r"\bhit\s*-?\s*confirm\b", text_lower)
        or re.search(r"\bhitconfirm\b", text_lower)
        or re.search(r"\bhc\b", text_lower)
        or re.search(r"\bconfirm\s+window\b", text_lower)
        or re.search(r"\bconfirm\s+timing\b", text_lower)
        or re.search(r"\bconfirmable\b", text_lower)
        or re.search(r"\bconfirm\b", text_lower)
    )
    super_gain_alias_query = bool(
        re.search(r"\bsuper\s*gain\b", text_lower)
        or re.search(r"\bsuper\s*meter\s*gain\b", text_lower)
        or re.search(r"\bmeter\s*gain\b", text_lower)
        or re.search(r"\bsuper\s*build\b", text_lower)
        or re.search(r"\bsa\s*gain\b", text_lower)
    )
    range_alias_query = bool(
        (
            re.search(r"\brange\b", text_lower)
            or re.search(r"\blength\b", text_lower)
        )
        and not re.search(r"\bin\s+range\b", text_lower)
    )
    property_alias_flags = {
        "startup": startup_alias_query
        or bool(re.search(r"\bstart\s*up\b|\bstartup\b", text_lower)),
        "active": bool(re.search(r"\bactive\b|\bactive\s+frames?\b", text_lower)),
        "recovery": bool(re.search(r"\brecovery\b", text_lower)),
        "total": bool(re.search(r"\btotal(?:\s+frames?)?\b", text_lower)),
        "on_hit": bool(re.search(r"\bon\s+hit\b", text_lower)),
        "on_block": bool(re.search(r"\bon\s+block\b|\bplus\s+on\s+block\b|\bminus\s+on\s+block\b", text_lower)),
        "cancel": bool(re.search(r"\bcancel(?:l?able)?\b", text_lower)),
        "damage": bool(re.search(r"\bdamage\b|\bdmg\b", text_lower)),
        "guard": bool(re.search(r"\bguard\b|\battack\s+level\b|\batk\s*lvl\b|\batk\s*level\b", text_lower)),
        "chip_damage": bool(re.search(r"\bchip\s+damage\b", text_lower)),
        "drive_damage": bool(re.search(r"\bdrive\s+(?:chip|dmg|damage)\b", text_lower)),
        "stun": bool(re.search(r"\bhitstun\b|\bblockstun\b|\bstun\b", text_lower)),
        "invuln": bool(re.search(r"\binvuln(?:erability)?\b|\binvul\b", text_lower)),
        "hitconfirm": hitconfirm_alias_query,
        "super_gain": super_gain_alias_query,
        "range": range_alias_query,
        "frame_advantage": bool(re.search(r"\bframe\s+adv(?:antage)?\b", text_lower)),
        "counter_hit_adv": bool(
            re.search(r"\bcounter\s*hit\s+adv(?:antage)?\b", text_lower)
            or re.search(r"\bch\s*adv\b", text_lower)
        ),
        "counter_hit": bool(
            (
                re.search(r"\bcounter\s*hit\b", text_lower)
                and not re.search(r"\bcounter\s*hit\s+adv(?:antage)?\b", text_lower)
            )
            or (
                re.search(r"\bch\b", text_lower)
                and not re.search(r"\bch\s*adv\b", text_lower)
            )
        ),
        "punish_counter": bool(
            re.search(r"\bpunish\s*counter\b", text_lower)
            or re.search(r"\bpc\b", text_lower)
        ),
    }
    if property_alias_flags.get("damage") and (
        property_alias_flags.get("chip_damage") or property_alias_flags.get("drive_damage")
    ):
        property_alias_flags["damage"] = False
    property_match_count = sum(1 for matched in property_alias_flags.values() if matched)
    table_intent_query = bool(
        re.search(r"\ball\s+frames?\b", text_lower)
        or re.search(r"\bfull\s+frames?\b", text_lower)
        or re.search(r"\bfull\s+frame\s*data\b", text_lower)
        or re.search(r"\btable\b", text_lower)
    )
    property_only_query = bool(property_match_count == 1 and not table_intent_query)
    comparison_keywords = [
        "which is better",
        "which is faster",
        "compare",
        "comparison",
        "versus",
    ]
    punish_keywords = ["punish", "punishable", "can i punish", "is it punishable"]
    target_combo_query = bool(re.search(r"\b(?:tc|target\s+combo|targetcombo|[1-9][0-9]*[a-z]{1,3}\s*(?:,|~|>|->)\s*(?:[1-9][0-9]*[a-z]{1,3}|[a-z]{1,3}))\b", text_lower))
    special_grab_query = bool(re.search(r"\b(command\s+grab|spd|piledriver|typhoon)\b", text_lower))
    super_level_query = bool(
        re.search(
            r"\b(?:sa\s*[123]|super\s*art\s*(?:level\s*)?[123]|super\s*[123]|lv\s*[123]|lvl\s*[123]|level\s*[123])\b",
            text_lower,
        )
    )
    named_move_query = any(
        query_mentions_loaded_move_name(
            residual_move_candidate(char)[1],
            FRAME_DATA.get(char, ()),
        )
        for char in mentioned_chars
    )
    stats_keys_in_query = match_stat_keys_in_text(text_lower)
    resolved_move_query = False
    resolved_query_rows = {}
    for char in mentioned_chars:
        residual_candidate, residual_tokens = residual_move_candidate(char)
        if not residual_candidate:
            continue
        if len(residual_tokens) == 1 and stats_keys_in_query:
            continue
        resolved_row = lookup_frame_data(char, residual_candidate)
        if resolved_row:
            resolved_query_rows[char] = resolved_row
            resolved_move_query = True
    wants_comparison = (
        any(kw in text_lower for kw in comparison_keywords)
        or re.search(r"\bvs\b", text_lower)
        or (len(mentioned_chars) >= 2 and re.search(r"\band\b", text_lower))
    )
    wants_frame_data = (
        any(kw in text_lower for kw in frame_keywords)
        or any(kw in text_lower for kw in punish_keywords)
        or wants_comparison
        or startup_alias_query
        or hitconfirm_alias_query
        or super_gain_alias_query
        or range_alias_query
        or property_match_count > 0
        or target_combo_query
        or super_level_query
        or gif_query
        or air_throw_move_query
        or jump_normal_move_query
        or air_special_move_query
        or directional_normal_move_query
        or grounded_normal_move_query
        or charge_button_query
        or named_move_query
        or resolved_move_query
    )
    results = []
    tc_selected_combos = set()
    tc_base_tokens = set()
    query_has_explicit_strength = False
    explicit_move_attempt = False
    missing_scrolls_query = False
    comparison_char_inputs = {}
    if wants_frame_data:
        # 2. Heuristic: For each mentioned character, search for moves mentioned nearby?
        # Simpler approach: Check if any move inputs are present in the text
        # that map to these characters.

        query_requires_variant_state = query_requires_character_variant_state(text_tokens)
        query_requires_charged = any(token in text_tokens for token in ("charged", "hold", "held"))
        query_requests_level2 = jamie_drink_level is None and bool(re.search(r"\b(?:lv|lvl|level)\s*2\b", text_lower))
        query_requests_level3 = jamie_drink_level is None and bool(re.search(r"\b(?:lv|lvl|level)\s*3\b", text_lower))

        def query_requests_super_art(level):
            explicit_super = bool(
                re.search(
                    rf"\b(?:sa\s*{level}|super\s*art\s*(?:level\s*)?{level}|super\s*{level})\b",
                    text_lower,
                )
            )
            bare_level = bool(re.search(rf"\b(?:lv|lvl|level)\s*{level}\b", text_lower))
            return explicit_super or (jamie_drink_level is None and bare_level)

        query_requests_sa1 = query_requests_super_art(1)
        query_requests_sa2 = query_requests_super_art(2)
        query_requests_sa3 = query_requests_super_art(3)
        query_requests_ca = bool(
            re.search(r"\b(?:ca|critical\s+art)\b", text_lower)
        )
        stock_hint_tokens = ("stock", "stocked", "enhanced", "windclad")

        def token_is_stock_hint(token):
            token_norm = str(token or "").lower().strip()
            if not token_norm:
                return False
            if token_norm in stock_hint_tokens:
                return True
            return any(
                difflib.SequenceMatcher(None, token_norm, hint_token).ratio() >= 0.82
                for hint_token in stock_hint_tokens
            )

        query_requires_stocked = any(
            token in text_tokens for token in ("stock", "stocked", "enhanced", "windclad")
        ) or bool(re.search(r"\bwind\s+clad\b", text_lower)) or any(
            token_is_stock_hint(token) for token in text_tokens
        )

        def row_is_charged_variant(row):
            move_name = str(row.get("moveName", "")).lower()
            cmn_name = str(row.get("cmnName", "")).lower()
            num_cmd = str(row.get("numCmd", "")).lower()
            char_key = resolve_character_key(row.get("char_name", ""))
            num_cmd_compact = normalize_num_cmd_token(num_cmd)
            akuma_fireball_level = (
                char_key == "akuma"
                and num_cmd_compact == "236p"
                and bool(re.search(r"\blvl\s*[23]\b", num_cmd))
            )
            return (
                "charged" in move_name
                or "charged" in cmn_name
                or "hold" in move_name
                or "hold" in cmn_name
                or "(charged" in num_cmd
                or "(hold" in num_cmd
                or akuma_fireball_level
            )

        def row_is_od_variant(row):
            move_name = str(row.get("moveName", "")).lower().strip()
            cmn_name = str(row.get("cmnName", "")).lower().strip()
            num_cmd_compact = re.sub(r"[^a-z0-9]", "", str(row.get("numCmd", "")).lower())
            return (
                move_name.startswith(("od ", "ex "))
                or cmn_name.startswith(("od ", "ex "))
                or num_cmd_compact.endswith(("pp", "kk"))
            )

        def apply_explicit_strength_result_filter(rows):
            if not query_has_explicit_strength or not rows:
                return rows
            if query_wants_od_strength:
                od_rows = [row for row in rows if row_is_od_variant(row)]
                return od_rows or rows
            if query_wants_non_od_strength:
                exact_rows = [row for row in rows if row_matches_explicit_strength(row, text_lower)]
                if exact_rows:
                    return exact_rows
                non_od_rows = [row for row in rows if not row_is_od_variant(row)]
                return non_od_rows or rows
            return rows

        def row_matches_requested_level(row):
            if not (query_requests_level2 or query_requests_level3):
                return False
            row_text = " ".join(
                str(row.get(field, "")).lower()
                for field in ("moveName", "cmnName", "numCmd")
            )
            if query_requests_level2 and re.search(r"\b(?:lv|lvl|level)\s*2\b", row_text):
                return True
            if query_requests_level3 and re.search(r"\b(?:lv|lvl|level)\s*3\b", row_text):
                return True
            return False

        def query_mentions_fireball_terms():
            return bool(re.search(r"\b(?:fireball|hadou?ken|gou\s+hadou?ken|236\s*h?p)\b", text_lower))

        def row_matches_explicit_strength(row, strength_query_text):
            row_move_name = str(row.get("moveName", "")).lower().strip()
            row_cmn_name = str(row.get("cmnName", "")).lower().strip()
            row_num_cmd = str(row.get("numCmd", "")).lower().strip()
            row_num_cmd_compact = re.sub(r"[^a-z0-9]", "", row_num_cmd)

            if re.search(r"\b(?:od|ex)\b", strength_query_text):
                return row_is_od_variant(row)

            strength_groups = [
                ({"light", "l", "lp", "lk"}, {"lp", "lk"}),
                ({"medium", "m", "mp", "mk"}, {"mp", "mk"}),
                ({"heavy", "h", "hp", "hk"}, {"hp", "hk"}),
            ]

            requested_suffixes = set()
            for token_group, suffixes in strength_groups:
                if any(re.search(rf"\b{re.escape(token)}\b", strength_query_text) for token in token_group):
                    requested_suffixes.update(suffixes)

            if not requested_suffixes:
                return False

            if any(row_move_name.startswith(f"{suffix} ") or row_cmn_name.startswith(f"{suffix} ") for suffix in requested_suffixes):
                return True
            if any(
                row_move_name.startswith(f"{word} ") or row_cmn_name.startswith(f"{word} ")
                for word in ("light", "medium", "heavy")
                if word[0] in {suffix[0] for suffix in requested_suffixes}
            ):
                return True
            return row_num_cmd_compact.endswith(tuple(requested_suffixes))

        def row_matches_query_move_terms(row):
            ignored_tokens = {
                "framedata", "frame", "frames", "data", "stats", "stat", "gif", "gifs", "hitbox", "hitboxes",
                "light", "medium", "heavy", "l", "m", "h",
                "lp", "mp", "hp", "lk", "mk", "hk", "pp", "kk",
                "od", "ex", "charged", "hold", "held",
                "startup", "active", "recovery", "range",
                "on", "hit", "block", "damage", "cancel",
                "super", "art", "level", "lvl", "lv",
                "sa1", "sa2", "sa3", "lv1", "lv2", "lv3", "lvl1", "lvl2", "lvl3",
            }
            if jamie_drink_level is not None:
                ignored_tokens.update({"drink", "drinks", f"dl{jamie_drink_level}"})
            for char in mentioned_chars:
                ignored_tokens.update(re.findall(r"[a-z0-9]+", str(char).lower()))
            for alias, canonical in CHARACTER_ALIASES.items():
                if canonical in mentioned_chars:
                    ignored_tokens.update(re.findall(r"[a-z0-9]+", str(alias).lower()))

            significant_tokens = [
                token for token in text_tokens
                if token not in ignored_tokens and len(token) >= 3
            ]
            if not significant_tokens:
                return True

            row_text = " ".join(
                str(row.get(field, "")).lower()
                for field in ("moveName", "cmnName", "numCmd", "plnCmd")
            )
            row_text_compact = re.sub(r"[^a-z0-9]", "", row_text)
            row_tokens = re.findall(r"[a-z0-9]+", row_text)
            for token in significant_tokens:
                token_compact = re.sub(r"[^a-z0-9]", "", token)
                if not token_compact:
                    continue
                if token in row_text or token_compact in row_text_compact:
                    continue
                if len(token_compact) >= 4 and any(
                    len(row_token) >= 4
                    and difflib.SequenceMatcher(None, token_compact, row_token).ratio() >= 0.84
                    for row_token in row_tokens
                ):
                    continue
                return False
            return True

        def row_is_ca_variant(row):
            move_name = str(row.get("moveName", "")).lower()
            cmn_name = str(row.get("cmnName", "")).lower()
            num_cmd = str(row.get("numCmd", "")).lower()
            return (
                "critical art" in move_name
                or "critical art" in cmn_name
                or bool(re.search(r"\(\s*ca\s*\)", num_cmd))
            )

        def row_is_stocked_variant(row):
            move_name = str(row.get("moveName", "")).lower()
            cmn_name = str(row.get("cmnName", "")).lower()
            num_cmd = str(row.get("numCmd", "")).lower()
            combined = f"{move_name} {cmn_name} {num_cmd}"
            if re.search(r"\b0\s*stocks?\b", combined):
                return False

            has_stock_count = bool(re.search(r"\b[1-9]\d*\s*stocks?\b", combined))
            has_stock_tag = "(stock" in move_name or "(stock" in cmn_name or "(stock" in num_cmd
            has_enhanced_tag = (
                "enhanced" in move_name
                or "enhanced" in cmn_name
                or "(enhanced" in num_cmd
            )
            has_windclad_tag = "windclad" in move_name or "windclad" in cmn_name
            has_wind_stock_hold = "wind stock" in cmn_name and (
                "(" in cmn_name or "(hold" in num_cmd
            )
            return (
                has_stock_count
                or has_stock_tag
                or has_enhanced_tag
                or has_windclad_tag
                or has_wind_stock_hold
            )

        def row_is_air_move(row):
            move_name = str(row.get("moveName", "")).lower()
            cmn_name = str(row.get("cmnName", "")).lower()
            num_cmd = str(row.get("numCmd", "")).lower()
            return (
                "(air" in num_cmd
                or "air" in move_name
                or "air" in cmn_name
                or "aerial" in move_name
                or "aerial" in cmn_name
            )

        # 3. Extract move inputs and aliases from query text
        move_regex = r"\b([1-9][0-9]*[a-zA-Z]+|stand\s+[a-zA-Z]+|crouch\s+[a-zA-Z]+|(?:neutral\s+|n\s+)?jump\s+[a-zA-Z]+|(?:neutral\s+|n\s+)?jump\s+[1-9][0-9]*[a-zA-Z]+|(?:neutral\s+|n\s+)?j(?:\s+|\.)[a-zA-Z]+|(?:neutral\s+|n\s+)?j(?:\s+|\.)[1-9][0-9]*[a-zA-Z]+|(?:neutral\s+|n\s+)?j\.?[1-9][0-9]*[a-zA-Z]+|(?:lp|mp|hp|lk|mk|hk|light|medium|heavy|l|m|h)\s+[a-zA-Z]+(?:\s+[a-zA-Z]+)?|[a-zA-Z]+\s+kick|[a-zA-Z]+\s+punch)\b"
        potential_inputs = re.findall(move_regex, text_lower)
        compact_motion_inputs = []
        motion_button_matches = re.findall(
            r"\b([1-9][0-9]{1,4})\s*(?:\+)?\s*(lp|mp|hp|lk|mk|hk|pp|kk|p|k)\b",
            text_lower,
        )
        for motion_digits, button_suffix in motion_button_matches:
            compact_motion = f"{motion_digits}{button_suffix}"
            if compact_motion not in compact_motion_inputs:
                compact_motion_inputs.append(compact_motion)

        for compact_motion in collect_normal_notation_inputs(text_lower):
            if compact_motion not in compact_motion_inputs:
                compact_motion_inputs.append(compact_motion)

        if compact_motion_inputs:
            potential_inputs = compact_motion_inputs + potential_inputs
        strength_prefix_pattern = re.compile(r"^(lp|mp|hp|lk|mk|hk|light|medium|heavy|l|m|h)\b")
        strength_prefixes_for_filter = [
            "lp", "mp", "hp", "lk", "mk", "hk",
            "light", "medium", "heavy", "l", "m", "h",
        ]
        filtered_inputs = []
        for inp in potential_inputs:
            if not inp:
                continue
            original_inp = str(inp).strip().lower()
            cleaned_inp = re.sub(r"\s+framedata$", "", inp).strip()
            cleaned_inp = re.sub(r"\s+frame\s*data$", "", cleaned_inp).strip()
            cleaned_inp = re.sub(r"\s+frame$", "", cleaned_inp).strip()
            cleaned_inp = re.sub(
                r"\s+(?:gif|gifs|hitbox|hitboxes)(?:\s+link)?$",
                "",
                cleaned_inp,
            ).strip()
            if not cleaned_inp:
                continue
            if cleaned_inp in strength_prefixes_for_filter and re.search(
                r"\b(frame\s*data|framedata|frame|data|gif|gifs|hitbox|hitboxes|startup|recovery|active|stats|punish|punishable)\b",
                original_inp,
            ):
                continue
            if not strength_prefix_pattern.match(cleaned_inp):
                if any(
                    re.search(
                        rf"\b{re.escape(prefix)}\s+{re.escape(cleaned_inp)}\b",
                        text_lower,
                    )
                    for prefix in strength_prefixes_for_filter
                ):
                    continue
            filtered_inputs.append(cleaned_inp)
        potential_inputs = filtered_inputs
        extra_inputs = []
        query_strength_tokens = {
            "lp", "mp", "hp", "lk", "mk", "hk",
            "light", "medium", "heavy", "l", "m", "h",
            "od", "ex",
        }
        compact_strength_motion_present = bool(
            re.search(
                r"\b(?:lp|mp|hp|lk|mk|hk|light|medium|heavy|l|m|h)\s*(?:dp|srk|shoryu|shoryuken)\b"
                r"|\b(?:dp|srk|shoryu|shoryuken)\s*(?:lp|mp|hp|lk|mk|hk|light|medium|heavy|l|m|h)\b",
                text_lower,
            )
        )
        compact_od_motion_present = bool(
            re.search(
                r"\b(?:od|ex)\s*(?:dp|srk|shoryu|shoryuken)\b"
                r"|\b(?:dp|srk|shoryu|shoryuken)\s*(?:od|ex)\b",
                text_lower,
            )
        )
        compact_num_cmd_strength_present = bool(
            re.search(
                r"\b(?:j\.?\s*)?[1-9][0-9]{1,5}\s*(?:\+)?\s*(?:lp|mp|hp|lk|mk|hk|pp|kk)\b",
                text_lower,
            )
        )
        query_has_explicit_strength = bool(
            any(token in query_strength_tokens for token in text_tokens)
            or compact_strength_motion_present
            or compact_od_motion_present
            or compact_num_cmd_strength_present
        )
        query_wants_od_strength = bool(re.search(r"\b(od|ex)\b", text_lower))
        query_wants_non_od_strength = bool(
            re.search(r"\b(lp|mp|hp|lk|mk|hk|light|medium|heavy|l|m|h)\b", text_lower)
        )
        filter_state = create_character_filter_state()
        query_requests_air_context = bool(
            re.search(r"\b(?:air|aerial)\b", text_lower)
            or air_special_move_query
        )
        air_fireball_context = bool(
            re.search(
                r"\b(?:air|aerial)\s+fireball\b|\bair\s+hadoken\b",
                text_lower,
            )
        )
        air_sa1_context = bool(
            re.search(
                r"\b(?:air|aerial)\s*(?:sa\s*1|super\s*art\s*1|super\s*1|level\s*1)\b"
                r"|\b(?:sa\s*1|super\s*art\s*1|super\s*1|level\s*1)\s*(?:air|aerial)\b",
                text_lower,
            )
        )
        air_sa2_context = bool(
            re.search(
                r"\b(?:air|aerial)\s*(?:sa\s*2|super\s*art\s*2|super\s*2|level\s*2)\b"
                r"|\b(?:sa\s*2|super\s*art\s*2|super\s*2|level\s*2)\s*(?:air|aerial)\b",
                text_lower,
            )
        )
        zangief_borscht_context = bool(
            re.search(r"\bborscht\b", text_lower)
            or re.search(r"\bj\.?\s*360\s*\+?\s*k{1,2}\b", text_lower)
            or re.search(r"\bj\s+360\s*\+?\s*k{1,2}\b", text_lower)
        )
        air_sa3_context = bool(
            re.search(
                r"\b(?:air|aerial)\s*(?:sa\s*3|super\s*art\s*3|super\s*3|level\s*3|critical\s+art|ca)\b"
                r"|\b(?:sa\s*3|super\s*art\s*3|super\s*3|level\s*3|critical\s+art|ca)\s*(?:air|aerial)\b",
                text_lower,
            )
        )
        air_tatsu_context = bool(
            re.search(
                r"\b(?:air|aerial)\s+tatsu\b|\b(?:air|aerial)\s+tatsumaki\b|\btatsu\s*\(air\)\b|\bj\.?\s*214k\b",
                text_lower,
            )
        )
        followup_contexts = collect_character_followup_contexts(text_lower, mentioned_chars)
        ken_run_followup_context = bool(followup_contexts.get("run_followup"))
        cammy_hooligan_followup_context = bool(
            "cammy" in mentioned_chars and query_has_hooligan_followup(text_lower)
        )

        character_aliases = collect_character_specific_aliases(
            text_lower,
            extra_inputs,
            potential_inputs,
            mentioned_chars=mentioned_chars,
            air_sa1_context=air_sa1_context,
            air_sa2_context=air_sa2_context,
            air_sa3_context=air_sa3_context,
            query_requires_stocked=query_requires_stocked,
            query_wants_od_strength=query_wants_od_strength,
            ken_run_followup_context=ken_run_followup_context,
        )
        filter_state = character_aliases["filter_state"]
        potential_inputs = character_aliases["potential_inputs"]

        if query_requests_ca and "critical art" not in extra_inputs:
            extra_inputs.append("critical art")
        for requested, super_input in (
            (query_requests_sa1, "super art level 1"),
            (query_requests_sa2, "super art level 2"),
            (query_requests_sa3, "super art level 3"),
        ):
            if requested and super_input not in extra_inputs:
                extra_inputs.append(super_input)


        def is_special_motion_num_cmd(num_cmd_raw):
            compact = re.sub(r"[^a-z0-9]", "", str(num_cmd_raw).lower())
            if not compact or ">" in str(num_cmd_raw):
                return False
            motion_prefixes = (
                "236", "214", "623", "421", "41236", "63214", "4268", "624", "46", "28",
                "214214", "236236", "360", "720", "22",
            )
            return compact.startswith(motion_prefixes)

        def get_special_canonical_base_name(row):
            raw_name = str(row.get("cmnName", "")).lower().strip()
            if not raw_name:
                raw_name = str(row.get("moveName", "")).lower().strip()
            if not raw_name:
                return ""
            char_key = resolve_character_key(row.get("char_name", ""))
            base_name = re.sub(r"^(od|ex)\s+", "", raw_name)
            base_name = re.sub(
                r"^(lp|mp|hp|lk|mk|hk|pp|kk|light|medium|heavy|l|m|h)\s+",
                "",
                base_name,
            ).strip()
            base_name = re.sub(r"\s*\(charged\)", "", base_name).strip()
            if char_key == "akuma" and base_name in {
                "fireball (lvl 2)",
                "red fireball (lvl 3)",
                "red fireball",
            }:
                return "fireball"
            return base_name

        command_jump_notation_present = bool(
            re.search(
                r"\b(?:neutral\s+|n\s+)?(?:jump\s+|j\.?\s*)[1-9][0-9]*(?:lp|mp|hp|lk|mk|hk|p|k)\b",
                text_lower,
            )
        )

        if (
            not query_has_explicit_strength
            and mentioned_chars
            and not target_combo_query
            and not command_jump_notation_present
            and not query_requires_stocked
            and not query_requests_ca
        ):
            seen_special_prompts = set()
            for char in mentioned_chars:
                special_base_map = {}
                for row in FRAME_DATA.get(char, []):
                    if char == "jamie" and row.get("_jamie_drink_level") is not None:
                        continue
                    if not is_special_motion_num_cmd(row.get("numCmd", "")):
                        continue
                    canonical_base = get_special_canonical_base_name(row)
                    if not canonical_base:
                        continue
                    special_base_map.setdefault(canonical_base, [])
                    if row not in special_base_map[canonical_base]:
                        special_base_map[canonical_base].append(row)

                for base_name, variants in special_base_map.items():
                    prompt_variants = variants

                    chosen_variant, handled_variant = choose_character_special_variant(
                        char=char,
                        base_name=base_name,
                        variants=variants,
                        query_requests_sa1=query_requests_sa1,
                        query_requests_sa2=query_requests_sa2,
                        query_requires_variant_state=query_requires_variant_state,
                        row_matches_variant_state=row_matches_character_variant_state,
                    )
                    if chosen_variant and chosen_variant not in results:
                        results.append(chosen_variant)
                    if handled_variant:
                        continue

                    if query_requires_variant_state:
                        state_variants = [row for row in variants if row_matches_character_variant_state(row)]
                        if state_variants:
                            prompt_variants = state_variants
                        else:
                            continue
                    if query_requires_charged:
                        charged_prompt_variants = [row for row in prompt_variants if row_is_charged_variant(row)]
                        if charged_prompt_variants:
                            prompt_variants = charged_prompt_variants
                    if query_requests_air_context:
                        air_prompt_variants = [row for row in prompt_variants if row_is_air_move(row)]
                        if air_prompt_variants:
                            prompt_variants = air_prompt_variants
                        else:
                            continue
                    prompt_variants = prefer_grounded_rows(prompt_variants, text_lower)
                    base_tokens = re.findall(r"[a-z0-9]+", base_name)
                    base_in_query = tokens_in_text(base_tokens)
                    if not base_in_query and base_name == "fireball":
                        base_in_query = "hadoken" in text_tokens or "hadouken" in text_tokens
                    if not base_in_query and base_name == "upkicks":
                        base_in_query = "tensho" in text_tokens or "tenshokyaku" in text_tokens
                    if not base_in_query and base_name == "palm thrust":
                        base_in_query = "hashogeki" in text_tokens
                    if not base_in_query and base_name == "super art level 1":
                        base_in_query = query_requests_sa1
                    if not base_in_query and base_name == "super art level 2":
                        base_in_query = query_requests_sa2
                    if not base_in_query and base_name == "super art level 3":
                        base_in_query = query_requests_sa3
                    if not base_in_query and base_name == "spd":
                        base_in_query = "command" in text_tokens and "grab" in text_tokens
                    if not base_in_query and base_name in {"burn kick", "burning kick"}:
                        base_in_query = bool(
                            re.search(r"\bburnkicks?\b", text_lower)
                            or re.search(r"\bburn\s+kicks?\b", text_lower)
                            or re.search(r"\bburning\s+kicks?\b", text_lower)
                        )
                    if not base_in_query and base_name == "scissor kick":
                        base_in_query = bool(
                            re.search(r"\bscissors?\b", text_lower)
                            or re.search(r"\bscissor\s+kicks?\b", text_lower)
                        )
                    if not base_in_query:
                        continue
                    if query_requests_level2 or query_requests_level3:
                        level_variants = [row for row in prompt_variants if row_matches_requested_level(row)]
                        if level_variants:
                            for row in level_variants:
                                if row not in results:
                                    results.append(row)
                            continue
                    if len(prompt_variants) < 2:
                        continue
                    if (
                        air_fireball_context
                        and base_name == "fireball"
                        and "air fireball" in special_base_map
                    ):
                        continue
                    if len(prompt_variants) == 2:
                        od_variants = [row for row in prompt_variants if row_is_od_variant(row)]
                        non_od_variants = [row for row in prompt_variants if not row_is_od_variant(row)]
                        if len(od_variants) == 1 and len(non_od_variants) == 1:
                            chosen_variant = od_variants[0] if query_wants_od_strength else non_od_variants[0]
                            if chosen_variant not in results:
                                results.append(chosen_variant)
                            continue
                    if should_skip_special_prompt_base(
                        char=char,
                        base_name=base_name,
                        air_tatsu_context=air_tatsu_context,
                        ken_run_followup_context=ken_run_followup_context,
                    ):
                        continue
                    prompt_key = (char, base_name)
                    if prompt_key in seen_special_prompts:
                        continue
                    seen_special_prompts.add(prompt_key)
                    special_prompt_blocks.append(
                        build_sf6_disambiguation_prompt(
                            "Special Strength Options",
                            char.capitalize(),
                            f"{base_name.title()} variants",
                            format_move_options(prompt_variants),
                        )
                    )
        dp_strength_inputs = []
        dp_strength_prefix_matches = re.findall(
            r"\b(lp|mp|hp|lk|mk|hk|light|medium|heavy|l|m|h)\s*(?:\+)?\s*(dp|srk|shoryu|shoryuken)\b",
            text_lower,
        )
        for strength_token, motion_token in dp_strength_prefix_matches:
            token = f"{strength_token} {motion_token}"
            if token not in dp_strength_inputs:
                dp_strength_inputs.append(token)
        dp_strength_suffix_matches = re.findall(
            r"\b(dp|srk|shoryu|shoryuken)\s*(?:\+)?\s*(lp|mp|hp|lk|mk|hk|light|medium|heavy|l|m|h)\b",
            text_lower,
        )
        for motion_token, strength_token in dp_strength_suffix_matches:
            token = f"{strength_token} {motion_token}"
            if token not in dp_strength_inputs:
                dp_strength_inputs.append(token)
        for token in dp_strength_inputs:
            if token not in extra_inputs:
                extra_inputs.append(token)

        dp_aliases = ["dp", "srk", "shoryu", "shoryuken", "623"]
        dp_present = False
        for token in dp_aliases:
            if re.search(rf"\b{re.escape(token)}\b", text_lower):
                if dp_strength_inputs and token in {"dp", "srk", "shoryu", "shoryuken"}:
                    dp_present = True
                    continue
                if token not in extra_inputs:
                    extra_inputs.append(token)
                dp_present = True
        if dp_present and re.search(r"\b(ex|od)\b", text_lower):
            extra_inputs.append("623pp")
            extra_inputs.append("623kk")
        if re.search(r"\b(ex|od)(dp|srk|shoryu|shoryuken)\b", text_lower):
            extra_inputs.append("623pp")
            extra_inputs.append("623kk")
        if ken_run_followup_context:
            extra_inputs = [
                token for token in extra_inputs
                if token not in {"dp", "srk", "shoryu", "shoryuken", "tatsu", "dragonlash"}
            ]
        if "sway" in text_lower:
            extra_inputs.append("sway")
        if "jus cool" in text_lower or "juscool" in text_lower:
            extra_inputs.append("jus cool")

        filter_state = collect_late_character_specific_aliases(
            text_lower,
            extra_inputs,
            filter_state,
            mentioned_chars=mentioned_chars,
        )
        sa_alias_matches = re.findall(r"\bsa\s*([123])\b", text_lower)
        for sa_level in sa_alias_matches:
            sa_token = f"sa{sa_level}"
            if sa_token not in extra_inputs:
                extra_inputs.append(sa_token)
        # 46P charge patterns (back-forward+punch)
        charge_patterns = [
            (r"\b46p\b", "46p"),
            (r"\b46lp\b", "46lp"),
            (r"\b46mp\b", "46mp"),
            (r"\b46hp\b", "46hp"),
            (r"\b46pp\b", "46pp"),
            (r"\bb,\s*f\+?p\b", "46p"),
            (r"\bb,\s*f\+?lp\b", "46lp"),
            (r"\bb,\s*f\+?mp\b", "46mp"),
            (r"\bb,\s*f\+?hp\b", "46hp"),
            (r"\bb,\s*f\+?pp\b", "46pp"),
            (r"\bbf\+?p\b", "46p"),
            (r"\bbf\+?lp\b", "46lp"),
            (r"\bbf\+?mp\b", "46mp"),
            (r"\bbf\+?hp\b", "46hp"),
            (r"\bbf\+?pp\b", "46pp"),
            (r"\bback\s*forward\+?p\b", "46p"),
            (r"\bback\s*forward\+?lp\b", "46lp"),
            (r"\bback\s*forward\+?mp\b", "46mp"),
            (r"\bback\s*forward\+?hp\b", "46hp"),
            (r"\bback\s*forward\+?pp\b", "46pp"),
            # 28K charge patterns (down-up+kick)
            (r"\b28k\b", "28k"),
            (r"\b28lk\b", "28lk"),
            (r"\b28mk\b", "28mk"),
            (r"\b28hk\b", "28hk"),
            (r"\b28kk\b", "28kk"),
            (r"\bd,\s*u\+?k\b", "28k"),
            (r"\bd,\s*u\+?lk\b", "28lk"),
            (r"\bd,\s*u\+?mk\b", "28mk"),
            (r"\bd,\s*u\+?hk\b", "28hk"),
            (r"\bd,\s*u\+?kk\b", "28kk"),
            (r"\bdu\+?k\b", "28k"),
            (r"\bdu\+?lk\b", "28lk"),
            (r"\bdu\+?mk\b", "28mk"),
            (r"\bdu\+?hk\b", "28hk"),
            (r"\bdu\+?kk\b", "28kk"),
            (r"\bdown\s*up\+?k\b", "28k"),
            (r"\bdown\s*up\+?lk\b", "28lk"),
            (r"\bdown\s*up\+?mk\b", "28mk"),
            (r"\bdown\s*up\+?hk\b", "28hk"),
            (r"\bdown\s*up\+?kk\b", "28kk"),
        ]
        for pattern, token in charge_patterns:
            if re.search(pattern, text_lower) and token not in extra_inputs:
                extra_inputs.append(token)
        combo_text = text_lower.replace("->", ">").replace("~", ">")
        tc_selected_combos = set()
        tc_base_tokens = set(re.findall(r"\b[1-9][0-9]*[a-z]{1,3}\b", text_lower))
        tc_pair_candidates = set()
        for base_token, follow_token in re.findall(
            r"\b([1-9][0-9]*[a-z]{1,3})\s*(?:,|>|->)\s*([a-z]{1,3}|[1-9][0-9]*[a-z]{1,3})\b",
            combo_text,
        ):
            tc_pair_candidates.add(f"{base_token}>{follow_token}")
        for base_token, follow_token in re.findall(
            r"\b([1-9][0-9]*[a-z]{1,3})\s+([a-z]{1,3})\s+(?:target\s+combo|tc)\b",
            text_lower,
        ):
            tc_pair_candidates.add(f"{base_token}>{follow_token}")

        combo_matches = re.findall(
            r"\b[0-9a-zA-Z+]+(?:\s*>\s*[0-9a-zA-Z+]+)+\b",
            combo_text,
        )
        for combo in combo_matches:
            combo_token = re.sub(r"\s+", "", combo)
            tc_selected_combos.add(combo_token)
            if combo_token not in extra_inputs:
                extra_inputs.append(combo_token)

        if target_combo_query and mentioned_chars:
            compact_text = re.sub(r"\s+", "", combo_text)
            for char in mentioned_chars:
                normalized_char = normalize_char_name(char)
                compact_text_for_char = re.sub(
                    rf"\b{re.escape(normalized_char)}\b", "", compact_text
                )
                if compact_text_for_char == compact_text:
                    compact_text_for_char = compact_text
                tc_map = {}
                for row in FRAME_DATA.get(char, []):
                    num_cmd_raw = str(row.get("numCmd", ""))
                    num_cmd = num_cmd_raw.lower()
                    if ">" not in num_cmd:
                        continue
                    base_cmd = num_cmd.split(">", 1)[0].strip()
                    base_key = re.sub(r"\s+", "", base_cmd)
                    if not base_key:
                        continue
                    tc_map.setdefault(base_key, []).append(num_cmd_raw)
                for base_key, combos in tc_map.items():
                    if base_key not in compact_text_for_char:
                        continue
                    explicit_pair_matches = [
                        pair for pair in tc_pair_candidates if pair.startswith(f"{base_key}>")
                    ]
                    if explicit_pair_matches:
                        # FAT writes follow-ups with the neutral 5 ("5HP > 5HP"); people type "5HP > HP".
                        def follow_key(follow):
                            return re.sub(r"(^|>)5(?=[lmh]?[pk])", r"\1", follow)

                        explicit_follows = {follow_key(pair.split(">", 1)[1]) for pair in explicit_pair_matches}
                        combo_follows = {}
                        for combo_raw in combos:
                            combo_key = re.sub(r"\s+", "", combo_raw.lower())
                            if ">" in combo_key:
                                combo_follows[combo_key] = follow_key(combo_key.split(">", 1)[1])
                        # The whole typed chain wins, then an exact follow-up, then every combo continuing from it.
                        typed_chains = {follow_key(chain) for chain in tc_selected_combos}
                        matched = [key for key in combo_follows if follow_key(key) in typed_chains] or [
                            key for key, follow in combo_follows.items() if follow in explicit_follows
                        ] or [
                            key for key, follow in combo_follows.items()
                            if any(follow.startswith(explicit) for explicit in explicit_follows)
                        ]
                        for combo_key in matched:
                            tc_selected_combos.add(combo_key)
                            if combo_key not in extra_inputs:
                                extra_inputs.append(combo_key)
                        if matched:
                            continue
                    if len(combos) == 1:
                        combo_token = re.sub(r"\s+", "", combos[0].lower())
                        tc_selected_combos.add(combo_token)
                        if combo_token not in extra_inputs:
                            extra_inputs.append(combo_token)
                        continue
                    tc_ambiguous_inputs.add(base_key)
                    tc_prompt_blocks.append(
                        build_sf6_disambiguation_prompt(
                            "Target Combo Options",
                            char.capitalize(),
                            f"{base_key.upper()} follow-ups",
                            [f"{index}. {combo}" for index, combo in enumerate(combos, start=1)],
                        )
                    )
        keyword_inputs = [
            # 46P moves
            "air slasher",
            "sonic boom",
            "sumo headbutt",
            "psycho crusher",
            "rolling attack",
            "blanka ball",
            "bison crusher",
            "crusher",
            "fireball",
            "boom",
            "headbutt",
            "clap",
            "claps",
            "neko damashi",
            "oicho",
            "oicho throw",
            "ball",
            # 28K moves
            "vertical rolling attack",
            "upball",
            "up ball",
            "somersault kick",
            "flash kick",
            "flashkick",
            "shadow rise",
            "command jump",
            "fly",
            "jackknife maximum",
            "upkicks",
            "upkick",
            "up kicks",
            "tensho",
            "tenshokyaku",
            "tensho kick",
            "tensho kicks",
            "dive kick",
            "divekick",
            "demon flip",
            "demon raid",
            "demon low slash",
            "demon guillotine",
            "demon blade kick",
            "demon swoop",
            "demon gou zanku",
            "demon gou rasen",
            "adamant flame",
            "flaming fist",
            "flame",
            "burn kick",
            "burnkick",
            "burn kicks",
            "burnkicks",
            "burning kick",
            "burning kicks",
            "airthrow",
            "air throw",
            "air grab",
            "scissors",
            "scissor kick",
            "scissor kicks",
            "bear grab",
            "bear hug",
            "running bear grab",
            "air burn kick",
            "air burnkick",
            "air burning kick",
            "air burning kicks",
            "aerial burn kick",
            "aerial burnkick",
            "aerial burning kick",
            "teleport",
            "ashura",
            "ashura senku",
            "raging demon",
            "tenma",
            "gozanku",
            "air fireball",
            "aerial fireball",
            "air hadoken",
            "zanku",
            "air tatsu",
            "aerial tatsu",
            "air tatsumaki",
            "aerial tatsumaki",
            "air legs",
            "airlegs",
            "aerial legs",
            "air lightning legs",
            "sumo smash",
            "ass slam",
            "butt slam",
            "spinning bird kick",
            "sbk",
            # JP 22 specials
            "triglav",
            "amnesia",
            "ground spike",
            "spike",
            "pierce",
        ]
        # Strength prefixes for charge moves
        strength_prefixes = ["lp", "mp", "hp", "od", "ex", "light", "medium", "heavy", "l", "m", "h"]
        for token in keyword_inputs:
            matched_strength_for_token = False
            # Check for strength+keyword combos (e.g., "heavy fireball", "hp boom")
            for prefix in strength_prefixes:
                combo = f"{prefix} {token}"
                if combo in text_lower and combo not in extra_inputs:
                    if should_skip_keyword_input(token, text_lower):
                        continue
                    extra_inputs.append(combo)
                    matched_strength_for_token = True
            # Check for bare keyword
            if (
                not matched_strength_for_token
                and re.search(rf"\b{re.escape(token)}\b", text_lower)
                and token not in extra_inputs
            ):
                if should_skip_keyword_input(token, text_lower):
                    continue
                extra_inputs.append(token)
        extra_inputs = adjust_character_specific_keyword_inputs(text_lower, mentioned_chars, extra_inputs)
        ordered_inputs = []
        for inp in extra_inputs + potential_inputs:
            if inp and inp not in ordered_inputs:
                ordered_inputs.append(inp)
        potential_inputs = ordered_inputs

        if wants_comparison and (potential_inputs or extra_inputs) and len(mentioned_chars) >= 2:
            side_segments = [
                segment.strip()
                for segment in re.split(r"\b(?:vs|versus|and)\b", text_lower)
                if segment.strip()
            ]
            if len(side_segments) >= 2:
                assigned_chars = set()

                def segment_mentions_character(segment_text, char_key):
                    segment_tokens = re.findall(r"[a-z0-9]+", segment_text)
                    if not segment_tokens:
                        return False
                    char_tokens = re.findall(r"[a-z0-9]+", str(char_key).lower())
                    if char_tokens and tokens_in_haystack(segment_tokens, char_tokens):
                        return True
                    for alias, canonical in CHARACTER_ALIASES.items():
                        if normalize_char_name(canonical) != normalize_char_name(char_key):
                            continue
                        alias_tokens = re.findall(r"[a-z0-9]+", str(alias).lower())
                        if alias_tokens and tokens_in_haystack(segment_tokens, alias_tokens):
                            return True
                    return False

                for side_text in side_segments:
                    side_chars = [
                        char for char in mentioned_chars
                        if segment_mentions_character(side_text, char)
                    ]
                    if not side_chars:
                        continue

                    if len(side_chars) == 1:
                        target_char = side_chars[0]
                    else:
                        target_char = next(
                            (char for char in side_chars if char not in assigned_chars),
                            side_chars[0],
                        )

                    side_inputs = []
                    for inp in potential_inputs:
                        inp_norm = str(inp or "").strip().lower()
                        if not inp_norm:
                            continue
                        if re.search(rf"\b{re.escape(inp_norm)}\b", side_text):
                            if inp not in side_inputs:
                                side_inputs.append(inp)

                    if side_inputs:
                        comparison_char_inputs[target_char] = side_inputs
                        assigned_chars.add(target_char)

        explicit_move_attempt = bool(potential_inputs or extra_inputs)
        if mentioned_chars:
            char_tokens = set()
            for char in mentioned_chars:
                char_tokens.update(re.findall(r"[a-z0-9]+", str(char).lower()))
                normalized_char = normalize_char_name(char)
                if normalized_char:
                    char_tokens.add(normalized_char)
                    # A character may be named by an alias ("gief" for Zangief); only
                    # strip aliases the user actually typed so move words survive.
                    for alias, canonical in CHARACTER_ALIASES.items():
                        if normalize_char_name(canonical) != normalized_char:
                            continue
                        alias_tokens = re.findall(r"[a-z0-9]+", alias.lower())
                        if alias_tokens and tokens_in_text(alias_tokens):
                            char_tokens.update(alias_tokens)
            residual_tokens = [
                tok for tok in text_tokens
                if tok not in move_query_stop_tokens and tok not in char_tokens
            ]
            if residual_tokens:
                explicit_move_attempt = True
                residual_candidate = " ".join(residual_tokens).strip()
                if residual_candidate and residual_candidate not in potential_inputs:
                    potential_inputs.append(residual_candidate)

        strength_prefix_re = re.compile(r"^(?:lp|mp|hp|lk|mk|hk|pp|kk|od|ex|light|medium|heavy|l|m|h)\s+")
        text_compact = re.sub(r"[^a-z0-9]", "", text_lower)

        def token_matches_move_name(name_token, query_token):
            if (name_token == "od" and query_token == "ex") or (name_token == "ex" and query_token == "od"):
                return True
            if name_token == query_token:
                return True
            if len(query_token) >= 3 and name_token.startswith(query_token):
                return True
            if len(name_token) >= 3 and query_token.startswith(name_token):
                return True
            return False

        motion_button_notation_present = bool(
            re.search(
                r"\b[1-9][0-9]*\s*(?:\+)?\s*(?:lp|mp|hp|lk|mk|hk|pp|kk|p|k)\b",
                text_lower,
            )
        )

        for char in mentioned_chars:
            char_data = FRAME_DATA[char]
            for row in char_data:
                for name_key in ["cmnName", "moveName"]:
                    raw_name = str(row.get(name_key, "")).lower().strip()
                    if not raw_name:
                        continue
                    candidate_names = [raw_name]
                    stripped_name = strength_prefix_re.sub("", raw_name).strip()
                    if (
                        stripped_name
                        and stripped_name != raw_name
                        and not query_has_explicit_strength
                    ):
                        candidate_names.append(stripped_name)
                    candidate_names = expand_character_candidate_names(char, candidate_names)
                    for candidate_name in candidate_names:
                        candidate_has_strength_prefix = bool(strength_prefix_re.match(candidate_name))
                        if query_has_explicit_strength and not candidate_has_strength_prefix:
                            continue
                        candidate_tokens = re.findall(r"[a-z0-9]+", candidate_name)
                        if not candidate_tokens:
                            continue
                        if len(candidate_tokens) < 2:
                            if should_append_single_token_candidate(char, candidate_tokens, text_tokens, text_lower):
                                if candidate_name not in potential_inputs:
                                    potential_inputs.append(candidate_name)
                            continue
                        candidate_compact = re.sub(r"[^a-z0-9]", "", candidate_name)
                        if (
                            len(candidate_compact) >= 6
                            and candidate_compact in text_compact
                        ):
                            if candidate_name not in potential_inputs:
                                potential_inputs.append(candidate_name)
                            continue
                        if all(
                            any(token_matches_move_name(name_tok, query_tok) for query_tok in text_tokens)
                            for name_tok in candidate_tokens
                        ):
                            if candidate_name not in potential_inputs:
                                potential_inputs.append(candidate_name)

        # also valid simple inputs: "mp", "hk" if preceded by char?

        for char in mentioned_chars:
            char_data = FRAME_DATA[char]

            # Check against potential inputs found via regex
            char_lookup_inputs = potential_inputs
            if comparison_char_inputs.get(char):
                char_lookup_inputs = comparison_char_inputs[char]
            for inp in char_lookup_inputs:
                row = lookup_frame_data(char, inp)
                if row and row not in results:
                    results.append(row)

            # Also check strict "frame data [char] [move]" remainder if exists
            # (This handles the specific verified cases)

            # "brute force" check for short inputs if the regex missed them (like "mp")
            # only if the string looks like "ryu mp"
            def is_button_part_of_dp_motion(button):
                return bool(
                    re.search(
                        rf"\b{re.escape(char)}\s+{button}\s*(?:\+)?\s*(?:dp|srk|shoryu|shoryuken)\b",
                        text_lower,
                    )
                )

            if not special_grab_query and not motion_button_notation_present:
                if f"{char} mp" in text_lower and not is_button_part_of_dp_motion("mp"):
                    row = lookup_frame_data(char, "mp")
                    if row and row not in results:
                        results.append(row)
                if f"{char} mk" in text_lower and not is_button_part_of_dp_motion("mk"):
                    row = lookup_frame_data(char, "mk")
                    if row and row not in results:
                        results.append(row)
                if f"{char} hp" in text_lower and not is_button_part_of_dp_motion("hp"):
                    row = lookup_frame_data(char, "hp")
                    if row and row not in results:
                        results.append(row)
                if f"{char} hk" in text_lower and not is_button_part_of_dp_motion("hk"):
                    row = lookup_frame_data(char, "hk")
                    if row and row not in results:
                        results.append(row)
                if f"{char} lp" in text_lower and not is_button_part_of_dp_motion("lp"):
                    row = lookup_frame_data(char, "lp")
                    if row and row not in results:
                        results.append(row)
                if f"{char} lk" in text_lower and not is_button_part_of_dp_motion("lk"):
                    row = lookup_frame_data(char, "lk")
                    if row and row not in results:
                        results.append(row)

        collect_character_specific_rows(
            text_lower,
            mentioned_chars,
            results,
            lookup_frame_data,
            query_has_explicit_strength=query_has_explicit_strength,
            zangief_borscht_context=zangief_borscht_context,
        )

        if special_grab_query and query_has_explicit_strength and not results and mentioned_chars:
            for char in mentioned_chars:
                grab_variants = []
                for row in FRAME_DATA.get(char, []):
                    cmn_name = str(row.get("cmnName", "")).lower()
                    if "command grab" in cmn_name or re.search(r"\bspd\b", cmn_name):
                        grab_variants.append(row)
                if len(grab_variants) >= 2:
                    special_prompt_blocks.append(
                        build_sf6_disambiguation_prompt(
                            "Special Strength Options",
                            char.capitalize(),
                            "Command Grab variants",
                            format_move_options(grab_variants),
                        )
                    )

        if query_requires_variant_state and results:
            state_results = [row for row in results if row_matches_character_variant_state(row)]
            results = state_results

        if query_requires_charged and results:
            charged_results = [row for row in results if row_is_charged_variant(row)]
            if charged_results:
                results = charged_results
            else:
                upgraded_charged_results = []
                for row in results:
                    row_char_key = resolve_character_key(row.get("char_name", ""))
                    if not row_char_key:
                        continue

                    row_num_cmd_base = normalize_num_cmd_token(row.get("numCmd", ""))
                    if not row_num_cmd_base:
                        continue

                    charged_match = None
                    for candidate in FRAME_DATA.get(row_char_key, []):
                        if not row_is_charged_variant(candidate):
                            continue
                        candidate_base = normalize_num_cmd_token(candidate.get("numCmd", ""))
                        if candidate_base == row_num_cmd_base:
                            charged_match = candidate
                            break

                    if charged_match and charged_match not in upgraded_charged_results:
                        upgraded_charged_results.append(charged_match)

                if upgraded_charged_results:
                    results = upgraded_charged_results

        if mentioned_chars and "akuma" in mentioned_chars and query_mentions_fireball_terms():
            akuma_charged_fireballs = [
                row for row in FRAME_DATA.get("akuma", [])
                if get_special_canonical_base_name(row) == "fireball"
                and row_is_charged_variant(row)
                and not row_is_od_variant(row)
            ]
            if query_requests_level2 or query_requests_level3:
                level_results = [row for row in akuma_charged_fireballs if row_matches_requested_level(row)]
                if level_results:
                    results = level_results
            elif query_requires_charged and query_has_explicit_strength and not query_wants_od_strength and len(akuma_charged_fireballs) > 1:
                special_prompt_blocks.append(
                    build_sf6_disambiguation_prompt(
                        "Special Strength Options",
                        "Akuma",
                        "Fireball variants",
                        format_move_options(akuma_charged_fireballs),
                    )
                )
                allow_explicit_special_prompt = True
                results = []

        if query_has_explicit_strength and results:
            wants_od_strength = bool(re.search(r"\b(od|ex)\b", text_lower))
            wants_non_od_strength = bool(
                re.search(r"\b(lp|mp|hp|lk|mk|hk|light|medium|heavy|l|m|h)\b", text_lower)
            )
            if wants_od_strength:
                od_results = [row for row in results if row_is_od_variant(row)]
                if od_results:
                    results = od_results
            elif wants_non_od_strength:
                non_od_results = [row for row in results if not row_is_od_variant(row)]
                if non_od_results:
                    results = non_od_results

            exact_strength_results = [
                row for row in results
                if row_matches_explicit_strength(row, text_lower)
            ]
            if exact_strength_results:
                results = exact_strength_results

            exact_term_results = [
                row for row in results
                if row_matches_query_move_terms(row)
            ]
            if exact_term_results:
                results = exact_term_results

        results = apply_character_specific_result_filters(
            text_lower,
            text_tokens,
            mentioned_chars,
            results,
            filter_state=filter_state,
            air_tatsu_context=air_tatsu_context,
            air_fireball_context=air_fireball_context,
            air_sa1_context=air_sa1_context,
            query_requests_sa3=query_requests_sa3,
            air_sa3_context=air_sa3_context,
            query_wants_od_strength=query_wants_od_strength,
            normalize_char_name=normalize_char_name,
            lookup_frame_data=lookup_frame_data,
            row_is_ca_variant=row_is_ca_variant,
            cammy_hooligan_followup_context=cammy_hooligan_followup_context,
        )

        if query_requests_ca and results:
            ca_rows = [row for row in results if row_is_ca_variant(row)]
            if ca_rows:
                results = ca_rows

        if query_requests_air_context and results:
            air_rows = []
            for row in results:
                move_name = str(row.get("moveName", "")).lower()
                cmn_name = str(row.get("cmnName", "")).lower()
                num_cmd = str(row.get("numCmd", "")).lower()
                if "air" in move_name or "air" in cmn_name or "(air)" in num_cmd:
                    air_rows.append(row)
            if air_rows:
                results = air_rows

        if query_requires_stocked and results:
            stocked_rows = [row for row in results if row_is_stocked_variant(row)]
            if stocked_rows:
                results = stocked_rows

        results = apply_explicit_strength_result_filter(results)

        if (
            not query_has_explicit_strength
            and results
            and not target_combo_query
            and not query_requires_stocked
            and not query_requests_ca
        ):
            existing_special_prompt_keys = set()

            def variant_is_air_move(row):
                move_name = str(row.get("moveName", "")).lower()
                cmn_name = str(row.get("cmnName", "")).lower()
                num_cmd = str(row.get("numCmd", "")).lower()
                return (
                    "(air" in num_cmd
                    or "air" in move_name
                    or "air" in cmn_name
                    or "aerial" in move_name
                    or "aerial" in cmn_name
                )

            for prompt_block in special_prompt_blocks:
                char_match = re.search(r"Special Strength Options \(([^)]+)\)", prompt_block)
                base_match = re.search(r"\n([^\n]+) variants:", prompt_block)
                if not char_match or not base_match:
                    continue
                prompt_char = normalize_char_name(char_match.group(1))
                prompt_base = str(base_match.group(1)).lower().strip()
                if prompt_char and prompt_base:
                    existing_special_prompt_keys.add((prompt_char, prompt_base))

            ambiguous_special_keys = set()
            for row in results:
                row_char_norm = normalize_char_name(row.get("char_name", ""))
                if not row_char_norm:
                    continue
                if mentioned_chars and row_char_norm not in {
                    normalize_char_name(char) for char in mentioned_chars
                }:
                    continue
                row_char_key = resolve_character_key(row.get("char_name", ""))
                if not row_char_key:
                    continue
                if not is_special_motion_num_cmd(row.get("numCmd", "")):
                    continue

                base_name = get_special_canonical_base_name(row)
                if not base_name:
                    continue

                if should_skip_ambiguous_special_key(row_char_norm, base_name):
                    continue

                key = (row_char_norm, base_name)
                if key in existing_special_prompt_keys or key in ambiguous_special_keys:
                    continue

                variants = []
                for candidate in FRAME_DATA.get(row_char_key, []):
                    if row_char_key == "jamie" and candidate.get("_jamie_drink_level") is not None:
                        continue
                    if not is_special_motion_num_cmd(candidate.get("numCmd", "")):
                        continue
                    if get_special_canonical_base_name(candidate) != base_name:
                        continue
                    if query_requires_variant_state and not row_matches_character_variant_state(candidate):
                        continue
                    variants.append(candidate)

                variants = prefer_grounded_rows(variants, text_lower)
                if query_requests_air_context and not any(
                    variant_is_air_move(candidate) for candidate in variants
                ):
                    continue

                if query_requests_level2 or query_requests_level3:
                    continue

                if len(variants) < 2:
                    continue

                if len(variants) == 2:
                    od_variants = [candidate for candidate in variants if row_is_od_variant(candidate)]
                    non_od_variants = [candidate for candidate in variants if not row_is_od_variant(candidate)]
                    if len(od_variants) == 1 and len(non_od_variants) == 1:
                        chosen_variant = od_variants[0] if query_wants_od_strength else non_od_variants[0]
                        if chosen_variant not in results:
                            results.append(chosen_variant)
                        continue

                special_prompt_blocks.append(
                    build_sf6_disambiguation_prompt(
                        "Special Strength Options",
                        row_char_key.capitalize(),
                        f"{base_name.title()} variants",
                        format_move_options(variants),
                    )
                )
                ambiguous_special_keys.add(key)

            if ambiguous_special_keys:
                filtered_results = []
                for row in results:
                    row_char = normalize_char_name(row.get("char_name", ""))
                    row_base = get_special_canonical_base_name(row)
                    row_key = (row_char, row_base)
                    if (
                        is_special_motion_num_cmd(row.get("numCmd", ""))
                        and row_key in ambiguous_special_keys
                    ):
                        continue
                    filtered_results.append(row)
                results = filtered_results

    if query_has_explicit_strength and len(mentioned_chars) == 1 and not wants_comparison and not target_combo_query:
        resolved_row = resolved_query_rows.get(mentioned_chars[0])
        if (
            resolved_row
            and row_matches_explicit_strength(resolved_row, text_lower)
            and row_matches_query_move_terms(resolved_row)
        ):
            results = [resolved_row]
            special_prompt_blocks = []

    if special_prompt_blocks and (
        allow_explicit_special_prompt
        or
        (
            not query_has_explicit_strength
            and not query_requires_stocked
            and not query_requests_ca
        )
        or (special_grab_query and not results)
    ):
        results = []

    if (
        wants_comparison
        and len(mentioned_chars) >= 2
        and len(comparison_char_inputs) >= 2
        and not special_prompt_blocks
    ):
        scoped_comparison_rows = []
        for char in mentioned_chars:
            scoped_inputs = comparison_char_inputs.get(char, [])
            for scoped_input in scoped_inputs:
                scoped_row = lookup_frame_data(char, scoped_input)
                if scoped_row and scoped_row not in scoped_comparison_rows:
                    scoped_comparison_rows.append(scoped_row)
        if scoped_comparison_rows:
            results = scoped_comparison_rows

    if target_combo_query:
        if tc_prompt_blocks and not tc_selected_combos:
            results = []
        else:
            filtered_tc_results = []
            for row in results:
                num_cmd_compact = re.sub(r"\s+", "", str(row.get("numCmd", "")).lower())
                if ">" not in num_cmd_compact:
                    continue
                if tc_selected_combos and num_cmd_compact not in tc_selected_combos:
                    continue
                if tc_base_tokens and not any(
                    num_cmd_compact.startswith(f"{base}>") for base in tc_base_tokens
                ):
                    continue
                if row not in filtered_tc_results:
                    filtered_tc_results.append(row)
            if filtered_tc_results:
                results = filtered_tc_results
            elif tc_prompt_blocks:
                results = []

    # A stance or run named in the query ("aki stance hk", "marisa stance grab", "kimberly run mk") is answered
    # from the sheet's "<starter> > <follow-up>" rows, over any other reading of its words or prompt.
    if mentioned_chars:
        results, every_char_named_a_stance = apply_stance_rows(
            text_lower,
            mentioned_chars,
            results,
            FRAME_DATA,
            find_characters=lambda part: find_fuzzy_aliases_in_text(part, CHARACTER_ALIASES, FRAME_DATA.keys()),
        )
        if every_char_named_a_stance:
            tc_prompt_blocks = []
            special_prompt_blocks = []

    if not wants_comparison:
        results = prefer_grounded_rows(results, text_lower)

    # Format the results
    formatted_blocks = []

    stats_intent, results, formatted_blocks = apply_stats_context(
        text_lower,
        mentioned_chars,
        FRAME_STATS,
        lookup_frame_data,
        results,
        formatted_blocks,
        explicit_move_attempt=explicit_move_attempt,
        wants_frame_data=wants_frame_data,
        gif_query=gif_query,
        property_only_query=property_only_query,
        startup_alias_query=startup_alias_query,
        charge_button_query=charge_button_query,
        # A target combo's options are moves too, so "5HP > HP" never falls back to the stats panel.
        move_results_found=bool(results or tc_prompt_blocks),
    )
    wants_stats = stats_intent.wants_stats
    stats_only = stats_intent.stats_only
    stats_char_keys = list(stats_intent.character_keys)

    # 4. AUTO-INJECT KEY MOVES (Context Injection)
    # If we have a character but NO specific moves found (e.g. "Help me with Ryu"),
    # Optional private coaching may give advice about buttons, so provide data for likely buttons.
    # to prevent hallucinations (like saying 5MK is special cancellable when it isn't).
    results = filter_character_specific_final_results(
        text_lower,
        mentioned_chars,
        results,
        lookup_frame_data,
        query_has_explicit_strength=query_has_explicit_strength,
        frame_data=FRAME_DATA,
    )
    if (
        wants_frame_data
        and mentioned_chars
        and not results
        and not target_combo_query
        and not special_prompt_blocks
        and not explicit_move_attempt
    ):
        key_moves = ["5MP", "5MK", "2MK", "5HP", "2HP", "5HK", "2HK"]
        for char in mentioned_chars:
            for km in key_moves:
                k_row = lookup_frame_data(char, km)
                if k_row and k_row not in results:
                    results.append(k_row)

    if (
        wants_frame_data
        and mentioned_chars
        and explicit_move_attempt
        and not results
        and not tc_prompt_blocks
        and not special_prompt_blocks
    ):
        missing_scrolls_query = True

    for move_data in results:
        def clean(val):
            return str(val).replace('*', ',')

        startup = clean(move_data.get('startup', '-'))
        active = clean(move_data.get('active', '-'))
        recovery = clean(move_data.get('recovery', '-')).replace('(', ' (Whiff: ')
        cancel = clean(move_data.get('xx', '-'))
        damage = clean(move_data.get('dmg', '-'))
        guard = clean(move_data.get('atkLvl', '-'))
        atk_range = format_attack_range_for_table(move_data)
        on_hit = clean(move_data.get('onHit', '-'))
        on_block = clean(move_data.get('onBlock', '-'))
        extra_info = clean(move_data.get('extraInfo', '-')).replace('[', '').replace(']', '').replace('"', '')
        
        chip = clean(move_data.get("chp", "-"))
        ddoh = clean(move_data.get("DDoH", "-"))
        ddob = clean(move_data.get("DDoB", "-"))
        ssoh = clean(move_data.get("SelfSoH", "-"))
        ssob = clean(move_data.get("SelfSoB", "-"))

        gauge_info = (
            f"Chip Damage: {chip}\n"
            f"Drive Dmg: Hit {ddoh} / Block {ddob}\n"
            f"Super Gain: Hit {ssoh} / Block {ssob}\n"
        )
        
        # Hit Confirm Data (Always Included)
        hc_sp = clean(move_data.get('hcWinSpCa', '-')).strip() or '-'
        hc_tc = clean(move_data.get('hcWinTc', '-')).strip() or '-'
        hc_notes = clean(move_data.get('hcWinNotes', '-')).replace('[', '').replace(']', '').replace('"', '').strip() or '-'
        hc_info = (
            f"Hit Confirm (Sp/Su): {hc_sp} // Hit Confirm (TC): {hc_tc}\n"
            f"Hit Confirm Notes: {hc_notes}\n"
        )

        # Stun Data (Always Included)
        hstun = clean(move_data.get('hitstun', '-'))
        bstun = clean(move_data.get('blockstun', '-'))
        stun_info = f"Stun Frames: Hit {hstun} // Block {bstun}\n"

        block = (
            f"**{move_data['moveName']} ({move_data['numCmd']})**\n"
            f"Character: {move_data.get('char_name', 'Unknown')}\n"
            f"Startup: {startup} // Active: {active} // Recovery: {recovery}\n"
            f"Cancel: {cancel}\n"
            f"Damage: {damage}\n"
            f"Guard: {guard}\n"
            f"Range: {atk_range}\n"
            f"On Hit: {on_hit} // On Block: {on_block}\n"
            f"{gauge_info}"
            f"{stun_info}"
            f"{hc_info}"
            f"Notes: {extra_info}"
        )
        formatted_blocks.append(block)

    # 5. Attach TC/special prompts, punish verdict, and mode selection
    if tc_prompt_blocks:
        formatted_blocks.extend(tc_prompt_blocks)
    if special_prompt_blocks:
        formatted_blocks.extend(special_prompt_blocks)

    sections = []
    if formatted_blocks:
        sections.append("\n\n".join(formatted_blocks))

    output = "\n\n---\n".join(sections)

    # Check for punish calculation
    punish_verdict = check_punish(text_lower, results)
    if punish_verdict:
        if output:
            output = punish_verdict + "\n\n---\n\n" + output
        else:
            output = punish_verdict

    has_frame_blocks = bool(formatted_blocks)
    has_stats_blocks = bool(wants_stats and stats_char_keys)
    if stats_only and has_stats_blocks and not results:
        mode = "stats"
    elif has_frame_blocks:
        mode = "frame"
    else:
        mode = "none"

    return {
        "data": output,
        "mode": mode,
        "rows": results,
        "stats_query": wants_stats,
        "stats_only": stats_only,
        "stats_char_keys": stats_char_keys,
        "stats_keys": list(stats_intent.stat_keys) if stats_intent.stat_keys else None,
        "startup_alias_query": startup_alias_query,
        "hitconfirm_alias_query": hitconfirm_alias_query,
        "super_gain_alias_query": super_gain_alias_query,
        "range_alias_query": range_alias_query,
        "wants_comparison": bool(wants_comparison),
        "property_only_query": property_only_query,
        "target_combo_query": target_combo_query,
        "missing_scrolls_query": missing_scrolls_query,
        "gif_query": gif_query,
        "explicit_move_attempt": explicit_move_attempt,
    }
