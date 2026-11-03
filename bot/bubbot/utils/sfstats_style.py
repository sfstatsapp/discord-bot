"""SF Stats' look for Bub's messages (src/lib/discord/ui.ts and emoji.ts in sfstatsapp/sf-stats).

Menus are Components V2 cards (``CardView``): one container with SF Stats' near-black accent, a
``##`` heading, short lines and a small ``-#`` footer, with the controls below it. Results (frame
data, stats, combos, glossary answers) stay embeds so SF Stats' frame-data helper can still post
them; they take the same accent. Buttons are grey, the active tab or toggle is blue, and labels are
plain text: Bub's cards carry no emoji.

A Components V2 message can never become an embed again, so a menu and a result never edit into
each other: picking from a menu posts the result as a new message, and a result's buttons open
menus as new private messages.
"""

import discord

BRAND = discord.Colour(0x0B0B0B)
ACTION = discord.ButtonStyle.secondary
ACTIVE = discord.ButtonStyle.primary
# Splits a card into blocks with a thin line between them, like ui.ts divider().
DIVIDER = object()


def toggle_style(active):
    """Show/hide toggles read like SF Stats tabs: blue while showing, grey otherwise."""
    return ACTIVE if active else ACTION


class AllHitboxImagesButton(discord.ui.Button):
    """Show All Images on a frame card: posts every image link when the move has more than one."""

    def __init__(self, hitbox_links):
        self.hitbox_links = list(hitbox_links or [])
        super().__init__(label="Show All Images", style=ACTION, disabled=len(self.hitbox_links) <= 1)

    async def callback(self, interaction: discord.Interaction):
        if len(self.hitbox_links) <= 1:
            await interaction.response.defer()
            return
        await interaction.response.send_message("\n".join(self.hitbox_links))


def card_text(title=None, *lines, footer=None):
    """"## Title", the lines that exist, then "-# a · b" in small print."""
    parts = [f"## {title}"] if title else []
    parts.extend(str(line) for line in lines if line)
    footer_parts = [str(part) for part in (footer if isinstance(footer, (list, tuple)) else [footer]) if part]
    if footer_parts:
        parts.append("-# " + " · ".join(footer_parts))
    return "\n".join(parts)


def embed_card_blocks(embed):
    """An existing embed (glossary entries) as card blocks: heading and text, a line, then its fields."""
    head = card_text(embed.title, embed.description)
    fields = "\n\n".join(f"**{field.name}**\n{field.value}" for field in embed.fields if field.name or field.value)
    footer = f"-# {embed.footer.text}" if embed.footer and embed.footer.text else ""
    return [block for block in (head, DIVIDER if fields else None, fields, footer) if block]


def _control_rows(controls):
    """Controls grouped into action rows like discord.ui.View: explicit rows first, then the rest fill in."""
    rows = {}

    def fits(row, item):
        if isinstance(item, discord.ui.Select):
            return not row
        return len(row) < 5 and not any(isinstance(other, discord.ui.Select) for other in row)

    for item in controls:
        if item.row is not None:
            rows.setdefault(item.row, []).append(item)
    for item in controls:
        if item.row is None:
            index = 0
            while not fits(rows.get(index, []), item):
                index += 1
            rows.setdefault(index, []).append(item)
    return [rows[index] for index in sorted(rows)]


class CardView(discord.ui.LayoutView):
    """A Components V2 card: the text in one container with SF Stats' accent, the controls below it.

    Controls are added as on a discord.ui.View (``add_item`` with ``row=``, or ``@discord.ui.button``,
    which LayoutView alone ignores), so menus keep their structure. ``set_card`` sets the text.
    """

    def __init__(self, *, timeout=None):
        super().__init__(timeout=timeout)
        self._blocks = []
        self._controls = []
        for name, func in self._decorated_controls():
            item = func.__discord_ui_model_type__(**func.__discord_ui_model_kwargs__)
            item.callback = self._bind(func, item)
            setattr(self, name, item)
            self._controls.append(item)
        self._layout()

    @classmethod
    def _decorated_controls(cls):
        found = {}
        for base in reversed(cls.__mro__):
            for name, member in base.__dict__.items():
                if hasattr(member, "__discord_ui_model_type__"):
                    found[name] = member
        return found.items()

    def _bind(self, func, item):
        async def callback(interaction):
            return await func(self, interaction, item)

        return callback

    def set_card(self, *blocks):
        """The card's text: markdown strings, with DIVIDER for a thin line between them."""
        self._blocks = [block for block in blocks if block]
        self._layout()
        return self

    def add_item(self, item):
        if isinstance(item, (discord.ui.Button, discord.ui.Select)):
            self._controls.append(item)
            self._layout()
            return self
        return super().add_item(item)

    def remove_item(self, item):
        if item in self._controls:
            self._controls.remove(item)
            self._layout()
            return self
        return super().remove_item(item)

    def _layout(self):
        super().clear_items()
        # Discord allows 4,000 characters of text across the whole card.
        parts, budget = [], 4000
        for block in self._blocks:
            if block is DIVIDER:
                parts.append(discord.ui.Separator())
            elif budget > 0:
                text = str(block)[:budget]
                budget -= len(text)
                parts.append(discord.ui.TextDisplay(text))
        super().add_item(discord.ui.Container(*(parts or [discord.ui.TextDisplay("​")]), accent_colour=BRAND))
        for row in _control_rows(self._controls):
            super().add_item(discord.ui.ActionRow(*row))
