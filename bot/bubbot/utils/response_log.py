"""Append-only JSONL interaction logging plus owner DM/channel notifications."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

INTERACTION_PROMPT = "prompt"
INTERACTION_SLASH = "slash_command"
INTERACTION_MENU = "menu"
INTERACTION_REPORT = "user_report"

FAILED_PROMPT_REASONS = frozenset(
    {
        "public_invalid_query",
        "missing_scrolls",
        "missing_hitbox_gif",
    }
)


def get_response_log_path(base_dir: Optional[str] = None) -> str:
    path_text = str(os.getenv("BUB_RESPONSE_LOG_FILE", "bub_response_log.jsonl") or "").strip()
    if not path_text:
        path_text = "bub_response_log.jsonl"
    if os.path.isabs(path_text):
        return path_text
    root = base_dir or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(root, path_text)


def _utc_now_fields() -> dict[str, str]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return {
        "timestamp_utc": now.isoformat(),
        "date_utc": now.date().isoformat(),
        "time_utc": now.time().isoformat(),
    }


def append_log_record(record: dict[str, Any], *, base_dir: Optional[str] = None) -> str:
    path = get_response_log_path(base_dir)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def summarize_embed(embed) -> str:
    if embed is None:
        return ""
    title = str(getattr(embed, "title", "") or "").strip()
    description = str(getattr(embed, "description", "") or "").strip()
    parts = [part for part in (title, description) if part]
    for field in list(getattr(embed, "fields", None) or []):
        name = str(getattr(field, "name", "") or "").strip()
        value = str(getattr(field, "value", "") or "").strip()
        if name or value:
            parts.append(f"{name}: {value}" if name else value)
    return "\n".join(parts).strip()


def summarize_sent_message(message) -> tuple[str, str]:
    content = str(getattr(message, "content", "") or "").strip()
    embed_parts = [summarize_embed(embed) for embed in list(getattr(message, "embeds", None) or [])]
    embed_parts = [part for part in embed_parts if part]
    embed_text = "\n\n".join(embed_parts).strip()
    attachment_urls = [
        str(getattr(item, "url", "") or getattr(item, "filename", "") or "").strip()
        for item in list(getattr(message, "attachments", None) or [])
        if getattr(item, "url", None) or getattr(item, "filename", None)
    ]
    attachment_text = " | ".join(url for url in attachment_urls if url)
    chunks = [part for part in (content, embed_text, attachment_text) if part]
    summary = "\n\n".join(chunks).strip()
    if embed_text and attachment_text:
        kind = "embed+gif"
    elif embed_text:
        kind = "embed"
    elif attachment_text:
        kind = "gif"
    elif content:
        kind = "text"
    else:
        kind = "unknown"
    return kind, summary or "(no visible content)"


def _message_context(message) -> dict[str, Any]:
    author = getattr(message, "author", None)
    guild = getattr(message, "guild", None)
    channel = getattr(message, "channel", None)
    return {
        "guild_id": getattr(guild, "id", None),
        "guild_name": str(getattr(guild, "name", "") or ""),
        "channel_id": getattr(channel, "id", None),
        "channel_name": str(getattr(channel, "name", "") or ""),
        "user_id": getattr(author, "id", None),
        "user_name": str(getattr(author, "display_name", "") or getattr(author, "name", "") or ""),
        "message_id": getattr(message, "id", None),
        "jump_url": str(getattr(message, "jump_url", "") or ""),
    }


def build_log_record(
    *,
    interaction_type: str,
    reason: str,
    prompt: str = "",
    response_text: str = "",
    response_kind: str = "text",
    user_report: str = "",
    failure_reason: str = "",
    source_message=None,
    reply_message=None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    record = {
        **_utc_now_fields(),
        "interaction_type": interaction_type,
        "reason": reason,
        "prompt": prompt,
        "response_text": response_text,
        "response_kind": response_kind,
        "user_report": user_report,
        "failure_reason": failure_reason,
    }
    if source_message is not None:
        record.update({f"source_{key}": value for key, value in _message_context(source_message).items()})
    if reply_message is not None:
        ctx = _message_context(reply_message)
        record["reply_message_id"] = ctx.pop("message_id", None)
        record["reply_jump_url"] = ctx.pop("jump_url", None)
        record.update({f"reply_{key}": value for key, value in ctx.items()})
    if extra:
        record.update(extra)
    return record


def log_record(record: dict[str, Any], *, base_dir: Optional[str] = None) -> str:
    return append_log_record(record, base_dir=base_dir)


def log_message_and_reply(
    source_message,
    reply_message,
    *,
    interaction_type: str = INTERACTION_PROMPT,
    reason: str,
    prompt: str = "",
    response_kind: str = "",
    response_text: str = "",
    user_report: str = "",
    failure_reason: str = "",
    extra: Optional[dict[str, Any]] = None,
    base_dir: Optional[str] = None,
) -> str:
    if not response_kind or not response_text:
        inferred_kind, inferred_text = summarize_sent_message(reply_message)
        response_kind = response_kind or inferred_kind
        response_text = response_text or inferred_text
    if not prompt:
        prompt = str(getattr(source_message, "content", "") or "")
    record = build_log_record(
        interaction_type=interaction_type,
        reason=reason,
        prompt=prompt,
        response_text=response_text,
        response_kind=response_kind,
        user_report=user_report,
        failure_reason=failure_reason,
        source_message=source_message,
        reply_message=reply_message,
        extra=extra,
    )
    return log_record(record, base_dir=base_dir)


def log_from_interaction(
    interaction,
    *,
    interaction_type: str,
    reason: str,
    prompt: str = "",
    response_text: str = "",
    response_kind: str = "text",
    extra: Optional[dict[str, Any]] = None,
    base_dir: Optional[str] = None,
) -> str:
    user = getattr(interaction, "user", None)
    guild = getattr(interaction, "guild", None)
    channel = getattr(interaction, "channel", None)
    record = build_log_record(
        interaction_type=interaction_type,
        reason=reason,
        prompt=prompt,
        response_text=response_text,
        response_kind=response_kind,
        extra={
            "guild_id": getattr(guild, "id", None),
            "guild_name": str(getattr(guild, "name", "") or ""),
            "channel_id": getattr(channel, "id", None),
            "channel_name": str(getattr(channel, "name", "") or ""),
            "user_id": getattr(user, "id", None),
            "user_name": str(getattr(user, "display_name", "") or getattr(user, "name", "") or ""),
            **(extra or {}),
        },
    )
    return log_record(record, base_dir=base_dir)


# Owner notifications: a post in the bot's error channel (BUB_OWNER_NOTIFY_CHANNEL_ID / BUB_LOG_CHANNEL_ID,
# "off" for none), where SF Stats reports too. No DM unless BUB_OWNER_USER_ID names someone.
_DEFAULT_NOTIFY_CHANNEL_ID = 1563332693423431900


def get_owner_notify_channel_id() -> Optional[int]:
    for env_name in ("BUB_OWNER_NOTIFY_CHANNEL_ID", "BUB_LOG_CHANNEL_ID"):
        raw = str(os.getenv(env_name, "") or "").strip()
        if raw.lower() == "off":
            return None
        if raw.isdigit():
            return int(raw)
    return _DEFAULT_NOTIFY_CHANNEL_ID


def get_owner_notify_user_id() -> Optional[int]:
    for env_name in ("BUB_OWNER_USER_ID", "BUB_RESPONSE_LOG_DM_USER_ID"):
        raw = str(os.getenv(env_name, "") or "").strip()
        if raw.isdigit():
            return int(raw)
    return None


def format_owner_notification(record: dict[str, Any]) -> str:
    lines = [
        f"**Bub log — {record.get('reason', 'event')}**",
        f"Type: `{record.get('interaction_type', '')}`",
    ]
    if record.get("failure_reason"):
        lines.append(f"Failure: `{record['failure_reason']}`")
    if record.get("guild_name") or record.get("source_guild_name"):
        lines.append(f"Server: {record.get('guild_name') or record.get('source_guild_name')}")
    if record.get("channel_name") or record.get("source_channel_name"):
        lines.append(f"Channel: #{record.get('channel_name') or record.get('source_channel_name')}")
    if record.get("user_name") or record.get("source_user_name"):
        lines.append(f"User: {record.get('user_name') or record.get('source_user_name')}")
    if record.get("prompt"):
        lines.append(f"Prompt: {record['prompt'][:1800]}")
    if record.get("response_text"):
        lines.append(f"Bub said: {record['response_text'][:1800]}")
    if record.get("user_report"):
        lines.append(f"User report: {record['user_report'][:1800]}")
    jump = record.get("reply_jump_url") or record.get("source_jump_url") or record.get("jump_url")
    if jump:
        lines.append(jump)
    return "\n".join(lines)


async def notify_owner(client, record: dict[str, Any]) -> bool:
    if client is None:
        return False
    content = format_owner_notification(record)
    if len(content) > 1900:
        content = content[:1900] + "…"
    delivered = False
    owner_id = get_owner_notify_user_id()
    if owner_id:
        try:
            user = client.get_user(owner_id)
            if user is None:
                user = await client.fetch_user(owner_id)
            await user.send(content)
            delivered = True
        except Exception as exc:
            print(f"Owner notify DM error: {exc}", flush=True)
    channel_id = get_owner_notify_channel_id()
    if channel_id:
        try:
            channel = client.get_channel(channel_id)
            if channel is None:
                channel = await client.fetch_channel(channel_id)
            # Prompts are user text: never let one ping the channel.
            import discord

            await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
            delivered = True
        except Exception as exc:
            print(f"Owner notify channel error (channel={channel_id}): {exc}", flush=True)
    return delivered


async def log_and_notify_owner(
    client,
    record: dict[str, Any],
    *,
    base_dir: Optional[str] = None,
) -> str:
    path = log_record(record, base_dir=base_dir)
    await notify_owner(client, record)
    return path
