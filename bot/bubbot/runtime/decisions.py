"""OpenRouter's Decisions endpoint, asked in layers.

The model only picks among options listed here, so it never writes frame data or names a move
Bub doesn't have. Layer 1 (``route``) decides whether an "@SF Stats" message wants frame data
(Bub, answered here) or SF Stats data (forwarded to the web app, which asks its own questions).
Layer 2 for frame data (``bubbot.runtime.frame_resolver``) picks the game, character and move
from Bub's own tables when Bub's parser can't read a request.

Off unless DECISIONS_API_KEY is set. Every failure, timeout or unsure answer returns None and the
caller falls back to what it did before. Only the message text is sent, mentions removed; logs
carry question names and timings, never content.
"""

import os
import re
import time
from dataclasses import dataclass

import aiohttp


DEFAULT_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "openai/gpt-6-luna-decisions"
# The endpoint rejects a choice question with more options than this.
MAX_CHOICES = 255
MAX_TEXT = 500
NONE = "none"

ROUTES = {
    "framedata": "Frame data for a fighting game (Street Fighter 6, SFV, USF4, Third Strike, Guilty Gear Strive and +R, 2XKO, "
                 "BlazBlue, City of the Wolves, Mortal Kombat 1, Avatar Legends): a move's startup, on block, on hit, hitboxes, "
                 "gifs, combos, punishes, move inputs like 5HP, 2MK or 236LP, a character's own stats like dash or jump speed (never a player's), "
                 "fighting game terms, quizzes, the frame data menu.",
    "sfstats": "SF Stats, the Street Fighter 6 ranked stats bot: a player's CFN profile, rank, LP or MR, main and matchups; "
               "one player's stats as one character (\"my viper\", \"my ryu stats\", \"@someone's juri\"); "
               "leaderboards and best players by character or country; this server's leaderboard; head-to-head records between "
               "players; a character's popularity, win rate and best or worst matchups; SF6 replays and the replay capture queue; "
               "linking a Discord account to a CFN profile; what SF Stats can do.",
}
ROUTE_INSTRUCTIONS = ("A Discord message is addressed to a bot that answers both fighting game frame data and Street Fighter 6 "
                      "ranked stats. Which kind of answer does the person want?")


@dataclass(frozen=True)
class Config:
    key: str = ""
    url: str = DEFAULT_URL
    model: str = DEFAULT_MODEL
    timeout_seconds: float = 1.8

    @property
    def enabled(self):
        return bool(self.key)


def _float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# How sure an answer must be to act on it (it must also be the model's top choice, and never "none"). Lower than
# the web app's 0.6: the options here are Bub's own lists, so a wrong pick is still real data, and its card names the move.
CONFIDENCE = max(0.1, min(0.95, _float("DECISIONS_CONFIDENCE", 0.4)))


def load_config():
    return Config(
        key=os.getenv("DECISIONS_API_KEY", "").strip(),
        url=os.getenv("DECISIONS_URL", "").strip() or DEFAULT_URL,
        model=os.getenv("DECISIONS_MODEL", "").strip() or DEFAULT_MODEL,
        timeout_seconds=max(0.3, min(10.0, _float("DECISIONS_TIMEOUT_SECONDS", 1.8))),
    )


CONFIG = load_config()


def enabled(config=None):
    return (config or CONFIG).enabled


def options(values, none_text, describe=None):
    """A choice question's criteria: "none" first (every question needs one, or the model refuses it and the whole
    request fails), then up to 254 distinct values."""
    criteria = {NONE: none_text}
    for value in values:
        value = str(value or "").strip()
        if value and value != NONE and value not in criteria:
            criteria[value] = describe(value) if describe else None
        if len(criteria) >= MAX_CHOICES:
            break
    return criteria


def choice(criteria, instructions):
    return {"type": "choice", "criteria": criteria, "instructions": instructions}


def request_text(content):
    """The text the model sees: no Discord markup, no "sfstats" prefix."""
    text = re.sub(r"<[@#][!&]?\d{17,20}>", " ", str(content or ""))
    text = re.sub(r"^\s*[!@.]?\s*sf\s*-?\s*stats\b[\s,:;.!?-]*", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()[:MAX_TEXT]


def read_answers(body, questions):
    """{name: (choice, confidence)} for answers we asked for, with values we listed; anything else is dropped."""
    answers = body.get("answers") if isinstance(body, dict) else None
    if not isinstance(answers, dict):
        return None
    result = {}
    for name, question in questions.items():
        answer = answers.get(name)
        if not isinstance(answer, dict) or answer.get("type") != "choice":
            continue
        value = answer.get("choice")
        if not isinstance(value, str) or value not in question["criteria"]:
            continue
        confidence = answer.get("confidence")
        if not isinstance(confidence, (int, float)):
            confidence = (answer.get("probabilities") or {}).get(value)
        confidence = max(0.0, min(1.0, float(confidence))) if isinstance(confidence, (int, float)) else 0.0
        result[name] = (value, confidence)
    return result


DEFAULT_GAME_KEY = "sf6"


async def pick_game(text, games, tagged, *, session, config=None, label="game"):
    """Street Fighter 6 unless the message names a game title (Bub's own tag detection). Only when it names more
    than one ("guilty gear": Strive or +R) is the model asked, and only among those."""
    by_key = {game.key: game for game in games}
    named = [by_key[key] for key in tagged if key in by_key]
    if len(named) == 1:
        return named[0]
    if not named:
        return by_key.get(DEFAULT_GAME_KEY)
    question = {"game": choice(options([game.label for game in named], "None of these games."),
                               "Which of these fighting games does the request mean?")}
    picked = sure(await decide(text, question, session=session, config=config, label=label), "game")
    return next((game for game in named if game.label == picked), None)


def sure(answers, name, minimum=CONFIDENCE):
    """The chosen value when it is confident and not "none", else None."""
    value, confidence = (answers or {}).get(name, (None, 0.0))
    return value if value not in (None, NONE) and confidence >= minimum else None


async def decide(text, questions, *, session, config=None, label="decide", timeout_seconds=None):
    """One request, or None when off, too slow or failing. Bigger questions pass a longer timeout: message replies
    have no 3-second deadline, only the route question sits in front of every message."""
    config = config or CONFIG
    text = request_text(text)
    if not config.enabled or not text or not questions:
        return None
    started = time.monotonic()
    outcome = "ok"
    try:
        async with session.post(
            config.url,
            json={"model": config.model, "state": text, "questions": questions},
            headers={"Authorization": f"Bearer {config.key}", "Content-Type": "application/json", "X-Title": "SF Stats"},
            timeout=aiohttp.ClientTimeout(total=max(config.timeout_seconds, timeout_seconds or 0)),
        ) as response:
            if response.status != 200:
                outcome = f"HTTP {response.status}"
                return None
            answers = read_answers(await response.json(content_type=None), questions)
            if answers is None:
                outcome = "no answers"
            return answers
    except Exception as error:
        outcome = type(error).__name__
        return None
    finally:
        ms = round((time.monotonic() - started) * 1000)
        print(f"[decisions] {label} questions={','.join(questions)} {outcome} {ms}ms", flush=True)


# Layer 0: wording that settles the route without a request.
_FRAME_HINT = re.compile(
    r"\b(?:frame\s*data|frames?|startup|on\s*block|on\s*hit|recovery|hitbox(?:es)?|hurtbox(?:es)?|gifs?|combos?|"
    r"punish(?:es|able)?|cancell?able|define|definition|glossary|fgg|means?|meaning|terminology|"
    r"(?:standing|crouching|crouch|jumping|jump|close|far|light|medium|heavy|st|cr)\s+(?:punch|kick|slash)(?:es)?|"
    r"sweep|fireballs?|hadou?kens?|shoryukens?|tatsus?|overheads?|anti[\s-]?airs?|reversals?|frame\s*traps?|"
    r"moves?|buttons?|pokes?|normals?|specials?|grabs?|throws?|command\s+grabs?|air\s+(?:grabs?|throws?)|"
    r"(?:jumping|crouching|standing)\s+\w+|(?:forward|back|neutral|down|close|far)\s+(?:[lmh][pk]|(?:light|medium|heavy)\s+(?:punch|kick))|"
    r"mp|hp|lk|mk|hk|"
    r"range|reach|damage|hitstun|blockstun|chip|drive\s+damage|super\s+gain|frame\s+advantage|[1-9]{1,4}(?:lp|mp|hp|lk|mk|hk|pp|kk|p|k|s|h|d|l|m)|(?:cr|st|cl|far|j)\.?\s?[lmh][pk])\b",
    re.IGNORECASE,
)
_SFSTATS_HINT = re.compile(
    r"\b(?:rank(?:ed|s)?|lp|mr|leaderboards?|profile|cfn|replays?|queue|win\s*rates?|matchups?|top\s*\d+|best|"
    r"link(?:ed)?|elo|my\s+stats|vs\.?\s+@|nemesis|streaks?|peak|grind(?:s|ing)?|ladder|server|hours?|days|"
    r"how\s+often|who\s+(?:is|are)\s+the\s+best)\b",
    re.IGNORECASE,
)


# A player as the subject: the speaker's own ("my viper", "my ryu stats") rather than a character's frame data.
_PLAYER_HINT = re.compile(r"\b(?:my|mine)\b", re.IGNORECASE)


def local_route(content, *, players=0):
    """"framedata" or "sfstats" when the wording alone decides, else None (ask, or let the web app decide).
    ``players``: other people mentioned in the message ("@sfstats @friend this guys viper"), never the bots."""
    text = request_text(content)
    frame, stats = bool(_FRAME_HINT.search(text)), bool(_SFSTATS_HINT.search(text))
    if frame != stats:
        return "framedata" if frame else "sfstats"
    # About a player and nothing about moves: "my viper", "@friend's ryu" are SF Stats cards, not Bub's character stats.
    if not frame and (players or _PLAYER_HINT.search(text)):
        return "sfstats"
    return None


async def route(content, *, session, config=None, players=0):
    """Layer 0 then layer 1: "framedata", "sfstats" or None (unsure, off or failing)."""
    local = local_route(content, players=players)
    if local or not enabled(config):
        return local
    questions = {"route": choice(options(ROUTES, "Neither: small talk or nothing a bot looks up.", describe=ROUTES.get),
                                 ROUTE_INSTRUCTIONS)}
    answers = await decide(content, questions, session=session, config=config, label="route")
    return sure(answers, "route")
