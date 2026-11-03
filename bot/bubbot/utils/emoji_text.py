"""Keep emoji out of embed text.

Bub's result cards are plain frame-data tables. Emoji that arrive from data
sources, or from an old message being rebuilt, are removed here at display
time so the stored workbooks and notes stay untouched.
"""

import re

# Ranges that Unicode gives emoji presentation by default. Text symbols that
# carry meaning in frame data and glossary entries (★, ✓) are deliberately not
# included; arrows such as → are typography, not emoji.
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),  # pictographs, emoticons, flags, transport
    (0x2600, 0x26FF),    # miscellaneous symbols (⚔, ⚠, ⛔)
    (0x2700, 0x27BF),    # dingbats (✅, ❌, ➡)
    (0x2B00, 0x2BFF),    # stars and squares (⭐, ⬛)
    (0x203C, 0x203C),    # ‼
    (0x2049, 0x2049),    # ⁉
    (0x3030, 0x3030),    # 〰
    (0x303D, 0x303D),    # 〽
    (0x3297, 0x3297),    # ㊗
    (0x3299, 0x3299),    # ㊙
)
# Formatting characters that only exist to assemble an emoji sequence.
_EMOJI_FORMATTING = frozenset({0x200D, 0x20E3, 0xFE0E, 0xFE0F})
# Symbols that look emoji-like but are plain text in Bub's data. The sharp sign
# is part of Guilty Gear's "Asuka R♯"; the others appear in glossary and notes.
_TEXT_SYMBOLS = frozenset({0x2605, 0x266A, 0x266B, 0x266F, 0x2713})  # ★, ♪, ♫, ♯, ✓


def _is_emoji(character):
    codepoint = ord(character)
    if codepoint in _TEXT_SYMBOLS:
        return False
    return any(start <= codepoint <= end for start, end in _EMOJI_RANGES)


def strip_emojis(text):
    """Return text with emoji and emoji joiners removed, keeping normal punctuation.

    Text with no emoji is returned unchanged, so plain frame data and notes keep
    their exact spacing.
    """
    if not text:
        return text
    original = str(text)
    kept = []
    removed = False
    for character in original:
        if ord(character) in _EMOJI_FORMATTING or _is_emoji(character):
            removed = True
            continue
        kept.append(character)
    if not removed:
        return original
    cleaned = "".join(kept)
    # Removing an inline emoji can leave doubled spaces or a space before a newline.
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def strip_embed_emojis(embed):
    """Remove emoji from an embed's title, description, fields, footer and author, in place."""
    if embed is None:
        return None
    if embed.title:
        embed.title = strip_emojis(embed.title)
    if embed.description:
        embed.description = strip_emojis(embed.description)
    author = getattr(embed, "author", None)
    if author is not None and author.name:
        cleaned_author = strip_emojis(author.name)
        if cleaned_author != author.name:
            embed.set_author(name=cleaned_author, url=author.url, icon_url=author.icon_url)
    footer = getattr(embed, "footer", None)
    if footer is not None and footer.text:
        cleaned_footer = strip_emojis(footer.text)
        if cleaned_footer != footer.text:
            embed.set_footer(text=cleaned_footer, icon_url=footer.icon_url)
    for index, field in enumerate(list(embed.fields)):
        cleaned_name = strip_emojis(field.name)
        cleaned_value = strip_emojis(field.value)
        if cleaned_name == field.name and cleaned_value == field.value:
            continue
        # Discord rejects an empty field name or value; a zero-width space keeps the row.
        embed.set_field_at(
            index,
            name=cleaned_name or "\u200b",
            value=cleaned_value or "\u200b",
            inline=field.inline,
        )
    return embed


__all__ = ["strip_embed_emojis", "strip_emojis"]
