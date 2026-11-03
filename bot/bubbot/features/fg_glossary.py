"""Fighting Game Glossary helpers for NL, slash, and menu flows."""

import html
import json
import re
from pathlib import Path
from urllib.parse import quote

import discord
from bubbot.utils.sfstats_style import BRAND
from bubbot.utils.emoji_text import strip_embed_emojis


GLOSSARY_HOME_URL = "https://glossary.infil.net/"
GLOSSARY_LOOKUP_URL = "https://glossary.infil.net/?t="
GLOSSARY_JSON_PATH = Path(__file__).resolve().parents[1] / "data" / "fg_glossary.json"
_GLOSSARY_TERMS = json.loads(GLOSSARY_JSON_PATH.read_text(encoding="utf-8"))


def normalize_glossary_term(term):
    return " ".join(str(term or "").strip().split())


def glossary_url(term=None):
    normalized = normalize_glossary_term(term)
    if not normalized:
        return GLOSSARY_HOME_URL
    return f"{GLOSSARY_LOOKUP_URL}{quote(normalized, safe='')}"


def _strip_for_search(value):
    return re.sub(r"[.,/#!$%\^&\*;:{}=\-_`~() '\"]", "", str(value or "").lower())


def _clean_definition_text(value):
    text = str(value or "")
    text = re.sub(r"!<'([^']+)'(?:,'([^']+)')?>", lambda m: m.group(2) or m.group(1), text)
    text = re.sub(r"\?<'([^']+)','([^']+)'>", lambda m: f"{m.group(2)} ({m.group(1)})", text)
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _truncate(value, limit):
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _find_glossary_term(terms, query):
    normalized = normalize_glossary_term(query)
    if not normalized:
        return None
    query_lower = normalized.lower()
    query_stripped = _strip_for_search(normalized)
    best = None
    for item in terms or []:
        names = [str(item.get("term", ""))]
        names.extend(str(value) for value in item.get("altterm", []) or [])
        names_lower = [name.lower() for name in names if name]
        if query_lower in names_lower:
            return item
        stripped_names = [_strip_for_search(name) for name in names if name]
        if query_stripped in stripped_names:
            return item
        for index, stripped in enumerate(stripped_names):
            if query_stripped and query_stripped in stripped:
                score = (len(stripped) - len(query_stripped), index, str(item.get("term", "")))
                if best is None or score < best[0]:
                    best = (score, item)
    return best[1] if best else None


def find_glossary_term(term):
    return _find_glossary_term(_GLOSSARY_TERMS, term)


# Plain questions about a term. Only answered from the glossary when it has the term, so "what is ken 5mp"
# still reaches frame data.
_QUESTION_PATTERNS = (
    r"^(?:what\s+does|what\s+do|wtf\s+does|wtf\s+is)\s+(?:a\s+|an\s+|the\s+)?(.+?)\s+mean\s*\??$",
    r"^(?:what\s+is|what's|whats|what\s+are|wtf\s+is)\s+(?:a\s+|an\s+|the\s+)?(.+?)\s*\??$",
    r"^(?:meaning\s+of|explain|explain\s+what)\s+(?:a\s+|an\s+|the\s+)?(.+?)\s*(?:is|means)?\s*\??$",
)


def glossary_question_term(text):
    """The glossary term a plain question asks about ("what is a safe jump", "what does oki mean"), or None."""
    cleaned = normalize_glossary_term(text)
    for pattern in _QUESTION_PATTERNS:
        match = re.match(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            term = normalize_glossary_term(match.group(1))
            if term and _exact_glossary_term(term):
                return term
    return None


def _exact_glossary_term(query):
    """A term or alternate name equal to the query (ignoring case, spaces and punctuation); never a partial
    match, so "what's up" is not Getup and "what is ryu" is not Shoryuken."""
    stripped = _strip_for_search(query)
    for item in _GLOSSARY_TERMS or []:
        names = [str(item.get("term", ""))] + [str(value) for value in item.get("altterm", []) or []]
        if stripped and any(_strip_for_search(name) == stripped for name in names if name):
            return item
    return None


def build_glossary_embed(term=None):
    normalized = normalize_glossary_term(term)
    url = glossary_url(normalized)
    title = "Fighting Game Glossary"
    description = "Open Infil's Fighting Game Glossary."
    if normalized:
        title = f"FG Glossary: {normalized}"
        description = f"Search Infil's glossary for **{discord.utils.escape_markdown(normalized)}**."
    embed = discord.Embed(title=title, description=description, url=url, colour=BRAND)
    embed.add_field(name="Link", value=url, inline=False)
    embed.set_footer(text="Source: glossary.infil.net")
    return strip_embed_emojis(embed)


def build_glossary_definition_embed(term=None):
    normalized = normalize_glossary_term(term)
    if not normalized:
        return build_glossary_embed()
    item = find_glossary_term(normalized)
    if not item:
        embed = build_glossary_embed(normalized)
        embed.add_field(name="Definition", value="No exact glossary entry was found. Use the link to search the glossary site.", inline=False)
        return strip_embed_emojis(embed)

    title = str(item.get("term", "") or normalized).strip()
    definition = _truncate(_clean_definition_text(item.get("def", "")), 3500)
    url = glossary_url(title)
    embed = discord.Embed(title=f"FG Glossary: {title}", description=definition or "No definition text found.", url=url, colour=BRAND)
    alt_terms = [str(value).strip() for value in item.get("altterm", []) or [] if str(value).strip()]
    if alt_terms:
        embed.add_field(name="Also Known As", value=_truncate(", ".join(alt_terms), 900), inline=False)
    jp = _clean_definition_text(item.get("jp", ""))
    if jp:
        embed.add_field(name="Japanese", value=_truncate(jp, 900), inline=False)
    embed.add_field(name="Link", value=url, inline=False)
    embed.set_footer(text="Source: glossary.infil.net")
    return strip_embed_emojis(embed)


def build_glossary_link_view(term=None):
    view = discord.ui.View(timeout=None)
    view.add_item(build_glossary_link_button(term))
    return view


def build_glossary_link_button(term=None):
    label = "Open Term" if normalize_glossary_term(term) else "Open Glossary"
    return discord.ui.Button(label=label, style=discord.ButtonStyle.link, url=glossary_url(term))
