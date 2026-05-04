"""
Telegram notifications for market intelligence summaries.

Uses sendMessage with parse_mode=HTML (Telegram's HTML subset — bold, italic,
readable lists). LLM fields are escaped so entities parse reliably.

Long text is split into multiple messages (4096 char limit).

Env:
  TELEGRAM_BOT_TOKEN — from @BotFather
  TELEGRAM_CHAT_ID   — channel id (e.g. -1001234567890) or group id
"""

from __future__ import annotations

import html
import os
import re
from typing import Any, Dict, List

import requests

# Stay under Telegram's 4096 limit; leave room for "(n/m)" prefixes and tags.
_CHUNK = 3500


def _e(value: Any) -> str:
    """Escape text for Telegram HTML (<, >, &)."""
    return html.escape(str(value if value is not None else ""), quote=False)


def format_market_brief_html(data: Dict[str, Any]) -> str:
    """
    Build a single HTML message body for Telegram (parse_mode=HTML).

    Telegram allows: <b>, <strong>, <i>, <em>, <u>, <s>, <code>, <pre>, <a href="">, etc.
    We keep structure simple so splits between messages rarely break tags.
    """
    parts: List[str] = []

    parts.append("<b>BotCore — Morning market brief</b>")
    fetched = data.get("_fetched_at") or data.get("_db_created_at") or ""
    if fetched:
        parts.append(f"<i>{_e(fetched)}</i>")
    parts.append("")

    headline = data.get("headline") or "—"
    parts.append(f"<b>{_e(headline)}</b>")
    parts.append("")

    regime = data.get("market_regime")
    risk = data.get("risk_profile")
    if regime or risk:
        parts.append(
            f"<b>Regime</b>: {_e(regime or '—')}   "
            f"<b>Risk</b>: {_e(risk or '—')}"
        )
        parts.append("")

    summary = (data.get("market_summary") or "").strip()
    if summary:
        parts.append("<b>Summary</b>")
        # Preserve paragraph breaks from synthesis (\n\n → spacing)
        for para in re.split(r"\n\s*\n", summary):
            para = para.strip()
            if para:
                parts.append(_e(para))
        parts.append("")

    takeaways = data.get("key_takeaways") or []
    if takeaways:
        parts.append("<b>Key takeaways</b>")
        for i, t in enumerate(takeaways, 1):
            parts.append(f"{i}. {_e(t)}")
        parts.append("")

    drivers = data.get("drivers_outlook") or {}
    if isinstance(drivers, dict) and drivers:
        parts.append("<b>Drivers</b>")
        for k, v in drivers.items():
            parts.append(f"• <b>{_e(k)}</b>: {_e(v)}")
        parts.append("")

    catalysts = (data.get("upcoming_catalysts") or "").strip()
    if catalysts:
        parts.append("<b>Upcoming catalysts</b>")
        for line in catalysts.split("\n"):
            line = line.strip()
            if line:
                parts.append(_e(line))
        parts.append("")

    risk_env = (data.get("risk_environment") or "").strip()
    if risk_env:
        parts.append("<b>Risk environment</b>")
        parts.append(_e(risk_env))
        parts.append("")

    nuanced = data.get("nuanced_points") or []
    if nuanced:
        parts.append("<b>Nuanced</b>")
        for i, t in enumerate(nuanced, 1):
            parts.append(f"{i}. {_e(t)}")

    err = data.get("synthesis_error")
    if err:
        parts.append("")
        parts.append(f"<i>Synthesis note: {_e(err)}</i>")

    return "\n".join(parts).strip()


def _split_chunks(text: str, max_len: int = _CHUNK) -> List[str]:
    """Split on newlines so we avoid chopping mid-line; keeps HTML safer."""
    if len(text) <= max_len:
        return [text]
    out: List[str] = []
    rest = text
    while rest:
        if len(rest) <= max_len:
            out.append(rest)
            break
        cut = rest.rfind("\n", 0, max_len)
        if cut < max_len // 2:
            cut = max_len
        out.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    return out


def send_market_brief_to_telegram(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format market data as Telegram HTML and send to TELEGRAM_CHAT_ID.
    Returns { ok, message_ids?, parts?, error? }.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return {
            "ok": False,
            "error": "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set",
        }

    text = format_market_brief_html(data)
    chunks = _split_chunks(text)
    base = f"https://api.telegram.org/bot{token}"
    message_ids: List[int] = []

    for i, chunk in enumerate(chunks):
        prefix = ""
        if len(chunks) > 1:
            prefix = f"<i>(part {i + 1}/{len(chunks)})</i>\n"
        payload = {
            "chat_id": chat_id,
            "text": prefix + chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            r = requests.post(f"{base}/sendMessage", json=payload, timeout=45)
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if not r.ok or not body.get("ok"):
                desc = body.get("description") or r.text or r.reason
                return {
                    "ok": False,
                    "error": f"Telegram API error: {r.status_code} {desc}",
                    "message_ids": message_ids,
                    "parts_sent": len(message_ids),
                }
            mid = body.get("result", {}).get("message_id")
            if mid is not None:
                message_ids.append(mid)
        except requests.RequestException as e:
            return {
                "ok": False,
                "error": str(e),
                "message_ids": message_ids,
                "parts_sent": len(message_ids),
            }

    return {"ok": True, "message_ids": message_ids, "parts": len(chunks), "parse_mode": "HTML"}
