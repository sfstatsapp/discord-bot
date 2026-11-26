"""Report Issue button and modal for failed Bub replies.

Builds report context from frame/combo/menu responses and logs user feedback
via response_log for maintainer review.
"""

from __future__ import annotations

from typing import Optional

import discord

from bubbot.utils.response_log import (
    INTERACTION_REPORT,
    build_log_record,
    log_and_notify_owner,
)
from bubbot.utils.sfstats_style import ACTION


# Discord modal and Report Issue button
class FailedPromptReportModal(discord.ui.Modal, title="Report an issue"):
    report_details = discord.ui.TextInput(
        label="What went wrong?",
        style=discord.TextStyle.paragraph,
        placeholder="Example: I asked for mai ex fan and SF Stats couldn't find the move.",
        required=True,
        max_length=1500,
    )

    def __init__(self, report_context: dict):
        super().__init__()
        self.report_context = dict(report_context or {})

    async def on_submit(self, interaction: discord.Interaction):
        report_text = str(self.report_details.value or "").strip()
        if not report_text:
            await interaction.response.send_message("Please describe the issue before submitting.", ephemeral=True)
            return
        ctx = self.report_context
        record = build_log_record(
            interaction_type=INTERACTION_REPORT,
            reason="user_report",
            prompt=str(ctx.get("prompt", "") or ""),
            response_text=str(ctx.get("bub_response_text", "") or ""),
            response_kind="text",
            user_report=report_text,
            failure_reason=str(ctx.get("failure_reason", "") or ""),
            extra={
                "guild_id": ctx.get("guild_id"),
                "guild_name": ctx.get("guild_name", ""),
                "channel_id": ctx.get("channel_id"),
                "channel_name": ctx.get("channel_name", ""),
                "user_id": getattr(interaction.user, "id", None),
                "user_name": str(
                    getattr(interaction.user, "display_name", "")
                    or getattr(interaction.user, "name", "")
                    or ""
                ),
                "game": ctx.get("game", ""),
                "char_key": ctx.get("char_key", ""),
                "move_name": ctx.get("move_name", ""),
                "num_cmd": ctx.get("num_cmd", ""),
                "combo_section": ctx.get("combo_section", ""),
                "combo_subsection": ctx.get("combo_subsection", ""),
                "source_message_id": ctx.get("source_message_id"),
                "reply_message_id": ctx.get("reply_message_id"),
                "source_jump_url": ctx.get("source_jump_url", ""),
                "reply_jump_url": str(getattr(interaction.message, "jump_url", "") or ""),
            },
        )
        client = ctx.get("client")
        if client is None:
            from bubbot.runtime import message_router

            client = message_router.client
        await log_and_notify_owner(client, record)
        await interaction.response.send_message(
            "Thanks — your report was logged and sent to the maintainer.",
            ephemeral=True,
        )


class FailedPromptReportButton(discord.ui.Button):
    def __init__(self, report_context: dict):
        super().__init__(
            label="Report Issue",
            style=ACTION,
            custom_id="bub_failed_prompt_report",
        )
        self.report_context = dict(report_context or {})

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(FailedPromptReportModal(self.report_context))


# Report context builders (generic, frame, combo)
def build_report_context_from_message(source_message, *, bub_response_text: str, failure_reason: str, prompt: str = "") -> dict:
    author = getattr(source_message, "author", None) if source_message is not None else None
    guild = getattr(source_message, "guild", None) if source_message is not None else None
    channel = getattr(source_message, "channel", None) if source_message is not None else None
    resolved_prompt = prompt or (str(getattr(source_message, "content", "") or "") if source_message is not None else "")
    return {
        "prompt": resolved_prompt,
        "bub_response_text": bub_response_text,
        "failure_reason": failure_reason,
        "guild_id": getattr(guild, "id", None),
        "guild_name": str(getattr(guild, "name", "") or ""),
        "channel_id": getattr(channel, "id", None),
        "channel_name": str(getattr(channel, "name", "") or ""),
        "source_message_id": getattr(source_message, "id", None) if source_message is not None else None,
        "source_jump_url": str(getattr(source_message, "jump_url", "") or "") if source_message is not None else "",
        "source_user_id": getattr(author, "id", None),
        "source_user_name": str(getattr(author, "display_name", "") or getattr(author, "name", "") or ""),
    }


def build_frame_report_context(
    *,
    game: str,
    row=None,
    source_message=None,
    prompt: str = "",
    failure_reason: str = "frame_data_response",
    bub_response_text: str = "",
) -> dict:
    row = row or {}
    char_name = str(row.get("char_name") or row.get("char_key") or "").strip()
    move_name = str(row.get("moveName") or row.get("numCmd") or "").strip()
    if not bub_response_text:
        bub_response_text = " — ".join(part for part in (char_name, move_name) if part) or str(game or "frame data")
    context = build_report_context_from_message(
        source_message,
        bub_response_text=bub_response_text,
        failure_reason=failure_reason,
        prompt=prompt,
    )
    context.update(
        {
            "game": str(game or ""),
            "char_key": str(row.get("char_key") or row.get("char_name") or ""),
            "move_name": move_name,
            "num_cmd": str(row.get("numCmd") or ""),
        }
    )
    return context


def build_combo_report_context(
    *,
    game: str,
    char_key: str,
    source_message=None,
    prompt: str = "",
    section=None,
    subsection=None,
    failure_reason: str = "combo_response",
) -> dict:
    label_parts = [str(game or "").upper(), str(char_key or "")]
    if section:
        label_parts.append(str(section))
    if subsection:
        label_parts.append(str(subsection))
    bub_response_text = " — ".join(part for part in label_parts if part)
    context = build_report_context_from_message(
        source_message,
        bub_response_text=bub_response_text,
        failure_reason=failure_reason,
        prompt=prompt,
    )
    context.update(
        {
            "game": str(game or ""),
            "char_key": str(char_key or ""),
            "combo_section": str(section or ""),
            "combo_subsection": str(subsection or ""),
        }
    )
    return context


# Attach report button to frame/combo/menu views
def _iter_view_items(view):
    walk = getattr(view, "walk_children", None)
    if callable(walk):
        return list(walk())
    return list(getattr(view, "children", []) or [])


def stamp_report_context_on_sent(view, sent, client=None):
    if view is None or sent is None:
        return
    for child in _iter_view_items(view):
        report_context = getattr(child, "report_context", None)
        if not isinstance(report_context, dict):
            continue
        report_context["reply_message_id"] = getattr(sent, "id", None)
        report_context["reply_jump_url"] = str(getattr(sent, "jump_url", "") or "")
        if client is not None:
            report_context["client"] = client


def attach_failed_prompt_report_button(
    view,
    source_message,
    *,
    bub_response_text: str,
    failure_reason: str,
    report_context: Optional[dict] = None,
):
    if view is None:
        return None
    for child in _iter_view_items(view):
        if getattr(child, "custom_id", None) == "bub_failed_prompt_report":
            return view
    context = report_context or build_report_context_from_message(
        source_message,
        bub_response_text=bub_response_text,
        failure_reason=failure_reason,
    )
    button = FailedPromptReportButton(context)
    if isinstance(view, discord.ui.LayoutView) and not hasattr(view, "_controls"):
        view.add_item(discord.ui.ActionRow(button))
    else:
        view.add_item(button)
    return view


def attach_frame_report_button(
    view,
    *,
    game: str,
    row,
    source_message=None,
    prompt: str = "",
    failure_reason: str = "frame_data_response",
):
    context = build_frame_report_context(
        game=game,
        row=row,
        source_message=source_message,
        prompt=prompt,
        failure_reason=failure_reason,
    )
    return attach_failed_prompt_report_button(
        view,
        source_message,
        bub_response_text=context["bub_response_text"],
        failure_reason=failure_reason,
        report_context=context,
    )


def attach_combo_report_button(
    view,
    *,
    game: str,
    char_key: str,
    source_message=None,
    prompt: str = "",
    section=None,
    subsection=None,
):
    context = build_combo_report_context(
        game=game,
        char_key=char_key,
        source_message=source_message,
        prompt=prompt,
        section=section,
        subsection=subsection,
    )
    return attach_failed_prompt_report_button(
        view,
        source_message,
        bub_response_text=context["bub_response_text"],
        failure_reason="combo_response",
        report_context=context,
    )


def build_failed_prompt_report_view(source_message, *, bub_response_text: str, failure_reason: str):
    view = discord.ui.View(timeout=None)
    attach_failed_prompt_report_button(
        view,
        source_message,
        bub_response_text=bub_response_text,
        failure_reason=failure_reason,
    )
    return view
