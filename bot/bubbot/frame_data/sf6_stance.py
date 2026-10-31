"""SF6 stances and runs, read from the sheet.

FAT writes a stance or run as a starter row (A.K.I.'s 2PP Sinister Slide, Marisa's 214K Scutum,
Kimberly's 236K Sprint) and its follow-ups as "<starter> > <follow-up>" rows. A query that names a
starter ("aki stance", "coward crouch", "chun ss", "chun 214p") gets the starter; with more after it
("stance hk", "run mk", "stance grab", "214p hp") it gets the matching follow-up. Nothing here names a
character, so a new stance works as soon as FAT lists it.
"""

import re


# A starter is a row with follow-ups whose move or common name ends in one of these words.
STATE_WORDS = ("stance", "sway", "run", "sprint")
# Names players use that FAT doesn't, by the starter's move name.
COMMUNITY_NAMES = {"sinister slide": ("coward crouch",)}
BUTTONS = ("lp", "mp", "hp", "lk", "mk", "hk")
# Words that don't say which follow-up: property questions and glue.
FILLER = frozenset((
    "a", "an", "the", "s", "is", "its", "whats", "what", "how", "fast", "of", "on", "for", "into", "to", "then",
    "after", "from", "follow", "followup", "followups", "up", "frame", "frames", "data", "framedata", "startup",
    "active", "recovery", "block", "hit", "onblock", "onhit", "plus", "minus", "punish", "punishable", "safe",
    "unsafe", "damage", "dmg", "range", "gif", "gifs", "hitbox", "hitboxes", "image", "images", "cancel",
    "cancelable", "info", "move", "attack", "button", "please", "pls", "me", "show", "give", "advantage",
))
STOP_WORDS = frozenset(("vs", "versus", "and", "or", "compare", "compared"))
COMPARISON_SPLIT = re.compile(r"\b(?:vs|versus|and|against|compared\s+(?:to|with))\b")
SPEC_WORDS = {"punch": "p", "kick": "k", "throw": "lplk"}


def _words(text):
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def _phrase(text):
    return " ".join(_words(text))


def _step_keys(step):
    """One step of a command as a query can type it: '5HK' -> {'hk'}, '7/8/9' -> {'7', '8', '9'}."""
    step = re.sub(r"\(.*?\)", "", step).lower()
    keys = set()
    for part in step.split("/"):
        compact = re.sub(r"[^a-z0-9]", "", part)
        if re.fullmatch(r"5[a-z]+", compact):
            compact = compact[1:]
        if compact:
            keys.add(compact)
    return keys


def _followup_keys(suffix):
    steps = [step for step in suffix.split(">") if step.strip()]
    if len(steps) == 1:
        return _step_keys(steps[0])
    return {"".join(min(_step_keys(step) or {""}) for step in steps)}


def _name_words(row):
    """The follow-up's own words: what comes after the last '>' of its move and common names."""
    words = set()
    for field in ("moveName", "cmnName"):
        words.update(_words(str(row.get(field) or "").split(">")[-1]))
    return words


def starters(rows):
    """(starter row, its follow-up rows) for every stance or run in one character's rows."""
    commands = [str(row.get("numCmd") or "").strip() for row in rows]
    found = []
    for row, command in zip(rows, commands):
        if not command or ">" in command:
            continue
        # The state word ends the name ("Snake Stance", "Sprint > Arc Step"); Elena's "Sway Kick" is an attack.
        last_words = [(_words(str(row.get(field) or "").split(">")[0]) or [""])[-1] for field in ("moveName", "cmnName")]
        if not any(word in STATE_WORDS for word in last_words):
            continue
        followups = [other for other, other_command in zip(rows, commands) if other_command.startswith(f"{command} >")]
        if followups:
            found.append((row, followups))
    return found


def _starter_phrases(row, *, stance_named):
    """Each way a query can name the starter, with how sure that is: its names, input and initials beat a bare 'run'."""
    move, common = _phrase(row.get("moveName")), _phrase(row.get("cmnName"))
    phrases = {name: 3 for name in (move, common) if name}
    phrases[re.sub(r"[^a-z0-9]", "", str(row.get("numCmd") or "").lower())] = 3
    move_words = move.split()
    if len(move_words) >= 2 and move_words[0] != "od":
        phrases.setdefault("".join(word[0] for word in move_words), 2)
    for nickname in COMMUNITY_NAMES.get(move, ()):
        phrases[nickname] = 3
    words = set(move_words) | set(common.split())
    for word in STATE_WORDS:
        if word in words:
            phrases.setdefault(word, 1)
    # Dee Jay's Jus Cool is a sway, but people still call it his stance.
    if "sway" in words and not stance_named:
        phrases.setdefault("stance", 1)
    return phrases


def _pick_starter(text, found):
    """The starter the query names and the words after it, or (None, None)."""
    stance_named = any("stance" in _words(f"{row.get('moveName')} {row.get('cmnName')}") for row, _ in found)
    padded = f" {text} "
    best = None
    for row, followups in found:
        for phrase, score in _starter_phrases(row, stance_named=stance_named).items():
            index = padded.find(f" {phrase} ")
            if not phrase or index < 0:
                continue
            rank = (score, len(phrase))
            if best is None or rank > best[0]:
                best = (rank, row, followups, padded[index + len(phrase) + 2:])
    if best is None:
        return None, None, None
    return best[1], best[2], best[3]


def _spec(rest):
    """The words that pick a follow-up, without filler, up to a 'vs' or 'and'."""
    words = []
    for word in rest.split():
        if word in STOP_WORDS:
            break
        if word not in FILLER and word not in ("od", "ex"):
            words.append(word)
    return words


def _spec_key(words):
    """'hk hk' -> 'hkhk', 'kick' -> 'k', '5lplk' -> 'lplk'; None when a word isn't a button or input."""
    keys = []
    for word in words:
        word = SPEC_WORDS.get(word, word)
        if not re.fullmatch(r"[0-9]*(?:lplk|lp|mp|hp|lk|mk|hk|pp|kk|p|k)?", word):
            return None
        keys.append(word[1:] if re.fullmatch(r"5[a-z]+", word) else word)
    return "".join(keys) or None


def _steps(row):
    return str(row.get("numCmd") or "").count(">")


def _match_followups(followups, starter_command, words):
    keyed = [(row, _followup_keys(str(row.get("numCmd") or "")[len(starter_command):].lstrip(" >"))) for row in followups]
    key = _spec_key(words)
    if key:
        exact = [row for row, keys in keyed if key in keys]
        if exact:
            return exact
        if key in BUTTONS:
            # A.K.I.'s 5K takes any kick: "stance hk" is Heel Strike.
            generic = [row for row, keys in keyed if key[1] in keys]
            if generic:
                return generic
        if key in ("p", "k"):
            # Chun-Li's LK, MK and HK are all "stance kick".
            strengths = [row for row, keys in keyed if keys & {f"{strength}{key}" for strength in "lmh"}]
            if strengths:
                return strengths
    # By name: "stance grab", "run overhead", "overhead 2", Rashid's "run kick".
    wanted = set(words)
    named = [row for row in followups if wanted <= _name_words(row)]
    if not named:
        return []
    fewest = min((_steps(row), len(_name_words(row) - wanted)) for row in named)
    return [row for row in named if (_steps(row), len(_name_words(row) - wanted)) == fewest]


def find_stance_rows(text_lower, rows):
    """The rows a stance query asks for, or [] when the query names no stance or its follow-up isn't one of them."""
    found = starters(rows or [])
    if not found:
        return []
    text = " ".join(re.findall(r"[a-z0-9]+", re.sub(r"[>~,+]", " ", str(text_lower or "").lower())))
    starter, followups, rest = _pick_starter(text, found)
    if starter is None:
        return []
    # "marisa od stance": her OD Scutum has no follow-ups, so it isn't a starter; leave it to the parser.
    if {"od", "ex"} & set(text.split()) and not _phrase(starter.get("moveName")).startswith("od"):
        return []
    words = _spec(rest)
    if not words:
        return [starter]
    return _match_followups(followups, str(starter.get("numCmd") or "").strip(), words)


def _character_key(name):
    """'Chun-li' and 'chun-li', 'A.k.i' and 'a.k.i' alike: rows and the parser spell characters differently."""
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def apply_stance_rows(text_lower, mentioned_chars, results, frame_data, *, find_characters):
    """``results`` with each named stance's rows in place of that character's other rows, and whether every
    mentioned character named one. One character reads the whole query; "alex stance lp vs chun stance lp"
    reads each side for the character named in it."""
    if len(mentioned_chars) == 1:
        parts = {mentioned_chars[0]: text_lower}
    else:
        parts = {}
        for part in COMPARISON_SPLIT.split(text_lower):
            chars = find_characters(part)
            if len(chars) == 1 and chars[0] in mentioned_chars:
                parts.setdefault(chars[0], part)
    resolved = {}
    for char, part in parts.items():
        rows = find_stance_rows(part, frame_data.get(char, []))
        if rows:
            resolved[char] = rows
    if not resolved:
        return results, False
    merged = []
    for char in mentioned_chars:
        merged.extend(resolved.get(char) or [row for row in results if _character_key(row.get("char_name")) == _character_key(char)])
    mentioned_keys = {_character_key(char) for char in mentioned_chars}
    merged.extend(row for row in results if _character_key(row.get("char_name")) not in mentioned_keys)
    return merged, len(resolved) == len(mentioned_chars)
