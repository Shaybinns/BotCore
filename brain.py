"""
Brain - Core Trading Logic

Orchestrates the trading decision-making process by coordinating
OHLC analysis, chart analysis, market data, and AI reasoning.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional

from ohlc_analyzer import analyze_ohlc_data
from chart_analyzer import analyze_charts_with_gpt_vision
from market_data import get_market_data
from prompt import compose_sod_prompt, compose_intraday_prompt
from llm_model import call_gpt_mini
from database import (
    get_analysis_record,
    get_market_data_cache,
    save_market_data_cache,
    save_sod_analysis,
    save_intraday_analysis,
    save_test_run,
    get_current_positions,
    get_strategy,
    get_account_context_for_analysis,
)

# Market data is cached in the DB as 'market_data_note'.
# Intraday runs re-use it if fresher than this threshold.
MARKET_DATA_CACHE_HOURS = 4

_LONDON_TZ = ZoneInfo("Europe/London")


def _london_time_str() -> str:
    """Return a human-readable London time string with UTC offset label."""
    now_london = datetime.now(_LONDON_TZ)
    offset_h = int(now_london.utcoffset().total_seconds() // 3600)
    abbr = "BST" if offset_h == 1 else "GMT"
    return now_london.strftime(f"%Y-%m-%d %H:%M {abbr} (UTC{offset_h:+d})")


def _require_strategy_prompt(strategy_name: Optional[str]) -> str:
    """Load strategy prompt from DB; required for every SOD/intraday run."""
    name = (strategy_name or "").strip()
    if not name:
        raise ValueError(
            "Missing required strategy. All analysis runs must be linked to a named strategy."
        )
    record = get_strategy(name)
    if not record or not (record.get("strategy_prompt") or "").strip():
        raise ValueError(
            f"Strategy '{name}' not found in database. "
            "Use GET /api/strategies or POST /api/strategies to register it."
        )
    return record["strategy_prompt"].strip()


def _strategy_mandate_lines(strategy_name: str) -> List[str]:
    """Opening lines for the trading user prompt: bind decisions to the system strategy block."""
    name_line = f"Strategy name for this run: {strategy_name.strip()}"
    return [
        "=== STRATEGY MANDATE (READ FIRST) ===",
        "All analysis and trading decisions in your output must follow the ACTIVE TRADING STRATEGY in your "
        "system message: setup rules, invalidation, risk, session logic, and action: null / ENTER / MANAGE / EXIT. "
        "Ground every conclusion in that strategy; do not substitute a different methodology or contradict it.",
        name_line,
        "",
    ]


def _london_morning_brief_note_valid_for_sod(cached: Optional[Dict[str, Any]]) -> bool:
    """
    True if market_data_note was written today in Europe/London at or after 05:00 local.

    The scheduled morning brief (before SOD) populates this row; same-day SOD then reuses
    it instead of re-fetching RapidAPI / Perplexity / synthesis.
    """
    if not cached:
        return False
    created_at_str = cached.get("_db_created_at")
    if not created_at_str:
        return False
    try:
        created = datetime.fromisoformat(created_at_str)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        london = created.astimezone(_LONDON_TZ)
        now_ldn = datetime.now(_LONDON_TZ)
        cutoff = now_ldn.replace(hour=5, minute=0, second=0, microsecond=0)
        if london.date() != now_ldn.date():
            return False
        return london >= cutoff
    except Exception:
        return False


_CHART_TF_ALLOWED = frozenset({"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"})


def _normalize_chart_timeframe_code(raw: str) -> Optional[str]:
    """Map EA OHLC key or MT5 code to chart-img timeframe (H1, H4, …)."""
    tf = str(raw or "").strip().upper().replace("_DATA", "")
    aliases = {
        "1H": "H1", "4H": "H4", "1D": "D1", "1W": "W1",
        "5M": "M5", "15M": "M15", "30M": "M30",
    }
    tf = aliases.get(tf, tf)
    return tf if tf in _CHART_TF_ALLOWED else None


def _chart_timeframes_from_ohlc_keys(ohlc_data: Dict[str, Any]) -> List[str]:
    """Map EA OHLC keys (e.g. M5_DATA, H1_DATA) to chart-img timeframe codes."""
    out: List[str] = []
    for key in sorted(ohlc_data.keys()):
        tf = _normalize_chart_timeframe_code(key)
        if tf and tf not in out:
            out.append(tf)
        if len(out) >= 4:
            break
    return out


def analysis_note_text(stored: Optional[str], analysis_key: str) -> Optional[str]:
    """Extract analysis prose from stored JSON (or return legacy plain text)."""
    if not stored:
        return None
    text = stored.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            val = data.get(analysis_key)
            if val is not None:
                return str(val).strip() or None
    except json.JSONDecodeError:
        pass
    return text


def _chart_block_for_context(chart_observations: Any) -> str:
    """Plain-text Vision output, or JSON for error dicts / legacy dict payloads."""
    if isinstance(chart_observations, str):
        return chart_observations
    return json.dumps(chart_observations, indent=2)


def _user_run_instructions(
    run: str,
    analysis_key: str,
    fill_event: Optional[str] = None,
) -> List[str]:
    """
    Opening block for user_prompt: task instructions first, then context is appended after.
    run: "sod" | "intraday"
    """
    if run == "sod":
        analysis_line = (
            "A 3–5 sentence, comprehensive Start of Day analysis that sets your bias "
            "and trading plan for the full day ahead."
        )
    elif fill_event:
        analysis_line = (
            f"A 3–5 sentence intraday analysis. This run was triggered by a confirmed broker event: {fill_event}. "
            "Sentence 1 MUST acknowledge that the execution worked (e.g. your entry is live, the position closed, "
            "SL/TP hit, or a partial close completed)—then thread from your SOD and prior intraday notes as usual. "
            "Continue with current market read, what you watch next, and whether you are placing or managing positions."
        )
    else:
        analysis_line = (
            "A 3–5 sentence intraday analysis. Sentence 1 MUST synthesise your previous analyses, "
            "comparing your current analysis to that of your SOD analysis, and intraday analysis (if available), continuing on or invalidating the previous analyses and your trail of thought so far based on your strategy, "
            "allowing enough context for your future self, in your next intraday run, to understand the decision making flow to where you are now. "
            "Then you must continue by giving your current analysis based on your strategy, continuing from previous, if you are closer to an entry, what you are looking for and what could come next. "
            "Your trading today per the strategy, what you watch, what would change your view, and whether you are placing or managing positions."
        )

    return [
        "=== YOUR TASK (READ FIRST) ===",
        "You must now run your analysis using all context information provided below.",
        "Your analysis will be based on your strategy provided, and you will be outputting your analysis, your next review time, your monitoring timeframes at next review time, and the execution details for any trades you will be placing or managing. As your output will dictate what is traded.",
        "You must output valid JSON per your system prompt, only with exactly four top-level fields:",
        f'1. "{analysis_key}" — {analysis_line}',
        "2. next_review_time — when you will analyse the market again (London local time, no Z); "
        "schedule at a strategy catalyst (session open, H1/H4 close, level touch, news) — "
        "NOT the next M5 candle by default.",
        '3. monitoring_timeframes — JSON array of MT5 chart codes for the timeframes you will be analysing at your next review time (e.g. ["M5", "H1"]); only timeframes your strategy requires.',
        "4. executions — execution details (if any) for when you are placing or managing a trade:",
        '   { "action_type": "ENTER" | "MANAGE" | "EXIT" | null, "enter": {...}, "manage": {...}, "exit": {...} }',
        '   When action_type is ENTER, enter must include risk_percentage (whole number: 1 = 1% risk).',
        "   When action_type is null, omit enter/manage/exit sub-objects.",
        "",
        "Ensure you are following your ACTIVE TRADING STRATEGY (system message). "
        "Your analysis, monitoring_timeframes, next_review_time, and executions must all align with the strategy and with each other.",
        "Capital preservation comes first — but do not hesitate to place a trade precisely "
        "when conditions fully adhere to your strategy.",
        "",
    ]


def _parse_next_review_time(raw: Any) -> Optional[str]:
    """
    Normalize AI next_review_time to YYYY-MM-DDTHH:MM:SS (London local, no Z).
    Returns None if missing or unparseable.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in ("null", "none", "n/a"):
        return None

    text = text.replace("Z", "").replace("z", "").strip()
    text = text.replace(" ", "T")
    if "T" not in text and len(text) >= 16:
        text = text[:10] + "T" + text[11:]

    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            naive = datetime.strptime(text[:19] if "T" in text else text, fmt)
            normalized = naive.strftime("%Y-%m-%dT%H:%M:%S")
            london = naive.replace(tzinfo=_LONDON_TZ)
            if london.hour == 7 and london.minute == 0:
                print("[brain] WARNING: next_review_time is 07:00 London (SOD slot) — keeping as-is")
            return normalized
        except ValueError:
            continue

    m = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{1,2}):(\d{2})", text)
    if m:
        h, mi = int(m.group(2)), int(m.group(3))
        return f"{m.group(1)}T{h:02d}:{mi:02d}:00"

    print(f"[brain] WARNING: could not parse next_review_time: {raw!r}")
    return None


def _default_next_review_london() -> str:
    """Fallback when next_review_time is missing or already passed — next full hour London."""
    now = datetime.now(_LONDON_TZ)
    next_h1 = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    if next_h1 <= now:
        next_h1 += timedelta(hours=1)
    return next_h1.strftime("%Y-%m-%dT%H:%M:%S")


def _finalize_next_review_time(iso_str: Optional[str]) -> str:
    """Use the model's time as-is; only substitute next full hour if missing or in the past."""
    if not iso_str:
        fallback = _default_next_review_london()
        print(f"[brain] missing next_review_time — defaulting to {fallback} London")
        return fallback
    try:
        scheduled = datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=_LONDON_TZ)
    except ValueError:
        fallback = _default_next_review_london()
        print(f"[brain] unparseable next_review_time ({iso_str!r}) — defaulting to {fallback} London")
        return fallback
    if scheduled <= datetime.now(_LONDON_TZ):
        fallback = _default_next_review_london()
        print(f"[brain] next_review_time in the past ({iso_str}) — defaulting to {fallback} London")
        return fallback
    return iso_str


def _parse_monitoring_timeframes(
    raw: Any,
    default: Optional[List[str]] = None,
) -> List[str]:
    """Normalize monitoring_timeframes to a list of MT5 codes (e.g. M5, H1)."""
    allowed = {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"}
    if default is None:
        default = ["M5", "H1"]

    if raw is None:
        return list(default)

    items: List[str] = []
    if isinstance(raw, list):
        items = [str(x).strip().strip('"').upper() for x in raw if str(x).strip()]
    elif isinstance(raw, str):
        items = [p.strip().strip('"').upper() for p in raw.split(",") if p.strip()]

    normalized = []
    for tf in items:
        tf = tf.replace("_DATA", "")
        if tf in allowed:
            normalized.append(tf)
        elif tf == "1H":
            normalized.append("H1")
        elif tf == "5M":
            normalized.append("M5")

    return normalized or list(default)


def _normalize_action_type(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if isinstance(raw, str):
        val = raw.strip().upper()
        if val in ("", "NULL", "NONE", "CHECK", "WAIT", "WATCH", "HOTZONE"):
            return None
        if val in ("ENTER", "MANAGE", "EXIT"):
            return val
    return None


def _float_or_none(val: Any) -> Optional[float]:
    if val is None or val == "" or (isinstance(val, str) and val.lower() == "null"):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _build_bot_action_payload(
    parsed: Dict[str, Any],
    symbol: str,
    fill_event: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Map executions block from model JSON to the canonical payload for the EA.
    Shape must match prompt.py: executions.action_type + enter | manage | exit.
    """
    executions = parsed.get("executions")
    if not isinstance(executions, dict):
        executions = {}

    action_type = _normalize_action_type(executions.get("action_type"))
    next_review_time = _finalize_next_review_time(
        _parse_next_review_time(parsed.get("next_review_time"))
    )

    enter = manage = exit_payload = None

    if action_type == "ENTER":
        src = executions.get("enter")
        if not isinstance(src, dict):
            src = {}
        direction = str(src.get("direction", "")).strip().upper()
        enter = {
            "symbol": (src.get("symbol") or symbol or "").upper(),
            "direction": direction,
            "entry_price": _float_or_none(src.get("entry_price")),
            "stop_loss": _float_or_none(src.get("stop_loss")),
            "take_profit": _float_or_none(src.get("take_profit")),
            "risk_percentage": _float_or_none(src.get("risk_percentage")) or 1.0,
        }

    elif action_type == "MANAGE":
        src = executions.get("manage")
        if not isinstance(src, dict):
            src = {}
        trade_id = src.get("trade_id")
        manage = {
            "trade_id": int(trade_id) if trade_id is not None else None,
            "new_stop_loss": _float_or_none(src.get("new_stop_loss")),
            "new_take_profit": _float_or_none(src.get("new_take_profit")),
            "new_position_percentage": _float_or_none(src.get("new_position_percentage")),
        }

    elif action_type == "EXIT":
        src = executions.get("exit")
        if not isinstance(src, dict):
            src = {}
        trade_id = src.get("trade_id")
        exit_payload = {
            "trade_id": int(trade_id) if trade_id is not None else None,
        }

    bot_action = {
        "next_review_time": next_review_time,
        "action_type": action_type,
        "enter": enter,
        "manage": manage,
        "exit": exit_payload,
    }
    return _apply_execution_validation(bot_action)


def _apply_execution_validation(bot_action: Dict[str, Any]) -> Dict[str, Any]:
    """Server-side checks aligned with prompt rules and EA constraints."""
    action_type = bot_action.get("action_type")

    if action_type == "ENTER":
        ent = bot_action.get("enter") or {}
        sl = ent.get("stop_loss")
        tp = ent.get("take_profit")
        ep = ent.get("entry_price")
        direction = ent.get("direction")
        if direction not in ("BUY", "SELL"):
            print("[brain] REJECTED ENTER: direction must be BUY or SELL")
            bot_action["action_type"] = None
            bot_action["enter"] = None
        elif sl is None or tp is None:
            print("[brain] REJECTED ENTER: stop_loss and take_profit are required")
            bot_action["action_type"] = None
            bot_action["enter"] = None
        elif _float_or_none(sl) is None or _float_or_none(sl) <= 0:
            print("[brain] REJECTED ENTER: invalid stop_loss")
            bot_action["action_type"] = None
            bot_action["enter"] = None
        elif _float_or_none(tp) is None or _float_or_none(tp) <= 0:
            print("[brain] REJECTED ENTER: invalid take_profit")
            bot_action["action_type"] = None
            bot_action["enter"] = None
        elif _float_or_none(ep) is None or _float_or_none(ep) <= 0:
            print("[brain] REJECTED ENTER: invalid entry_price")
            bot_action["action_type"] = None
            bot_action["enter"] = None
        else:
            risk = _float_or_none(ent.get("risk_percentage"))
            if risk is None or risk <= 0:
                print("[brain] ENTER: risk_percentage missing — defaulting to 1%")
                ent["risk_percentage"] = 1.0
                bot_action["enter"] = ent
                risk = 1.0
            elif risk > 10:
                print("[brain] REJECTED ENTER: risk_percentage must be 0.1–10 (whole number, e.g. 1 = 1%)")
                bot_action["action_type"] = None
                bot_action["enter"] = None

    elif action_type == "MANAGE":
        mg = bot_action.get("manage") or {}
        if not mg.get("trade_id"):
            print("[brain] REJECTED MANAGE: trade_id required")
            bot_action["action_type"] = None
            bot_action["manage"] = None

    elif action_type == "EXIT":
        ex = bot_action.get("exit") or {}
        if not ex.get("trade_id"):
            print("[brain] REJECTED EXIT: trade_id required")
            bot_action["action_type"] = None
            bot_action["exit"] = None

    return bot_action


def _build_run_record_json(
    normalized: Dict[str, Any],
    analysis_key: str,
) -> str:
    """Persist full model run JSON (analysis + scheduling + executions) for chat/history."""
    ba = normalized.get("bot_action") or {}
    doc = {
        analysis_key: normalized.get(analysis_key, ""),
        "next_review_time": ba.get("next_review_time"),
        "monitoring_timeframes": normalized.get("monitoring_timeframes"),
        "executions": {
            "action_type": ba.get("action_type"),
            "enter": ba.get("enter"),
            "manage": ba.get("manage"),
            "exit": ba.get("exit"),
        },
    }
    return json.dumps(doc, ensure_ascii=False)


def _flatten_for_ea(
    result: Dict[str, Any],
    analysis_key: str,
    magic_number: int,
) -> Dict[str, Any]:
    """
    Flatten bot_action into top-level keys for the EA (no nested JSON parsing in MQL5).
    """
    ba = result.get("bot_action") or {}
    mtf = result.get("monitoring_timeframes") or ["M5", "H1"]
    if isinstance(mtf, list):
        mtf_str = ",".join(str(x).strip().strip('"') for x in mtf)
    else:
        mtf_str = str(mtf).strip()

    enter = ba.get("enter") or {}
    manage = ba.get("manage") or {}
    exit_p = ba.get("exit") or {}

    return {
        "magic_number": magic_number,
        analysis_key: result.get(analysis_key, ""),
        "next_review_time": ba.get("next_review_time"),
        "action_type": ba.get("action_type"),
        "monitoring_timeframes": mtf_str,
        "enter_symbol": enter.get("symbol"),
        "enter_direction": enter.get("direction"),
        "enter_price": enter.get("entry_price"),
        "enter_stop_loss": enter.get("stop_loss"),
        "enter_take_profit": enter.get("take_profit"),
        "enter_risk_percentage": enter.get("risk_percentage"),
        "manage_trade_id": manage.get("trade_id"),
        "manage_new_stop_loss": manage.get("new_stop_loss"),
        "manage_new_take_profit": manage.get("new_take_profit"),
        "manage_new_position_percentage": manage.get("new_position_percentage"),
        "exit_trade_id": exit_p.get("trade_id"),
    }


def _record_test_run(
    magic_number: int,
    run_type: str,
    symbol: str,
    strategy_name: str,
    macro: Any,
    ohlc: Any,
    chart: Any,
    system_prompt: str,
    flat_output: Dict[str, Any],
    raw_gpt_response: str,
) -> None:
    """Save full AI inputs + flat output to test_inputs (non-fatal on failure)."""
    try:
        save_test_run(
            magic_number=magic_number,
            run_type=run_type,
            symbol=symbol,
            strategy_name=strategy_name,
            macro=macro,
            ohlc=ohlc,
            chart=chart,
            system_prompt=system_prompt,
            flat_output=flat_output,
            raw_gpt_response=raw_gpt_response,
        )
    except Exception as e:
        print(f"[brain] test_inputs save warning: {e}")


def _persist_trading_run(
    magic_number: int,
    symbol: str,
    strategy_name: str,
    analysis_key: str,
    stored_json: str,
) -> None:
    """Save full run JSON to analysis_notes (executions live in API response only)."""
    if analysis_key == "sod_analysis":
        save_sod_analysis(magic_number, symbol, strategy_name, stored_json)
    else:
        save_intraday_analysis(magic_number, symbol, strategy_name, stored_json)


def _normalize_trading_response(
    parsed: Dict[str, Any],
    analysis_key: str,
    symbol: str,
    fill_event: Optional[str] = None,
) -> Dict[str, Any]:
    """Map model JSON (four top-level fields) to analysis text + execution payload for the EA."""
    analysis_text = parsed.get(analysis_key)
    if analysis_text is None:
        print(f"[brain] WARNING: missing {analysis_key} in model output")
        analysis_text = ""
    if isinstance(analysis_text, dict):
        analysis_text = json.dumps(analysis_text)
    analysis_text = str(analysis_text).strip()

    bot_action = _build_bot_action_payload(parsed, symbol, fill_event=fill_event)

    return {
        analysis_key: analysis_text,
        "bot_action": bot_action,
        "monitoring_timeframes": _parse_monitoring_timeframes(
            parsed.get("monitoring_timeframes")
        ),
    }


def _context_section_header() -> List[str]:
    return [
        "=== CONTEXT (USE FOR YOUR ANALYSIS) ===",
        "Everything below is the data for this run. Read each section in order and tie your output to it.",
        "",
    ]


def _append_run_metadata_sod(
    parts: List[str],
    magic_number: int,
    symbol: str,
    strategy_name: str,
) -> None:
    parts.extend([
        "=== RUN INFO ===",
        "Bot instance, symbol, strategy, and London time for this SOD run.",
        "",
        f"MAGIC_NUMBER: {magic_number}",
        f"SYMBOL: {symbol}",
        f"STRATEGY: {strategy_name}",
        f"ANALYSIS DATE: {datetime.now(timezone.utc).isoformat()}",
        f"CURRENT TIME (London): {_london_time_str()}",
        "NOTE: All OHLC timestamps and chart images are London local time.",
        "      next_review_time must be London local (format: YYYY-MM-DDTHH:MM:SS, no Z).",
        "",
        "=== SYSTEM INFO ===",
        "SOD runs automatically at 07:00 London each morning. Set today's bias and first intraday check.",
        "",
    ])


def _append_run_metadata_intraday(
    parts: List[str],
    magic_number: int,
    symbol: str,
    strategy_name: str,
) -> None:
    parts.extend([
        "=== RUN INFO ===",
        "Bot instance, symbol, strategy, and London time for this intraday check.",
        "",
        f"MAGIC_NUMBER: {magic_number}",
        f"SYMBOL: {symbol}",
        f"STRATEGY: {strategy_name}",
        f"ANALYSIS TIME: {datetime.now(timezone.utc).isoformat()}",
        f"CURRENT TIME (London): {_london_time_str()}",
        "NOTE: All OHLC timestamps and chart images are London local time.",
        "      next_review_time must be London local (format: YYYY-MM-DDTHH:MM:SS, no Z).",
        "",
        "=== SYSTEM INFO ===",
        "Intraday check — continue from today's SOD and your prior intraday note if present.",
        "Do not schedule next_review_time at 07:00 London (reserved for automatic SOD).",
        "Open positions sync on broker fills — you do not need frequent scheduled checks unless the strategy requires it.",
        "",
    ])


def _append_market_context(parts: List[str], market_context: Dict[str, Any]) -> None:
    parts.extend([
        "=== MARKET INTELLIGENCE ===",
        "Forward-looking desk brief (next 24–72h): regime, catalysts, scenarios, asset biases. "
        "Use for predictive bias and timing — not to recap past news. "
        "Weight forward_bias_24_48h, upcoming_catalysts, event_scenarios, and what_to_watch.",
        "",
        json.dumps(market_context, indent=2),
        "",
    ])


def format_account_snapshot_line(account_ctx: Dict[str, Any]) -> str:
    """One-line account summary for prompts (trading + chat)."""
    keys = (
        "account_size", "realised_pnl", "today_realised_pnl",
        "unrealised_pnl", "week_pnl", "month_pnl",
    )
    if not any(account_ctx.get(k) is not None for k in keys):
        return "ACCOUNT: (no snapshot on file)"
    parts = []
    for key, label in (
        ("account_size", "size"),
        ("realised_pnl", "realised"),
        ("today_realised_pnl", "today"),
        ("unrealised_pnl", "unreal"),
        ("week_pnl", "week"),
        ("month_pnl", "month"),
    ):
        v = account_ctx.get(key)
        parts.append(f"{label}={v if v is not None else '—'}")
    return "ACCOUNT: " + " ".join(parts)


def format_positions_compact(positions: List[Dict[str, Any]]) -> str:
    """Compact open positions block (one line per position)."""
    if not positions:
        return "OPEN POSITIONS: none"
    lines = ["OPEN POSITIONS:"]
    for p in positions:
        tid = p.get("trade_id")
        lines.append(
            f"  trade_id={tid} {p.get('direction')} {p.get('asset')} "
            f"entry={p.get('entry_price')} sl={p.get('stop_loss')} tp={p.get('take_profit')} "
            f"lots={p.get('lot_size')}"
        )
    return "\n".join(lines)


def _append_analysis_and_positions_sod(
    parts: List[str],
    previous_intraday: Optional[str],
    db_positions: List[Dict[str, Any]],
) -> None:
    if previous_intraday:
        parts.extend([
            "=== YESTERDAY'S LAST INTRADAY CHECK ===",
            "Final intraday note from before today's SOD (cleared when SOD saves). Use only if still relevant.",
            "",
            previous_intraday,
            "",
        ])
    _append_positions_context(parts, db_positions)


def _append_analysis_and_positions_intraday(
    parts: List[str],
    sod_text: Optional[str],
    last_intraday: Optional[str],
    db_positions: List[Dict[str, Any]],
    fill_event: Optional[str] = None,
) -> None:
    if fill_event:
        parts.extend([
            "=== FILL EVENT ===",
            f"Broker-confirmed: {fill_event} (this intraday run was triggered immediately after).",
            "",
        ])
    if sod_text:
        parts.extend([
            "=== START OF DAY ANALYSIS ===",
            "Today's SOD bias and plan — carry forward unless you explicitly invalidate it.",
            "",
            sod_text,
            "",
        ])
    if last_intraday:
        parts.extend([
            "=== LAST INTRADAY CHECK ===",
            "Your previous intraday note from earlier today — sentence 1 must thread from this (continue / adjust / invalidate).",
            "",
            last_intraday,
            "",
        ])
    _append_positions_context(parts, db_positions)


def _append_positions_context(
    parts: List[str],
    db_positions: List[Dict[str, Any]],
) -> None:
    parts.extend([
        format_positions_compact(db_positions),
        "Use trade_id from open positions for MANAGE/EXIT.",
        "",
    ])


def _append_ohlc_context(parts: List[str], processed_ohlc: Dict[str, Any]) -> None:
    parts.extend([
        "=== OHLC DATA ANALYSIS ===",
        "Structured swings, imbalances, FVGs, and levels from raw candles (not the raw bars).",
        "",
        json.dumps(processed_ohlc, indent=2),
        "",
    ])


def _append_chart_context(parts: List[str], chart_observations: Any) -> None:
    parts.extend([
        "=== CHART VISUAL ANALYSIS ===",
        "GPT Vision observations from chart images — pure price action, no trade decision.",
        "",
        _chart_block_for_context(chart_observations),
        "",
    ])


def _append_account_context(context_parts: list, account_ctx: Dict[str, Any]) -> None:
    """Append latest account snapshot (one line, from DB)."""
    context_parts.extend([
        format_account_snapshot_line(account_ctx),
        "",
    ])


def _get_market_data_cached(symbol: str) -> Dict[str, Any]:
    """
    Load market data from DB cache (market_data_note) if fresh.
    Re-fetches from APIs only if the cache is missing or older than MARKET_DATA_CACHE_HOURS.
    Used by intraday_action inside the parallel executor.

    Stored under symbol="GLOBAL" — market intelligence is not symbol-specific
    (VIX, DXY, Fed policy, news apply to all pairs equally).
    """
    cached = get_market_data_cache()
    if cached:
        created_at_str = cached.get('_db_created_at')
        if created_at_str:
            try:
                created_at = datetime.fromisoformat(created_at_str)
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                age_hours = (datetime.now(timezone.utc) - created_at).total_seconds() / 3600
                if age_hours < MARKET_DATA_CACHE_HOURS:
                    print(f"[market] Using cached market data ({age_hours:.1f}h old)")
                    return cached
                print(f"[market] Cache is {age_hours:.1f}h old — refreshing...")
            except Exception:
                print("[market] Cache timestamp unreadable — refreshing...")
        else:
            print("[market] Cache has no timestamp — refreshing...")
    else:
        print("[market] No market data cache in DB — fetching fresh...")

    data = get_market_data(symbol)
    save_market_data_cache(data)
    print("[market] Market data refreshed and saved to DB")
    return data


def sod_action(
    symbol: str,
    ohlc_data: Dict[str, List[Dict[str, Any]]],
    magic_number: int,
    strategy_name: str,
) -> Dict[str, Any]:
    """
    Start of Day (SOD) action - comprehensive daily market analysis.

    Workflow:
      0. Load DB positions + account + previous run note + strategy (required)
      1. OHLC analysis
      2. Market data  — reuses today's London morning brief (≥05:00) in DB if present;
                        otherwise fetches and saves (market_data_note)
      3. Chart visual analysis — GPT Vision on H4 and D1 charts only (images only, no other context)
      4. Trading decision  — GPT-4o-mini receives full context including chart observations
      5. Persist sod_analysis to analysis_notes (overwrites; clears intraday for new day)

    Args:
        symbol:        Trading symbol (e.g. "GBPUSD")
        ohlc_data:     {timeframe_key: [candle dicts], ...}
        magic_number:  MT5 EA magic number (unique per bot instance)
        strategy_name: Name of the strategy to load from DB (required)

    Returns:
        Comprehensive SOD analysis as JSON dictionary
    """
    print("=" * 60)
    print(f"SOD Analysis starting: {symbol} (magic {magic_number})")
    print("=" * 60)

    try:
        strategy_prompt_text = _require_strategy_prompt(strategy_name)
    except ValueError as e:
        print(f"[brain] {e}")
        return {
            "error": str(e),
            "symbol": symbol,
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
        }

    scoped_strategy = strategy_name.strip()
    print(f"[brain] Strategy loaded: '{scoped_strategy}'")

    # ------------------------------------------------------------------
    # Step 0: DB reads (fast, sequential — no API calls)
    # ------------------------------------------------------------------
    print("\n[brain] Loading DB context...")
    db_positions = get_current_positions(symbol, magic_number)
    analysis_record = get_analysis_record(magic_number, symbol, scoped_strategy)
    previous_intraday = (
        analysis_note_text(
            analysis_record.get("intraday_analysis") if analysis_record else None,
            "intraday_analysis",
        )
    )
    print(f"[brain] Positions: {len(db_positions)} open" if db_positions else "[brain] No open positions")
    print("[brain] Previous intraday note found" if previous_intraday else "[brain] No previous intraday note")

    # ------------------------------------------------------------------
    # Step 1: Parallel data collection
    #   - OHLC analysis       (CPU-light, fast)
    #   - Chart visual analysis (chart-img.com fetches + GPT Vision)
    #   - Market data           (morning brief cache if valid, else RapidAPI + 2x Perplexity)
    # All three are independent. They converge in Step 2.
    # ------------------------------------------------------------------
    # SOD: H4 + D1 only (matches EA 4h_DATA / 1D_DATA payloads).
    timeframes = ["H4", "D1"]

    def _fetch_market_data_sod():
        cached = get_market_data_cache()
        if cached and _london_morning_brief_note_valid_for_sod(cached):
            print("[market] Using morning brief market data cache for SOD (no refetch)")
            return cached
        data = get_market_data(symbol)
        save_market_data_cache(data)
        print("[market] Market data fetched and saved to DB")
        return data

    def _run_ohlc():
        return analyze_ohlc_data(ohlc_data)

    def _run_charts():
        return analyze_charts_with_gpt_vision(symbol=symbol, timeframes=timeframes)

    print("\n[brain] Step 1: Collecting data in parallel (OHLC + Charts + Market)...")

    processed_ohlc    = {}
    chart_observations = ""
    market_context    = {}

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_ohlc   = executor.submit(_run_ohlc)
        future_charts = executor.submit(_run_charts)
        future_market = executor.submit(_fetch_market_data_sod)

        for future in as_completed([future_ohlc, future_charts, future_market]):
            if future is future_ohlc:
                try:
                    processed_ohlc = future.result()
                    print(f"[brain] OHLC done — {len(processed_ohlc.get('timeframes', {}))} timeframes")
                except Exception as e:
                    print(f"[brain] OHLC error: {e}")

            elif future is future_charts:
                try:
                    chart_result = future.result()
                    chart_observations = chart_result.get("chart_analysis", "") or ""
                    print(f"[brain] Charts done — {len(chart_observations)} chars vision text")
                except Exception as e:
                    print(f"[brain] Chart error: {e}")
                    chart_observations = {"error": str(e)}

            elif future is future_market:
                try:
                    market_context = future.result()
                    print("[brain] Market data done")
                except Exception as e:
                    print(f"[brain] Market data error: {e}")

    # ------------------------------------------------------------------
    # Step 2: Assemble full context (user prompt)
    # ------------------------------------------------------------------
    print("\n[brain] Step 2: Assembling decision context...")

    account_ctx = get_account_context_for_analysis(magic_number)
    context_parts: List[str] = []
    context_parts.extend(_user_run_instructions("sod", "sod_analysis"))
    context_parts.extend(_strategy_mandate_lines(scoped_strategy))
    context_parts.extend(_context_section_header())
    _append_run_metadata_sod(context_parts, magic_number, symbol, scoped_strategy)
    _append_market_context(context_parts, market_context)
    _append_analysis_and_positions_sod(
        context_parts, previous_intraday, db_positions
    )
    _append_ohlc_context(context_parts, processed_ohlc)
    _append_chart_context(context_parts, chart_observations)
    _append_account_context(context_parts, account_ctx)

    full_context = "\n".join(context_parts)

    # ------------------------------------------------------------------
    # Step 3: Trading decision — gpt-5.4-mini with full assembled context
    # ------------------------------------------------------------------
    print("\n[brain] Step 3: Sending to gpt-5.4-mini for SOD trading decision...")
    try:
        sod_prompt    = compose_sod_prompt(strategy_prompt_text)
        response_text = call_gpt_mini(
            system_prompt=sod_prompt,
            user_prompt=full_context,
            temperature=0.2,
        )
        print(f"[brain] gpt-5.4-mini response received ({len(response_text)} chars)")

        parsed = _parse_gpt_response(response_text)
        result = _normalize_trading_response(parsed, "sod_analysis", symbol)

        print("[brain] Saving SOD run JSON to analysis_notes...")
        try:
            _persist_trading_run(
                magic_number,
                symbol,
                scoped_strategy,
                "sod_analysis",
                _build_run_record_json(result, "sod_analysis"),
            )
            print("[brain] sod_analysis saved")
        except Exception as e:
            print(f"[brain] Error saving SOD to database: {e}")

        result["sod_metadata"] = {
            "magic_number": magic_number,
            "symbol": symbol,
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "ohlc_timeframes_analyzed": list(ohlc_data.keys()),
            "chart_timeframes_analyzed": timeframes,
            "previous_intraday_context_used": previous_intraday is not None,
            "strategy_used": scoped_strategy,
        }

        print("\n" + "=" * 60)
        print("SOD Analysis complete")
        print("=" * 60)
        flat = _flatten_for_ea(result, "sod_analysis", magic_number)
        flat["sod_metadata"] = result["sod_metadata"]
        _record_test_run(
            magic_number=magic_number,
            run_type="sod",
            symbol=symbol,
            strategy_name=scoped_strategy,
            macro=market_context,
            ohlc=processed_ohlc,
            chart=chart_observations,
            system_prompt=sod_prompt,
            flat_output=flat,
            raw_gpt_response=response_text,
        )
        return flat

    except Exception as e:
        print(f"[brain] GPT decision call failed: {e}")
        return {
            "error": f"Failed to complete SOD analysis: {str(e)}",
            "symbol": symbol,
            "analysis_timestamp": datetime.now(timezone.utc).isoformat()
        }


def intraday_action(
    symbol: str,
    ohlc_data: Dict[str, List[Dict[str, Any]]],
    magic_number: int,
    strategy_name: str,
    fill_event: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Intraday action - active trading analysis during the day.

    Workflow:
      0. Load DB positions + SOD note + last run note + strategy (required)
      1. OHLC analysis
      2. Market data  — loaded from DB cache (market_data_note saved by SOD).
                        Re-fetched only if cache is missing or older than MARKET_DATA_CACHE_HOURS.
      3. Chart visual analysis — GPT Vision sees only the chart images, no context
      4. Trading decision  — GPT-4o-mini receives full context including chart observations
      5. Persist intraday_analysis to analysis_notes (overwrites prior intraday text)

    Args:
        symbol:        Trading symbol (e.g. "GBPUSD")
        ohlc_data:     {timeframe_key: [candle dicts], ...}
        magic_number:  MT5 EA magic number (unique per bot instance)
        fill_event:    Optional broker event that triggered this run (ENTRY_FILL, EXIT, SL, TP, PARTIAL)
        strategy_name: Name of the strategy to load from DB (required)

    Returns:
        Intraday trading decision as JSON dictionary
    """
    print("=" * 60)
    print(f"Intraday Analysis starting: {symbol} (magic {magic_number})")
    print("=" * 60)

    try:
        strategy_prompt_text = _require_strategy_prompt(strategy_name)
    except ValueError as e:
        print(f"[brain] {e}")
        return {
            "error": str(e),
            "symbol": symbol,
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
        }

    scoped_strategy = strategy_name.strip()
    print(f"[brain] Strategy loaded: '{scoped_strategy}'")

    # ------------------------------------------------------------------
    # Step 0: DB reads (fast, sequential — no API calls)
    # ------------------------------------------------------------------
    print("\n[brain] Loading DB context...")
    db_positions = get_current_positions(symbol, magic_number)
    analysis_record = get_analysis_record(magic_number, symbol, scoped_strategy)
    sod_text = analysis_note_text(
        analysis_record.get("sod_analysis") if analysis_record else None,
        "sod_analysis",
    )
    last_intraday = analysis_note_text(
        analysis_record.get("intraday_analysis") if analysis_record else None,
        "intraday_analysis",
    )
    print(f"[brain] Positions: {len(db_positions)} open" if db_positions else "[brain] No open positions")
    print(f"[brain] SOD analysis: {'found' if sod_text else 'missing'} | Prior intraday: {'found' if last_intraday else 'missing'}")

    timeframes = _chart_timeframes_from_ohlc_keys(ohlc_data) or ["M5", "H1"]

    # ------------------------------------------------------------------
    # Step 1: Parallel data collection
    #   - OHLC analysis        (CPU-light, fast)
    #   - Chart visual analysis (chart-img.com fetches + GPT Vision)
    #   - Market data           (DB cache if fresh, re-fetch only if stale)
    # All three are independent. They converge in Step 2.
    # ------------------------------------------------------------------
    def _run_ohlc():
        return analyze_ohlc_data(ohlc_data)

    def _run_charts():
        return analyze_charts_with_gpt_vision(symbol=symbol, timeframes=timeframes)

    print("\n[brain] Step 1: Collecting data in parallel (OHLC + Charts + Market)...")

    processed_ohlc     = {}
    chart_observations = ""
    market_context     = {}
    market_from_cache  = False

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_ohlc   = executor.submit(_run_ohlc)
        future_charts = executor.submit(_run_charts)
        future_market = executor.submit(_get_market_data_cached, symbol)

        for future in as_completed([future_ohlc, future_charts, future_market]):
            if future is future_ohlc:
                try:
                    processed_ohlc = future.result()
                    print(f"[brain] OHLC done — {len(processed_ohlc.get('timeframes', {}))} timeframes")
                except Exception as e:
                    print(f"[brain] OHLC error: {e}")

            elif future is future_charts:
                try:
                    chart_result = future.result()
                    chart_observations = chart_result.get("chart_analysis", "") or ""
                    print(f"[brain] Charts done — {len(chart_observations)} chars vision text")
                except Exception as e:
                    print(f"[brain] Chart error: {e}")
                    chart_observations = {"error": str(e)}

            elif future is future_market:
                try:
                    market_context = future.result()
                    market_from_cache = "_db_created_at" in market_context
                    print(f"[brain] Market data done ({'from cache' if market_from_cache else 'fresh fetch'})")
                except Exception as e:
                    print(f"[brain] Market data error: {e}")

    # ------------------------------------------------------------------
    # Step 2: Assemble full context (user prompt)
    # ------------------------------------------------------------------
    print("\n[brain] Step 2: Assembling decision context...")

    account_ctx = get_account_context_for_analysis(magic_number)
    context_parts: List[str] = []
    context_parts.extend(_user_run_instructions("intraday", "intraday_analysis", fill_event=fill_event))
    context_parts.extend(_strategy_mandate_lines(scoped_strategy))
    context_parts.extend(_context_section_header())
    _append_run_metadata_intraday(context_parts, magic_number, symbol, scoped_strategy)
    _append_market_context(context_parts, market_context)
    _append_analysis_and_positions_intraday(
        context_parts, sod_text, last_intraday, db_positions, fill_event=fill_event
    )
    _append_ohlc_context(context_parts, processed_ohlc)
    _append_chart_context(context_parts, chart_observations)
    _append_account_context(context_parts, account_ctx)

    full_context = "\n".join(context_parts)

    # ------------------------------------------------------------------
    # Step 3: Trading decision — gpt-5.4-mini with full assembled context
    # ------------------------------------------------------------------
    print("\n[brain] Step 3: Sending to gpt-5.4-mini for intraday trading decision...")
    try:
        intraday_prompt = compose_intraday_prompt(strategy_prompt_text)
        response_text = call_gpt_mini(
            system_prompt=intraday_prompt,
            user_prompt=full_context,
            temperature=0.2,
        )
        print(f"[brain] gpt-5.4-mini response received ({len(response_text)} chars)")

        parsed = _parse_gpt_response(response_text)
        result = _normalize_trading_response(parsed, "intraday_analysis", symbol, fill_event=fill_event)

        print("[brain] Saving intraday run JSON to analysis_notes...")
        try:
            _persist_trading_run(
                magic_number,
                symbol,
                scoped_strategy,
                "intraday_analysis",
                _build_run_record_json(result, "intraday_analysis"),
            )
            print("[brain] intraday_analysis saved")
        except Exception as e:
            print(f"[brain] Error saving intraday to database: {e}")

        result["intraday_metadata"] = {
            "magic_number": magic_number,
            "symbol": symbol,
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "timeframes_analyzed": timeframes,
            "sod_context_used": sod_text is not None,
            "last_intraday_context_used": last_intraday is not None,
            "market_data_from_cache": market_from_cache,
            "strategy_used": scoped_strategy,
        }

        print("\n" + "=" * 60)
        print("Intraday Analysis complete")
        print("=" * 60)
        flat = _flatten_for_ea(result, "intraday_analysis", magic_number)
        flat["intraday_metadata"] = result["intraday_metadata"]
        _record_test_run(
            magic_number=magic_number,
            run_type="intraday",
            symbol=symbol,
            strategy_name=scoped_strategy,
            macro=market_context,
            ohlc=processed_ohlc,
            chart=chart_observations,
            system_prompt=intraday_prompt,
            flat_output=flat,
            raw_gpt_response=response_text,
        )
        return flat

    except Exception as e:
        print(f"[brain] GPT decision call failed: {e}")
        return {
            "error": f"Failed to complete intraday analysis: {str(e)}",
            "symbol": symbol,
            "analysis_timestamp": datetime.now(timezone.utc).isoformat()
        }


def _parse_gpt_response(response_text: str) -> Dict[str, Any]:
    """Parse JSON from GPT response."""
    try:
        # Extract JSON from response if wrapped in markdown
        if "```json" in response_text:
            json_start = response_text.find("```json") + 7
            json_end = response_text.find("```", json_start)
            json_str = response_text[json_start:json_end].strip()
        elif "```" in response_text:
            json_start = response_text.find("```") + 3
            json_end = response_text.find("```", json_start)
            json_str = response_text[json_start:json_end].strip()
        else:
            json_str = response_text.strip()
        
        # Try to find JSON object
        import re
        json_match = re.search(r'\{.*\}', json_str, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        
        # If no match, try parsing the whole string
        return json.loads(json_str)
        
    except json.JSONDecodeError as e:
        print(f"Error parsing GPT response: {e}")
        print(f"Response text: {response_text[:500]}")
        # Return safe default
        return {
            "error": "Failed to parse AI response",
            "raw_response": response_text[:500]
        }
