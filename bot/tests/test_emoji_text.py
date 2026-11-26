import unittest

import discord

from bubbot.utils.emoji_text import strip_embed_emojis, strip_emojis


class StripEmojisTests(unittest.TestCase):
    def test_removes_pictographs_and_variation_selectors(self):
        self.assertEqual("THE GOAT\nNext line", strip_emojis("THE GOAT ‼️\nNext line"))
        self.assertEqual("Compare", strip_emojis("Compare ⚔️"))
        self.assertEqual("Win rate", strip_emojis("🟩 Win rate 📈"))

    def test_keeps_text_symbols_and_arrows(self):
        self.assertEqual("Tekken uses a star (★)", strip_emojis("Tekken uses a star (★)"))
        self.assertEqual("Done ✓", strip_emojis("Done ✓"))
        self.assertEqual("SF6 → Ranked", strip_emojis("SF6 → Ranked"))
        self.assertEqual("Asuka R♯", strip_emojis("Asuka R♯"))

    def test_plain_text_is_returned_unechanged(self):
        plain = "Startup  7\nActive   3(2)4"
        self.assertIs(strip_emojis(plain), plain)


class StripEmbedEmojisTests(unittest.TestCase):
    def test_cleans_every_visible_embed_part(self):
        embed = discord.Embed(title="🏆 Ranked", description="Best moves ⚔️")
        embed.add_field(name="📈 Startup", value="Fast ✅", inline=True)
        embed.set_footer(text="From SF Stats 🎬")

        result = strip_embed_emojis(embed)

        self.assertIs(result, embed)
        self.assertEqual("Ranked", embed.title)
        self.assertEqual("Best moves", embed.description)
        self.assertEqual("Startup", embed.fields[0].name)
        self.assertEqual("Fast", embed.fields[0].value)
        self.assertEqual("From SF Stats", embed.footer.text)

    def test_leaves_plain_embeds_unchanged(self):
        embed = discord.Embed(title="Cammy", description="Killer Bee Spin (214214P)")
        embed.add_field(name="Startup", value="13", inline=True)

        strip_embed_emojis(embed)

        self.assertEqual("Cammy", embed.title)
        self.assertEqual("Killer Bee Spin (214214P)", embed.description)
        self.assertEqual("Startup", embed.fields[0].name)
        self.assertEqual("13", embed.fields[0].value)


if __name__ == "__main__":
    unittest.main()
