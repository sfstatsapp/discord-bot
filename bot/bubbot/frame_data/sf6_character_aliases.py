"""SF6 character-specific alias dispatcher, stocked aliases, grab row collectors, Viper filters (stances: sf6_stance)."""

import re

from bubbot.frame_data.sf6_akuma_followups import apply_akuma_teleport_direction_aliases
from bubbot.frame_data.sf6_akuma_followups import collect_akuma_aliases
from bubbot.frame_data.sf6_akuma_followups import filter_akuma_air_fireball_results
from bubbot.frame_data.sf6_akuma_followups import filter_akuma_air_sa1_results
from bubbot.frame_data.sf6_akuma_followups import filter_akuma_followup_results
from bubbot.frame_data.sf6_akuma_followups import filter_akuma_non_air_sa3_results
from bubbot.frame_data.sf6_akuma_followups import filter_shoto_air_tatsu_results
from bubbot.frame_data.sf6_akuma_followups import infer_akuma_from_demon_terms
from bubbot.frame_data.sf6_deejay_followups import collect_deejay_sway_alias
from bubbot.frame_data.sf6_deejay_followups import filter_deejay_sway_results
from bubbot.frame_data.sf6_ken_aliases import collect_ken_aliases
from bubbot.frame_data.sf6_ken_aliases import filter_ken_jinrai_results
from bubbot.frame_data.sf6_cammy_followups import filter_cammy_hooligan_results
from bubbot.frame_data.sf6_parser_helpers import append_all_matching_aliases, append_first_matching_alias


def infer_character_mentions_from_terms(text_lower, frame_data, mentioned_chars):
    infer_akuma_from_demon_terms(text_lower, frame_data, mentioned_chars)


def create_character_filter_state():
    return {
        "akuma_followup_alias": None,
        "deejay_sway_followup_alias": None,
        "ken_jinrai_followup_alias": None,
    }


def query_requires_character_variant_state(text_tokens):
    return "denjin" in text_tokens


def row_matches_character_variant_state(row):
    move_name = str(row.get("moveName", "")).lower()
    cmn_name = str(row.get("cmnName", "")).lower()
    num_cmd = str(row.get("numCmd", "")).lower()
    return (
        "denjin" in move_name
        or "denjin" in cmn_name
        or "charged" in move_name
        or "charged" in cmn_name
        or "(charged)" in num_cmd
    )


def collect_character_specific_aliases(
    text_lower,
    extra_inputs,
    potential_inputs,
    *,
    mentioned_chars,
    air_sa1_context,
    air_sa2_context,
    air_sa3_context,
    query_requires_stocked,
    query_wants_od_strength,
    ken_run_followup_context,
):
    ken_jinrai_followup_alias = None
    akuma_followup_alias = None

    if "ken" in mentioned_chars:
        ken_jinrai_followup_alias = collect_ken_aliases(
            text_lower,
            extra_inputs,
            query_wants_od_strength=query_wants_od_strength,
            ken_run_followup_context=ken_run_followup_context,
        )

    if "mai" in mentioned_chars:
        collect_mai_aliases(text_lower, extra_inputs, air_sa2_context=air_sa2_context)

    if "jamie" in mentioned_chars:
        collect_jamie_aliases(text_lower, extra_inputs)

    if "akuma" in mentioned_chars:
        akuma_followup_alias, potential_inputs = collect_akuma_aliases(
            text_lower,
            extra_inputs,
            potential_inputs,
            air_sa1_context=air_sa1_context,
            air_sa3_context=air_sa3_context,
        )

    collect_simple_character_aliases(text_lower, extra_inputs, mentioned_chars=mentioned_chars)
    collect_stocked_aliases(
        text_lower,
        extra_inputs,
        mentioned_chars=mentioned_chars,
        query_requires_stocked=query_requires_stocked,
    )

    return {
        "filter_state": create_character_filter_state()
        | {
            "akuma_followup_alias": akuma_followup_alias,
            "ken_jinrai_followup_alias": ken_jinrai_followup_alias,
        },
        "potential_inputs": potential_inputs,
    }


def collect_late_character_specific_aliases(text_lower, extra_inputs, filter_state, *, mentioned_chars):
    has_od_denjin_fireball = bool(
        re.search(r"\b(ex|od)\s+denjin\s+(fireball|hadoken|hadouken)\b", text_lower)
    )
    if has_od_denjin_fireball:
        if "od denjin fireball" not in extra_inputs:
            extra_inputs.append("od denjin fireball")
    elif re.search(r"\bdenjin\s+(fireball|hadoken|hadouken)\b", text_lower):
        if "denjin fireball" not in extra_inputs:
            extra_inputs.append("denjin fireball")

    if "dee jay" in mentioned_chars:
        filter_state["deejay_sway_followup_alias"] = collect_deejay_sway_alias(text_lower, extra_inputs)
    return filter_state


def collect_character_followup_contexts(text_lower, mentioned_chars):
    return {
        "run_followup": get_ken_run_followup_context(text_lower, mentioned_chars),
    }


def adjust_character_specific_keyword_inputs(text_lower, mentioned_chars, extra_inputs):
    return apply_akuma_teleport_direction_aliases(text_lower, mentioned_chars, extra_inputs)


def filter_character_specific_final_results(
    text_lower,
    mentioned_chars,
    results,
    lookup_frame_data,
    *,
    query_has_explicit_strength,
    frame_data,
):
    results = filter_viper_air_burnkick_results(
        text_lower,
        mentioned_chars,
        results,
        lookup_frame_data,
        query_has_explicit_strength=query_has_explicit_strength,
    )
    return filter_jamie_drink_level_results(text_lower, mentioned_chars, results, frame_data)


def apply_character_specific_result_filters(
    text_lower,
    text_tokens,
    mentioned_chars,
    results,
    *,
    filter_state,
    air_tatsu_context,
    air_fireball_context,
    air_sa1_context,
    query_requests_sa3,
    air_sa3_context,
    query_wants_od_strength,
    normalize_char_name,
    lookup_frame_data,
    row_is_ca_variant,
    cammy_hooligan_followup_context,
):
    results = filter_cammy_hooligan_results(
        results,
        followup_context=cammy_hooligan_followup_context,
        normalize_char_name=normalize_char_name,
    )

    if air_tatsu_context and results:
        results = filter_shoto_air_tatsu_results(
            results,
            mentioned_chars=mentioned_chars,
            normalize_char_name=normalize_char_name,
        )

    if air_fireball_context and results:
        results = filter_akuma_air_fireball_results(
            results,
            mentioned_chars=mentioned_chars,
            text_tokens=text_tokens,
            query_wants_od_strength=query_wants_od_strength,
            normalize_char_name=normalize_char_name,
            lookup_frame_data=lookup_frame_data,
        )

    if air_sa1_context and results:
        results = filter_akuma_air_sa1_results(
            results,
            mentioned_chars=mentioned_chars,
            normalize_char_name=normalize_char_name,
            lookup_frame_data=lookup_frame_data,
        )

    if (query_requests_sa3 or air_sa3_context) and results:
        results = filter_akuma_non_air_sa3_results(
            results,
            mentioned_chars=mentioned_chars,
            normalize_char_name=normalize_char_name,
            row_is_ca_variant=row_is_ca_variant,
            lookup_frame_data=lookup_frame_data,
        )

    results = filter_jamie_special_results(text_lower, mentioned_chars, results)

    if filter_state.get("akuma_followup_alias") and results:
        results = filter_akuma_followup_results(results, filter_state["akuma_followup_alias"], normalize_char_name)
    if filter_state.get("ken_jinrai_followup_alias") and results:
        results = filter_ken_jinrai_results(results, filter_state["ken_jinrai_followup_alias"], normalize_char_name)
    if filter_state.get("deejay_sway_followup_alias") and results:
        results = filter_deejay_sway_results(results, filter_state["deejay_sway_followup_alias"], normalize_char_name)
    return results


def collect_mai_aliases(text_lower, extra_inputs, *, air_sa2_context):
    fan_aliases = [
        (r"\b(?:od|ex)\s+(?:stocked|stock)\s+(?:hold|held|charged)\s+fan\b", "od stocked hold fan"),
        (r"\b(?:stocked|stock)\s+(?:od|ex)\s+(?:hold|held|charged)\s+fan\b", "od stocked hold fan"),
        (r"\b(?:stocked|stock)\s+(?:hold|held|charged)\s+fan\b", "stocked hold fan"),
        (r"\b(?:od|ex)\s+(?:hold|held|charged)\s+fan\b", "od hold fan"),
        (r"\b(?:hold|held|charged)\s+fan\b", "hold fan"),
        (r"\b(?:od|ex)\s+(?:stocked|stock)\s+fan\b", "od stocked fan"),
        (r"\b(?:stocked|stock)\s+(?:od|ex)\s+fan\b", "od stocked fan"),
        (r"\b(?:stocked|stock)\s+fan\b", "stocked fan"),
        (r"\b(?:od|ex)\s+fan\b", "od fan"),
        (r"\b(?:l|light|lp)\s+fan\b", "l fan"),
        (r"\b(?:m|medium|mp)\s+fan\b", "m fan"),
        (r"\b(?:h|heavy|hp)\s+fan\b", "h fan"),
        (r"\bfan\b", "fan"),
    ]
    append_first_matching_alias(text_lower, extra_inputs, fan_aliases)
    if air_sa2_context and "air sa2" not in extra_inputs:
        extra_inputs.append("air sa2")


def collect_jamie_aliases(text_lower, extra_inputs):
    drink_alias = None
    if re.search(
        r"\b(?:drink|dr\s*4)\s+activation\b|\blevel\s*4\s+activation\b|\bactivation\s+drink\b",
        text_lower,
    ):
        drink_alias = "drink activation"
    elif re.search(r"\b(?:drink\s*(?:level\s*)?4|level\s*4\s*drink|4\s*drinks?|four\s+drinks?)\b", text_lower):
        drink_alias = "drink level 4"
    elif re.search(r"\b(?:drink\s*(?:level\s*)?3|level\s*3\s*drink|3\s*drinks?|three\s+drinks?)\b", text_lower):
        drink_alias = "drink level 3"
    elif re.search(r"\b(?:drink\s*(?:level\s*)?2|level\s*2\s*drink|2\s*drinks?|two\s+drinks?)\b", text_lower):
        drink_alias = "drink level 2"
    elif re.search(r"\b(?:drink\s*(?:level\s*)?1|level\s*1\s*drink|1\s*drink|one\s+drink)\b", text_lower):
        drink_alias = "drink level 1"
    elif re.search(r"\bdrink\b", text_lower):
        drink_alias = "drink"

    drink_level = jamie_drink_level_from_text(text_lower)
    if drink_level is not None and drink_alias is None:
        drink_alias = f"drink level {drink_level}"

    drink_is_state_modifier = drink_alias and re.search(
        r"\b(?!22p\b)[1-9][0-9]*(?:lp|mp|hp|lk|mk|hk|pp|kk|p|k)\b",
        text_lower,
    )
    if drink_alias and not drink_is_state_modifier and drink_alias not in extra_inputs:
        extra_inputs.insert(0, drink_alias)

    alias_groups = [
        [
            (r"\b(?:od|ex)\s+(?:palm|swagger(?:\s+step)?)\b", "od palm"),
            (r"\b(?:l|light|lp)\s+(?:palm|swagger(?:\s+step)?)\b", "lp palm"),
            (r"\b(?:m|medium|mp)\s+(?:palm|swagger(?:\s+step)?)\b", "mp palm"),
            (r"\b(?:h|heavy|hp)\s+(?:palm|swagger(?:\s+step)?)\b", "hp palm"),
            (r"\b(?:palm|swagger(?:\s+step)?)\b", "palm"),
        ],
        [
            (r"\b(?:od|ex)\s+(?:rekka|freeflow(?:\s+strikes)?)\b", "od rekka"),
            (r"\b(?:l|light|lp)\s+(?:rekka|freeflow(?:\s+strikes)?)\b", "lp rekka"),
            (r"\b(?:m|medium|mp)\s+(?:rekka|freeflow(?:\s+strikes)?)\b", "mp rekka"),
            (r"\b(?:h|heavy|hp)\s+(?:rekka|freeflow(?:\s+strikes)?)\b", "hp rekka"),
            (r"\b(?:rekka|freeflow(?:\s+strikes)?)\b", "rekka"),
        ],
        [
            (r"\b(?:od|ex)\s+(?:arrow\s+kick|up\s*kicks?|upkicks?)\b", "od arrow kick"),
            (r"\b(?:l|light|lk)\s+(?:arrow\s+kick|up\s*kicks?|upkicks?)\b", "l arrow kick"),
            (r"\b(?:m|medium|mk)\s+(?:arrow\s+kick|up\s*kicks?|upkicks?)\b", "m arrow kick"),
            (r"\b(?:h|heavy|hk)\s+(?:arrow\s+kick|up\s*kicks?|upkicks?)\b", "h arrow kick"),
            (r"\b(?:arrow\s+kick|up\s*kicks?|upkicks?)\b", "arrow kick"),
        ],
        [
            (r"\b(?:od|ex)\s+(?:bakkai|break\s*dance)\b|\b236kk\b", "od bakkai"),
            (r"\b(?:l|light|lk)\s+(?:bakkai|break\s*dance)\b|\b236lk\b", "lk bakkai"),
            (r"\b(?:m|medium|mk)\s+(?:bakkai|break\s*dance)\b|\b236mk\b", "mk bakkai"),
            (r"\b(?:h|heavy|hk)\s+(?:bakkai|break\s*dance)\b|\b236hk\b", "hk bakkai"),
            (r"\b(?:bakkai|break\s*dance)\b|\b236k\b", "bakkai"),
        ],
        [
            (r"\b(?:od|ex)\s+(?:luminous\s+)?dive\s+kick\b|\b(?:od|ex)\s+divekick\b|\b214kk\b|\bj\.?214kk\b", "od dive kick"),
            (r"\b(?:l|light|lk)\s+(?:luminous\s+)?dive\s+kick\b|\b(?:l|light|lk)\s+divekick\b|\b214lk\b|\bj\.?214lk\b", "dive kick"),
            (r"\b(?:m|medium|mk)\s+(?:luminous\s+)?dive\s+kick\b|\b(?:m|medium|mk)\s+divekick\b|\b214mk\b|\bj\.?214mk\b", "dive kick"),
            (r"\b(?:h|heavy|hk)\s+(?:luminous\s+)?dive\s+kick\b|\b(?:h|heavy|hk)\s+divekick\b|\b214hk\b|\bj\.?214hk\b", "dive kick"),
            (r"\b(?:luminous\s+)?dive\s+kick\b|\bdivekick\b|\b214k\b|\bj\.?214k\b", "dive kick"),
        ],
        [
            (r"\b(?:od|ex)\s+(?:tenshin|command\s+grab)\b", "od tenshin"),
            (r"\b(?:tenshin|command\s+grab)\b", "tenshin"),
        ],
        [
            (r"\b(?:od|ex)\s+(?:swagger\s+hermit\s+punch|hermit\s+punch|palm\s+follow(?:-?up)?)\b", "od swagger hermit punch"),
            (r"\b(?:l|light|lp)\s+(?:swagger\s+hermit\s+punch|hermit\s+punch|palm\s+follow(?:-?up)?)\b", "lp swagger hermit punch"),
            (r"\b(?:m|medium|mp)\s+(?:swagger\s+hermit\s+punch|hermit\s+punch|palm\s+follow(?:-?up)?)\b", "mp swagger hermit punch"),
            (r"\b(?:h|heavy|hp)\s+(?:swagger\s+hermit\s+punch|hermit\s+punch|palm\s+follow(?:-?up)?)\b", "hp swagger hermit punch"),
            (r"\b(?:swagger\s+hermit\s+punch|hermit\s+punch|palm\s+follow(?:-?up)?)\b", "swagger hermit punch"),
        ],
    ]
    for aliases in alias_groups:
        append_first_matching_alias(text_lower, extra_inputs, aliases)


def jamie_drink_level_from_text(text_lower):
    numeric_match = re.search(
        r"\b(?:d\s*|dl\s*|drinks?\s*(?:(?:level|lvl|lv)\s*)?)([0-4])\s*\+?"
        r"|\b(?:level|lvl|lv)\s*([0-4])\s*\+?\s*drinks?\b"
        r"|\b([0-4])\s*\+?\s*drinks?\b",
        text_lower,
    )
    if numeric_match:
        return int(next(group for group in numeric_match.groups() if group))

    word_levels = {"one": 1, "two": 2, "three": 3, "four": 4}
    word_match = re.search(r"\b(one|two|three|four)\s+drinks?\b", text_lower)
    return word_levels.get(word_match.group(1)) if word_match else None


def collect_simple_character_aliases(text_lower, extra_inputs, *, mentioned_chars):
    if "jp" in mentioned_chars:
        append_first_matching_alias(text_lower, extra_inputs, [
            (r"\b(?:od|ex)\s+swipe\b", "od swipe"),
            (r"\b(?:l|light|lp)\s+swipe\b", "l swipe"),
            (r"\b(?:m|medium|mp)\s+swipe\b", "m swipe"),
            (r"\b(?:h|heavy|hp)\s+swipe\b", "h swipe"),
            (r"\bswipe\b", "swipe"),
        ])
    if "a.k.i" in mentioned_chars:
        append_first_matching_alias(text_lower, extra_inputs, [
            (r"\b(?:od|ex)\s+whip\b", "od whip"),
            (r"\b(?:l|light|lp)\s+whip\b", "l whip"),
            (r"\b(?:m|medium|mp)\s+whip\b", "m whip"),
            (r"\b(?:h|heavy|hp)\s+whip\b", "h whip"),
            (r"\bwhip\b", "whip"),
        ])
    if "luke" in mentioned_chars:
        append_first_matching_alias(text_lower, extra_inputs, [
            (r"\b(?:charged|hold|held)\s+(?:l|light|lp)\s+(?:flash\s+)?knuckle\b", "charged light knuckle"),
            (r"\b(?:l|light|lp)\s+(?:charged|hold|held)\s+(?:flash\s+)?knuckle\b", "charged light knuckle"),
            (r"\b(?:charged|hold|held)\s+(?:m|medium|mp)\s+(?:flash\s+)?knuckle\b", "charged medium knuckle"),
            (r"\b(?:m|medium|mp)\s+(?:charged|hold|held)\s+(?:flash\s+)?knuckle\b", "charged medium knuckle"),
            (r"\b(?:charged|hold|held)\s+(?:h|heavy|hp)\s+(?:flash\s+)?knuckle\b", "charged heavy knuckle"),
            (r"\b(?:h|heavy|hp)\s+(?:charged|hold|held)\s+(?:flash\s+)?knuckle\b", "charged heavy knuckle"),
            (r"\b(?:charged|hold|held)\s+(?:flash\s+)?knuckle\b", "charged knuckle"),
        ])


def collect_stocked_aliases(text_lower, extra_inputs, *, mentioned_chars, query_requires_stocked):
    if not query_requires_stocked:
        return
    if "lily" in mentioned_chars:
        append_all_matching_aliases(text_lower, extra_inputs, [
            (r"\b(?:stocked|stock|windclad|wind\s+clad)\s+(?:condor\s+)?spire\b", "stocked spire"),
            (r"\b(?:stocked|stock|windclad|wind\s+clad)\s+(?:tomahawk|tomahawk\s+buster)\b", "stocked tomahawk"),
            (r"\b(?:stocked|stock|windclad|wind\s+clad|wind\s+stock)\s+(?:condor\s+)?wind\b", "stocked condor wind"),
        ])
    if "mai" in mentioned_chars:
        append_all_matching_aliases(text_lower, extra_inputs, [
            (r"\b(?:od|ex)\s+(?:stocked|stock)\s+(?:fireball|kachousen)\b", "od stocked fireball"),
            (r"\b(?:stocked|stock)\s+(?:od|ex)\s+(?:fireball|kachousen)\b", "od stocked fireball"),
            (r"\b(?:od|ex)\s+(?:stocked|stock)\s+(?:dp|ryuuenjin)\b", "od stocked dp"),
            (r"\b(?:stocked|stock)\s+(?:od|ex)\s+(?:dp|ryuuenjin)\b", "od stocked dp"),
            (r"\b(?:od|ex)\s+(?:stocked|stock)\s+(?:twirl|ryuuenbu)\b", "od stocked twirl"),
            (r"\b(?:stocked|stock)\s+(?:od|ex)\s+(?:twirl|ryuuenbu)\b", "od stocked twirl"),
            (r"\b(?:od|ex)\s+(?:stocked|stock)\s+(?:cartwheel|shinobi\s+bachi)\b", "od stocked cartwheel"),
            (r"\b(?:stocked|stock)\s+(?:od|ex)\s+(?:cartwheel|shinobi\s+bachi)\b", "od stocked cartwheel"),
            (r"\b(?:od|ex)\s+(?:stocked|stock)\s+(?:dive\s+kick|divekick|musasabi(?:\s+no\s+mai)?)\b", "od stocked dive kick"),
            (r"\b(?:stocked|stock)\s+(?:od|ex)\s+(?:dive\s+kick|divekick|musasabi(?:\s+no\s+mai)?)\b", "od stocked dive kick"),
            (r"\b(?:stocked|stock)\s+(?:fireball|kachousen)\b", "stocked fireball"),
            (r"\b(?:stocked|stock)\s+(?:dp|ryuuenjin)\b", "stocked dp"),
            (r"\b(?:stocked|stock)\s+(?:twirl|ryuuenbu)\b", "stocked twirl"),
            (r"\b(?:stocked|stock)\s+(?:cartwheel|shinobi\s+bachi)\b", "stocked cartwheel"),
            (r"\b(?:stocked|stock)\s+(?:dive\s+kick|divekick|musasabi(?:\s+no\s+mai)?)\b", "stocked dive kick"),
            (r"\b(?:stocked|stock)\s+(?:sa\s*1|super\s*art\s*1|super\s*1|level\s*1)\b", "stocked sa1"),
            (r"\b(?:stocked|stock)\s+(?:air\s+)?(?:sa\s*2|super\s*art\s*2|super\s*2|level\s*2)\b", "stocked air sa2"),
            (r"\b(?:air\s+)?(?:stocked|stock)\s+(?:sa\s*2|super\s*art\s*2|super\s*2|level\s*2)\b", "stocked air sa2"),
        ])
    if "juri" in mentioned_chars:
        append_all_matching_aliases(text_lower, extra_inputs, [
            (r"\b(?:stocked|stock)\s+(?:fireball|saihasho|fuha\s+release)\b", "stocked fireball"),
            (r"\b(?:stocked|stock)\s+(?:axe\s+kick|ankensatsu)\b", "stocked axe kick"),
            (r"\b(?:stocked|stock)\s+(?:spinning\s+kicks?|go\s+ohsatsu)\b", "stocked spinning kicks"),
            (r"\b(?:stocked|stock)\s+(?:air\s+)?sa\s*1\b", "stocked sa1"),
            (r"\b(?:air\s+)?(?:stocked|stock)\s+sa\s*1\b", "stocked sa1"),
        ])


def collect_lily_typhoon_rows(text_lower, mentioned_chars, results, lookup_frame_data):
    if "lily" not in mentioned_chars:
        return

    typhoon_patterns = [
        ("l typhoon", "l typhoon"),
        ("m typhoon", "m typhoon"),
        ("h typhoon", "h typhoon"),
        ("light typhoon", "light typhoon"),
        ("medium typhoon", "medium typhoon"),
        ("heavy typhoon", "heavy typhoon"),
        ("od typhoon", "od typhoon"),
        ("ex typhoon", "ex typhoon"),
        ("mexican typhoon", "mexican typhoon"),
        ("typhoon", "typhoon"),
    ]
    for pattern, alias_key in typhoon_patterns:
        if pattern in text_lower:
            row = lookup_frame_data("lily", alias_key)
            if row and row not in results:
                results.append(row)
            break


def filter_jamie_special_results(text_lower, mentioned_chars, results):
    if "jamie" not in mentioned_chars or not results:
        return results
    if jamie_drink_level_from_text(text_lower) is not None and re.search(
        r"\b(?!22p\b)[1-9][0-9]*(?:lp|mp|hp|lk|mk|hk|pp|kk|p|k)\b",
        text_lower,
    ):
        return results
    if not re.search(
        r"\b(?:rekka|freeflow|palm|swagger|arrow\s+kick|upkicks?|drink(?:\s+activation)?|activation)\b",
        text_lower,
    ):
        return results

    jamie_special_rows = [
        row
        for row in results
        if str(row.get("moveType", "")).strip().lower()
        in {"special", "movement-special", "super", "command-grab"}
    ]
    return jamie_special_rows or results


def filter_jamie_drink_level_results(text_lower, mentioned_chars, results, frame_data):
    drink_level = jamie_drink_level_from_text(text_lower)
    if "jamie" not in mentioned_chars or drink_level is None or not results:
        return results
    drink_is_state_modifier = bool(
        re.search(
            r"\b(?!22p\b)[1-9][0-9]*(?:lp|mp|hp|lk|mk|hk|pp|kk|p|k)\b",
            text_lower,
        )
    )

    def row_key(row):
        return tuple(str(row.get(field, "")).strip().lower() for field in ("moveName", "numCmd"))

    variants_by_key = {}
    for row in frame_data.get("jamie", []):
        variants_by_key.setdefault(row_key(row), []).append(row)

    filtered = []
    for row in results:
        if str(row.get("char_name", "")).strip().lower() != "jamie":
            filtered.append(row)
            continue
        if drink_is_state_modifier and str(row.get("moveName", "")).lower().startswith("the devil inside"):
            continue
        eligible = [
            candidate
            for candidate in variants_by_key.get(row_key(row), [])
            if int(candidate.get("_jamie_drink_level", 0) or 0) <= drink_level
        ]
        if not eligible:
            continue
        selected = max(eligible, key=lambda candidate: int(candidate.get("_jamie_drink_level", 0) or 0))
        if selected not in filtered:
            filtered.append(selected)
    return filtered


def get_ken_run_followup_context(text_lower, mentioned_chars):
    return "ken" in mentioned_chars and bool(
        re.search(r"\brun\s+(?:dp|shoryu|shoryuken|tatsu|dragonlash|dragon\s+lash|lash)\b", text_lower)
    )


def should_skip_keyword_input(token, text_lower):
    return token == "ball" and "blanka" not in text_lower


def expand_character_candidate_names(char, candidate_names):
    if char != "sagat":
        return candidate_names
    expanded = list(candidate_names)
    for name_variant in list(candidate_names):
        tigerless_variant = re.sub(r"\btiger\b", "", name_variant)
        tigerless_variant = re.sub(r"\s+", " ", tigerless_variant).strip()
        if tigerless_variant and tigerless_variant != name_variant and tigerless_variant not in expanded:
            expanded.append(tigerless_variant)
    return expanded


def should_append_single_token_candidate(char, candidate_tokens, text_tokens, text_lower):
    if char == "sagat" and len(candidate_tokens) == 1 and len(candidate_tokens[0]) >= 4:
        return candidate_tokens[0] in text_tokens
    return (
        char == "ken"
        and len(candidate_tokens) == 1
        and candidate_tokens[0] == "run"
        and "run" in text_tokens
        and not re.search(
            r"\brun\s+(?:stop|overhead|step|dp|shoryu|shoryuken|tatsu|dragonlash|dragon\s+lash|lash)\b",
            text_lower,
        )
    )


def collect_character_specific_rows(
    text_lower,
    mentioned_chars,
    results,
    lookup_frame_data,
    *,
    query_has_explicit_strength,
    zangief_borscht_context,
):
    for char in mentioned_chars:
        if not (char == "zangief" and zangief_borscht_context):
            spd_patterns = [
                ("l spd", "lp"), ("m spd", "mp"), ("h spd", "hp"),
                ("light spd", "lp"), ("medium spd", "mp"), ("heavy spd", "hp"),
                ("lspd", "lp"), ("mspd", "mp"), ("hspd", "hp"),
                ("od spd", "od"), ("ex spd", "od"),
                ("l command grab", "lp"), ("m command grab", "mp"), ("h command grab", "hp"),
                ("light command grab", "lp"), ("medium command grab", "mp"), ("heavy command grab", "hp"),
                ("od command grab", "od"), ("ex command grab", "od"),
                ("360+lp", "lp"), ("360+mp", "mp"), ("360+hp", "hp"), ("360+pp", "od"),
                ("360lp", "lp"), ("360mp", "mp"), ("360hp", "hp"), ("360pp", "od"),
                ("command grab", ""), ("spd", ""), ("360", ""),
            ]
            for pattern, strength in spd_patterns:
                if pattern in text_lower:
                    if not strength and query_has_explicit_strength:
                        continue
                    move_names = (
                        [f"{strength} command grab", f"{strength} screw piledriver", f"{strength} mexican typhoon"]
                        if strength
                        else ["command grab", "screw piledriver", "mexican typhoon"]
                    )
                    for move_name in move_names:
                        row = lookup_frame_data(char, move_name)
                        if row and row not in results:
                            results.append(row)
                            break
                    break

        if char == "zangief" and zangief_borscht_context:
            borscht_lookup = "od borscht dynamite" if (
                re.search(r"\b(?:od|ex)\s+borscht\b", text_lower)
                or re.search(r"\bj\.?\s*360\s*\+?\s*kk\b", text_lower)
                or re.search(r"\bj\s+360\s*\+?\s*kk\b", text_lower)
            ) else "borscht dynamite"
            row = lookup_frame_data(char, borscht_lookup)
            if row and row not in results:
                results.append(row)

    collect_lily_typhoon_rows(text_lower, mentioned_chars, results, lookup_frame_data)


def filter_viper_air_burnkick_results(
    text_lower,
    mentioned_chars,
    results,
    lookup_frame_data,
    *,
    query_has_explicit_strength,
):
    viper_air_burnkick_query = bool(
        "c.viper" in mentioned_chars
        and re.search(
            r"\b(?:air|aerial)\s+burn(?:ing)?\s+kicks?\b"
            r"|\b(?:air|aerial)\s+burnkicks?\b"
            r"|\bburn(?:ing)?\s+kicks?\s+(?:air|aerial)\b"
            r"|\bburnkicks?\s+(?:air|aerial)\b"
            r"|\bj\.?\s*236k\b"
            r"|\b236k\s*(?:\(air\)|air|aerial)\b",
            text_lower,
        )
    )
    if not viper_air_burnkick_query or not query_has_explicit_strength or not results:
        return results

    preferred_air_input = "air burn kick"
    if re.search(r"\b(?:od|ex|236kk)\b", text_lower):
        preferred_air_input = "od air burn kick"
    elif re.search(r"\b(?:h|heavy|hk|236hk)\b", text_lower):
        preferred_air_input = "h air burn kick"
    elif re.search(r"\b(?:m|medium|mk|236mk)\b", text_lower):
        preferred_air_input = "m air burn kick"
    elif re.search(r"\b(?:l|light|lk|236lk)\b", text_lower):
        preferred_air_input = "l air burn kick"

    preferred_air_row = lookup_frame_data("c.viper", preferred_air_input)
    if preferred_air_row and preferred_air_row not in results:
        results.insert(0, preferred_air_row)

    filtered_results = []
    for row in results:
        move_name = str(row.get("moveName", "")).lower()
        cmn_name = str(row.get("cmnName", "")).lower()
        num_cmd = str(row.get("numCmd", "")).lower()
        burnkick_row = "burn" in move_name or "burn" in cmn_name
        if not burnkick_row:
            filtered_results.append(row)
            continue
        if "(air" in num_cmd or "air" in move_name or "air" in cmn_name or "aerial" in move_name or "aerial" in cmn_name:
            filtered_results.append(row)
    return filtered_results or results
