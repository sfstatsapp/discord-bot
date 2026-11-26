"""Rankings over Bub's frame data: "the move with the most range", "fastest normal", "Ken's most plus move on block".

The game is Street Fighter 6 unless the message names another (decisions.pick_game). In one request the
Decisions API then picks the character (or all of them), the metric, which end of
the ranking, the move type, a trait (anti-air, projectile, air move) and how many moves, each from what that game's
table actually has. The ranking itself is computed here from Bub's rows, so every number is Bub's.
"""

import math
import re
from dataclasses import dataclass, field

import discord

from bubbot.data.aliases import CHARACTER_ALIASES
from bubbot.runtime import decisions
from bubbot.utils.character_lookup import find_aliases_in_text
from bubbot.utils.choice_utils import character_choices
from bubbot.utils.sfstats_style import BRAND
from bubbot.utils.emoji_text import strip_embed_emojis

# Columns a ranking can use, with how the model should read them. A game offers the ones its table fills.
METRICS = {
    "startup": ("Startup", "frames before the move can hit; lowest is fastest"),
    "active": ("Active frames", "how long the hit lasts; highest stays out longest"),
    "recovery": ("Recovery", "frames after the hit before acting again; lowest recovers fastest"),
    "total": ("Total frames", "the whole move; lowest is shortest"),
    "onHit": ("On hit", "frame advantage on hit; highest is most plus on hit"),
    "onBlock": ("On block", "frame advantage on block; highest is safest or most plus, lowest is most punishable"),
    "onPC": ("On punish counter", "frame advantage on a punish counter hit"),
    "dmg": ("Damage", "damage on hit; highest hits hardest"),
    "chp": ("Chip damage", "damage dealt through block"),
    "range": ("Range", "how far the move reaches; highest reaches furthest"),
    "hitstun": ("Hitstun", "frames the opponent is stunned on hit"),
    "blockstun": ("Blockstun", "frames the opponent is stuck blocking"),
    "hitstop": ("Hitstop", "freeze frames on contact"),
    "DDoH": ("Drive damage on hit", "drive gauge the opponent loses on hit"),
    "DDoB": ("Drive damage on block", "drive gauge the opponent loses on block"),
    "DGain": ("Drive gain", "drive gauge gained"),
    "SelfSoH": ("Super gain on hit", "super meter gained on hit"),
    "SelfSoB": ("Super gain on block", "super meter gained on block"),
}
SIGNED = {"onHit", "onBlock", "onPC"}
TRAITS = {"antiAirMove": "anti-air", "projectile": "projectile", "airmove": "air move"}
SORTS = {"desc": "Highest first: most, longest, furthest, hardest-hitting, most plus, safest on block.",
         "asc": "Lowest first: fastest startup, least, shortest, most minus or most punishable on block."}
DEFAULT_COUNT, MAX_COUNT = 5, 10
MIN_VALUES = 5

RANKING_HINT = re.compile(
    r"\b(?:most|least|fastest|quickest|slowest|longest|shortest|highest|lowest|biggest|smallest|furthest|farthest|"
    r"best|worst|safest|unsafest|strongest|weakest|top\s*\d+|plus-?est|minus-?est|which\s+moves?|what\s+moves?|"
    r"rank(?:ing)?\s+(?:the\s+)?moves?)\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)")


def is_ranking(text):
    return bool(RANKING_HINT.search(str(text or "")))


def number(value):
    """The leading number of a cell ("+3", "-2", "800 (1000)", "1.22"), or None ("KD +30", "-", empty)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return None if isinstance(value, float) and math.isnan(value) else float(value)
    match = _NUMBER.match(str(value))
    return float(match.group(1)) if match else None


def truthy(value):
    if isinstance(value, float) and math.isnan(value):
        return False
    return str(value or "").strip().lower() not in {"", "0", "false", "no", "nan", "none", "-"}


@dataclass
class Offer:
    """What one game's table can rank by."""
    characters: dict = field(default_factory=dict)   # display → key
    metrics: list = field(default_factory=list)
    kinds: list = field(default_factory=list)
    traits: list = field(default_factory=list)


def offer(frame_data):
    result = Offer(characters={display: key for key, display in character_choices({k: v for k, v in frame_data.items() if v})})
    counts, kinds, traits = {}, {}, set()
    for rows in frame_data.values():
        for row in rows or []:
            for metric in METRICS:
                if number(row.get(metric)) is not None:
                    counts[metric] = counts.get(metric, 0) + 1
            kind = str(row.get("moveType") or "").strip().lower()
            if kind and len(kind) <= 30 and kind != "nan":
                kinds[kind] = kinds.get(kind, 0) + 1
            traits.update(trait for trait in TRAITS if truthy(row.get(trait)))
    result.metrics = [metric for metric in METRICS if counts.get(metric, 0) >= MIN_VALUES]
    result.kinds = [kind for kind, count in sorted(kinds.items(), key=lambda item: -item[1]) if count >= 2][:40]
    result.traits = [trait for trait in TRAITS if trait in traits]
    return result


def named_character(text, game, available):
    """The one character the message names outright ("marisa longest range"), by the table's names and, for SF6,
    Bub's nicknames ("dj", "gief"); None when it names none or several."""
    keys = list(available.characters.values())
    aliases = {display: key for display, key in available.characters.items()}
    if game.key == "sf6":
        aliases.update({alias: canonical for alias, canonical in CHARACTER_ALIASES.items() if canonical in keys})
    found = find_aliases_in_text(text, aliases, keys)
    if len(found) != 1:
        return None
    return next(display for display, key in available.characters.items() if key == found[0])


def count_option(n):
    return f"{n} {'move' if n == 1 else 'moves'}"


def questions(game, available):
    return {
        "character": decisions.choice(
            decisions.options(sorted(available.characters), "No particular character: rank every character's moves."),
            f"Which {game.label} character's moves does the request rank? none when it asks about all characters or names none."),
        "metric": decisions.choice(
            decisions.options(available.metrics, "Nothing a frame data table measures.",
                              describe=lambda key: f"{METRICS[key][0]}: {METRICS[key][1]}"),
            "Which number does the request rank moves by? \"fastest\" is startup, \"most plus\" or \"safest\" is on block, "
            "\"reach\" or \"range\" is range, \"hardest hitting\" is damage."),
        "sort": decisions.choice(decisions.options(SORTS, "No direction is asked.", describe=SORTS.get),
                                 "Which end of that ranking does the request want?"),
        "kind": decisions.choice(
            decisions.options(available.kinds, "Any kind of move."),
            "Only moves of this kind, when the request names one (\"normals\", \"supers\", \"specials\", \"throws\")."),
        "trait": decisions.choice(
            decisions.options(available.traits, "No such filter.", describe=lambda key: TRAITS[key]),
            "Only moves with this trait, when the request names it (\"anti-air\", \"projectile\" or \"fireball\", \"air move\")."),
        "count": decisions.choice(
            decisions.options([count_option(n) for n in range(1, MAX_COUNT + 1)], "No number of moves is given."),
            "How many moves the request asks for: \"the move with\" is 1 move, \"top 3\" is 3 moves; none when unsaid."),
    }


@dataclass(frozen=True)
class Ranking:
    game: object
    character: str
    metric: str
    sort: str
    kind: str
    trait: str
    count: int
    entries: tuple   # (character, move label, value, row)


def rank(game, available, *, character=None, metric, sort, kind=None, trait=None, count=DEFAULT_COUNT, label=None):
    keys = [available.characters[character]] if character else list(game.frame_data)
    names = {key: display for display, key in available.characters.items()}
    entries, seen = [], set()
    for key in keys:
        for row in game.frame_data.get(key, []) or []:
            if kind and str(row.get("moveType") or "").strip().lower() != kind:
                continue
            if trait and not truthy(row.get(trait)):
                continue
            value = number(row.get(metric))
            if value is None:
                continue
            move = label(row) if label else str(row.get("moveName") or row.get("numCmd") or "").strip()
            identity = (key, move)
            if not move or identity in seen:
                continue
            seen.add(identity)
            entries.append((names.get(key, str(key).title()), move, value, row))
    entries.sort(key=lambda entry: entry[2], reverse=sort == "desc")
    return Ranking(game, character, metric, sort, kind, trait, count, tuple(entries[:count]))


def _format(metric, value):
    text = f"{value:.2f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))
    return f"+{text}" if metric in SIGNED and value > 0 else text


def embed(ranking):
    name = METRICS[ranking.metric][0]
    direction = "Highest" if ranking.sort == "desc" else "Lowest"
    if ranking.metric == "startup" and ranking.sort == "asc":
        direction, name = "Fastest", "startup"
    scope = ranking.character or "all characters"
    filters = " · ".join(part for part in (ranking.kind and f"{ranking.kind} moves", ranking.trait and TRAITS[ranking.trait]) if part)
    lines = []
    for index, (character, move, value, row) in enumerate(ranking.entries, start=1):
        extra = " · ".join(f"{METRICS[m][0].lower()} {_format(m, number(row.get(m)))}"
                           for m in ("startup", "onBlock") if m != ranking.metric and number(row.get(m)) is not None)
        who = "" if ranking.character else f"{character} · "
        lines.append(f"**{index}.** {who}{move} — **{_format(ranking.metric, value)}**" + (f" · {extra}" if extra else ""))
    result = discord.Embed(
        title=f"{direction} {name} · {scope} ({ranking.game.label})"[:256],
        description="\n".join(lines)[:4000] or "No moves have that number.",
        color=BRAND,
    )
    footer = f"{filters} · " if filters else ""
    result.set_footer(text=f"{footer}From SF Stats frame data · ask for any move to see its full card")
    return strip_embed_emojis(result)


async def answer(text, *, games, session, tagged=(), config=None, label=None):
    """A Ranking, or None when any question is unsure, off or failing."""
    if not decisions.enabled(config) or not games:
        return None
    game = await decisions.pick_game(text, games, tagged, session=session, config=config, label="rank-game")
    if game is None:
        return None
    available = offer(game.frame_data)
    if not available.metrics:
        return None
    answers = await decisions.decide(text, questions(game, available), session=session, config=config, label="rank",
                                     timeout_seconds=5.0)
    metric, sort = decisions.sure(answers, "metric"), decisions.sure(answers, "sort")
    if metric not in available.metrics or sort not in SORTS:
        return None
    character = decisions.sure(answers, "character")
    # A character the message names outright counts even when the model is unsure or answers "none".
    if character not in available.characters:
        character = named_character(text, game, available)
    kind, trait = decisions.sure(answers, "kind"), decisions.sure(answers, "trait")
    count_text = decisions.sure(answers, "count")
    count = int(count_text.split()[0]) if count_text else DEFAULT_COUNT
    return rank(game, available, character=character if character in available.characters else None, metric=metric, sort=sort,
                kind=kind if kind in available.kinds else None, trait=trait if trait in available.traits else None,
                count=max(1, min(MAX_COUNT, count)), label=label)
