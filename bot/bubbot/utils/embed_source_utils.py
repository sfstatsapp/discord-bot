"""Per-game data source footers and bundled icons on frame/combo embeds."""

from __future__ import annotations

from urllib.parse import urlparse

import discord

from bubbot.data.game_source_info import (
    GameSourceInfo,
    bundled_icon_path,
    get_additional_frame_source_info,
    get_combo_source_info,
    get_frame_source_info,
    resolve_attachment_icon_url,
    source_icon_attachment_name,
    source_key_for_game,
)
from bubbot.utils.discord_formatting import truncate_value
from bubbot.utils.emoji_text import strip_embed_emojis


def _resolve_source(game: str, *, kind: str) -> GameSourceInfo:
    if kind == "combo":
        return get_combo_source_info(game)
    return get_frame_source_info(game)


def format_footer_display_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    normalized = raw if "://" in raw else f"https://{raw.lstrip('/')}"
    parsed = urlparse(normalized)
    host = (parsed.netloc or "").strip()
    if not host and parsed.path:
        host = parsed.path.split("/")[0]
    if host.lower().startswith("www."):
        host = host[4:]
    return host


def _format_footer_source(source: GameSourceInfo) -> str:
    url = format_footer_display_url(source.url)
    if url:
        return f"{source.name} · {url}"
    return source.name


def source_icon_files(game: str, *, kind: str = "frame") -> list[discord.File]:
    source_key = source_key_for_game(game, kind=kind)
    path = bundled_icon_path(source_key)
    if not path:
        return []
    return [discord.File(path, filename=source_icon_attachment_name(source_key))]


def apply_game_source_footer(
    embed: discord.Embed,
    game: str,
    *,
    kind: str = "frame",
    prefer_attachment_icon: bool = True,
) -> discord.Embed:
    strip_embed_emojis(embed)
    source = _resolve_source(game, kind=kind)
    existing = ""
    if embed.footer and embed.footer.text:
        existing = str(embed.footer.text).strip()
    sources = (source,)
    if kind == "frame":
        sources += get_additional_frame_source_info(game)
    source_credits = tuple(_format_footer_source(item) for item in sources)
    footer_text = " · ".join(source_credits)
    if existing:
        missing_credits = tuple(
            credit for item, credit in zip(sources, source_credits)
            if item.name.lower() not in existing.lower()
        )
        footer_text = " · ".join((existing, *missing_credits))
    kwargs = {"text": truncate_value(footer_text, 2048)}
    if prefer_attachment_icon and bundled_icon_path(source_key_for_game(game, kind=kind)):
        icon_url = resolve_attachment_icon_url(game, kind=kind)
    else:
        from bubbot.data.game_source_info import resolve_icon_for_game

        icon_url = resolve_icon_for_game(game, kind=kind)
    if icon_url:
        kwargs["icon_url"] = icon_url
    embed.set_footer(**kwargs)
    return embed


def apply_source_footer_to_pages(pages, game: str, *, kind: str = "frame"):
    for embed in pages or []:
        apply_game_source_footer(embed, game, kind=kind)
    return pages
