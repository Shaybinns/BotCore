"""
Brain - Core Trading Logic

Orchestrates the trading decision-making process by coordinating
OHLC analysis, chart analysis, market data, and AI reasoning.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional

from ohlc_analyzer import analyze_ohlc_data
from chart_analyzer import analyze_charts_with_gpt_vision
from market_data import get_market_data
from prompt import compose_sod_prompt, compose_intraday_prompt
from llm_model import call_gpt
from database import (
    get_analysis_record,
    get_market_data_cache,
    save_market_data_cache,
    save_sod_analysis,
    save_intraday_analysis,
    save_bot_action,
    get_bot_action,
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


def _strategy_mandate_lines(strategy_name: Optional[str]) -> List[str]:
    """Opening lines for the trading user prompt: bind decisions to the system strategy block."""
    name = (strategy_name or "").strip()
    name_line = f"Strategy name for this run: {name}" if name else "Strategy name for this run: (none — follow any ACTIVE TRADING STRATEGY in the system message.)"
    return [
        "=== STRATEGY MANDATE (READ FIRST) ===",
        "All analysis and trading decisions in this response must follow the ACTIVE TRADING STRATEGY in your "
        "system message: setup rules, invalidation, risk, session logic, and CHECK vs ENTER / MANAGE / EXIT. "
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


def _chart_timeframes_from_ohlc_keys(ohlc_data: Dict[str, Any]) -> List[str]:
    """Map EA OHLC keys (e.g. M5_DATA, H1_DATA) to chart-img timeframe codes."""
    key_to_tf = {
        "4H_DATA": "H4", "H4_DATA": "H4",
        "1D_DATA": "D1", "D1_DATA": "D1",
        "H1_DATA": "H1", "1H_DATA": "H1",
        "M15_DATA": "M15", "M5_DATA": "M5", "M1_DATA": "M1",
        "M30_DATA": "M30", "W1_DATA": "W1", "1W_DATA": "W1",
    }
    out: List[str] = []
    for key in list(ohlc_data.keys())[:4]:
        tf = key_to_tf.get(key.upper()) or key.upper().replace("_DATA", "")
        if tf == "4H":
            tf = "H4"
        if tf and tf not in out:
            out.append(tf)
    return out


def _chart_block_for_context(chart_observations: Any) -> str:
    """Plain-text Vision output, or JSON for error dicts / legacy dict payloads."""
    if isinstance(chart_observations, str):
        return chart_observations
    return json.dumps(chart_observations, indent=2)


def _user_run_instructions(run: str, analysis_key: str) -> List[str]:
    """
    Opening block for user_prompt: task instructions first, then context is appended after.
    run: "sod" | "intraday"
    """
    if run == "sod":
        analysis_line = (
            "A 3–5 sentence, comprehensive Start of Day analysis that sets the bias "
            "and trading plan for the full day ahead."
        )
    else:
        analysis_line = (
            "A 3–5 sentence intraday analysis. Sentence 1 MUST thread from context: "
            "if LAST INTRADAY ANALYSIS is present, coupled with the SOD plan, state whether you continue, adjust, or "
            "invalidate that view and what changed; if only SOD is present (first intraday), "
            "state how you are carrying the SOD plan into this check. Then: current read of the markets, "
            "your trading today per the strategy, what you watch, what would change your view and ofcourse if you are placing any trades or managing positions."
        )

    return [
        "=== YOUR TASK (READ FIRST) ===",
        "You must now run your analysis using all context information provided below.",
        "You must output valid JSON only with exactly four top-level fields:",
        f'1. "{analysis_key}" — {analysis_line}',
        "2. next_review_time — when you will analyse the market again (London local time, no Z); must match what the strategy needs you to see next.",
        '3. monitoring_timeframes — JSON array of MT5 codes for intraday OHLC/charts (e.g. ["M5", "H1"]); only timeframes your strategy requires; state in analysis what you look for on each.',
        "4. executions — execution details (if any) when placing or managing a trade:",
        '   { "action_type": "ENTER" | "MANAGE" | "EXIT" | null, "enter": {...}, "manage": {...}, "exit": {...} }',
        "   When action_type is null, omit enter/manage/exit sub-objects.",
        "",
        "Ensure you are following your ACTIVE TRADING STRATEGY (system message). "
        "Your analysis, monitoring_timeframes, next_review_time, and executions must all align with the strategy and with each other.",
        "Capital preservation comes first — but do not hesitate to place a trade precisely "
        "when conditions fully adhere to your strategy.",
        "",
        "=== CONTEXT (USE FOR YOUR ANALYSIS) ===",
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
) -> Dict[str, Any]:
    """
    Map model executions (+ legacy shapes) to the canonical bot_action object for the EA.
    """
    executions = parsed.get("executions")
    if not isinstance(executions, dict):
        legacy = (parsed.get("decision") or {}).get("action") or parsed.get("action")
        executions = {"action_type": legacy}

    action_type = _normalize_action_type(executions.get("action_type"))

    raw_next = (
        parsed.get("next_review_time")
        or (parsed.get("decision") or {}).get("next_review_time")
        or executions.get("next_review_time")
    )
    next_review_time = _parse_next_review_time(raw_next)

    enter = manage = exit_payload = None

    if action_type == "ENTER":
        src = executions.get("enter") if isinstance(executions.get("enter"), dict) else executions
        legacy = parsed.get("enter_order") or {}
        direction = (
            src.get("direction")
            or src.get("order_type")
            or legacy.get("order_type")
            or ""
        )
        direction = str(direction).strip().upper()
        if direction not in ("BUY", "SELL"):
            direction = "BUY" if direction.startswith("B") else "SELL" if direction.startswith("S") else ""
        enter = {
            "symbol": (src.get("symbol") or legacy.get("asset") or symbol or "").upper(),
            "direction": direction,
            "entry_price": _float_or_none(src.get("entry_price") if "entry_price" in src else legacy.get("entry_price")),
            "stop_loss": _float_or_none(src.get("stop_loss") if "stop_loss" in src else legacy.get("stop_loss")),
            "take_profit": _float_or_none(src.get("take_profit") if "take_profit" in src else legacy.get("take_profit")),
            "risk_percentage": _float_or_none(src.get("risk_percentage") or legacy.get("risk_percentage")),
        }

    elif action_type == "MANAGE":
        src = executions.get("manage") if isinstance(executions.get("manage"), dict) else executions
        legacy = parsed.get("manage_order") or {}
        trade_id = src.get("trade_id") or src.get("ticket") or legacy.get("ticket")
        manage = {
            "trade_id": int(trade_id) if trade_id is not None else None,
            "new_stop_loss": _float_or_none(
                src.get("new_stop_loss") or src.get("update_stop_loss") or legacy.get("update_stop_loss")
            ),
            "new_take_profit": _float_or_none(
                src.get("new_take_profit") or src.get("update_take_profit") or legacy.get("update_take_profit")
            ),
            "new_position_percentage": _float_or_none(
                src.get("new_position_percentage")
                or src.get("partial_close_percentage")
                or legacy.get("partial_close_percentage")
            ),
        }

    elif action_type == "EXIT":
        src = executions.get("exit") if isinstance(executions.get("exit"), dict) else executions
        legacy = parsed.get("exit_order") or {}
        trade_id = src.get("trade_id") or src.get("ticket") or legacy.get("ticket")
        exit_payload = {
            "trade_id": int(trade_id) if trade_id is not None else None,
        }

    return {
        "next_review_time": next_review_time,
        "action_type": action_type,
        "enter": enter,
        "manage": manage,
        "exit": exit_payload,
    }


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


def _bot_action_for_api(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Strip DB metadata keys for EA/API consumption."""
    if not row:
        return {
            "next_review_time": None,
            "action_type": None,
            "enter": None,
            "manage": None,
            "exit": None,
        }
    return {
        "next_review_time": row.get("next_review_time"),
        "action_type": row.get("action_type"),
        "enter": row.get("enter"),
        "manage": row.get("manage"),
        "exit": row.get("exit"),
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
    analysis_text: str,
    bot_action: Dict[str, Any],
) -> Dict[str, Any]:
    """Save analysis_notes + bot_action; return canonical bot_action for the EA."""
    if analysis_key == "sod_analysis":
        save_sod_analysis(magic_number, symbol, strategy_name, analysis_text)
    else:
        save_intraday_analysis(magic_number, symbol, strategy_name, analysis_text)

    save_bot_action(
        magic_number,
        next_review_time=bot_action.get("next_review_time"),
        action_type=bot_action.get("action_type"),
        enter=bot_action.get("enter"),
        manage=bot_action.get("manage"),
        exit=bot_action.get("exit"),
    )
    saved = get_bot_action(magic_number)
    return _bot_action_for_api(saved)


def _normalize_trading_response(
    parsed: Dict[str, Any],
    analysis_key: str,
    symbol: str,
) -> Dict[str, Any]:
    """Map model output to analysis text + canonical bot_action for the EA."""
    analysis_text = parsed.get(analysis_key)
    if analysis_text is None:
        decision = parsed.get("decision") or {}
        analysis_text = (
            decision.get("summary")
            or decision.get("explanation")
            or parsed.get("sod_analysis")
            or parsed.get("intraday_analysis")
            or ""
        )
    if isinstance(analysis_text, dict):
        analysis_text = json.dumps(analysis_text)
    analysis_text = str(analysis_text).strip()

    bot_action = _build_bot_action_payload(parsed, symbol)

    mtf_raw = (
        parsed.get("monitoring_timeframes")
        or (parsed.get("decision") or {}).get("monitoring_timeframes")
    )

    return {
        analysis_key: analysis_text,
        "bot_action": bot_action,
        "monitoring_timeframes": _parse_monitoring_timeframes(mtf_raw),
    }


def _append_account_context(context_parts: list, account_ctx: Dict[str, Any]) -> None:
    """Append latest account snapshot (one row per magic_number + symbol + strategy)."""
    has_values = any(
        account_ctx.get(k) is not None
        for k in (
            "account_size", "realised_pnl", "unrealised_pnl",
            "today_realised_pnl", "week_pnl", "month_pnl",
        )
    )
    lines = ["=== ACCOUNT (latest snapshot) ==="]
    if account_ctx.get("snapshot_at"):
        lines.append(f"snapshot_at: {account_ctx['snapshot_at']}")
    if account_ctx.get("symbol"):
        lines.append(f"last_reported_symbol: {account_ctx['symbol']}")
    if account_ctx.get("strategy_name"):
        lines.append(f"last_reported_strategy: {account_ctx['strategy_name']}")
    if not has_values:
        lines.append("No account snapshot on file for this magic_number.")
        lines.append("")
        context_parts.extend(lines)
        return
    lines.extend([
        "account_size: " + (str(account_ctx.get("account_size")) if account_ctx.get("account_size") is not None else "—"),
        "realised_pnl: " + (str(account_ctx.get("realised_pnl")) if account_ctx.get("realised_pnl") is not None else "—"),
        "today_realised_pnl: " + (str(account_ctx.get("today_realised_pnl")) if account_ctx.get("today_realised_pnl") is not None else "—"),
        "unrealised_pnl: " + (str(account_ctx.get("unrealised_pnl")) if account_ctx.get("unrealised_pnl") is not None else "—"),
        "week_pnl: " + (str(account_ctx.get("week_pnl")) if account_ctx.get("week_pnl") is not None else "—"),
        "month_pnl: " + (str(account_ctx.get("month_pnl")) if account_ctx.get("month_pnl") is not None else "—"),
        "",
    ])
    context_parts.extend(lines)


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
    positions: List[Dict[str, Any]] = None,  # DEPRECATED - now retrieved from DB
    strategy_name: str = None,
) -> Dict[str, Any]:
    """
    Start of Day (SOD) action - comprehensive daily market analysis.

    Workflow:
      0. Load DB positions + previous run note + strategy (if specified)
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
        positions:     DEPRECATED - positions now retrieved from database
        strategy_name: Name of the strategy to load from DB (optional)

    Returns:
        Comprehensive SOD analysis as JSON dictionary
    """
    print("=" * 60)
    print(f"SOD Analysis starting: {symbol} (magic {magic_number})")
    print("=" * 60)

    scoped_strategy = strategy_name or ''

    # ------------------------------------------------------------------
    # Step 0: DB reads (fast, sequential — no API calls)
    # ------------------------------------------------------------------
    print("\n[brain] Loading DB context...")
    db_positions = get_current_positions(symbol, magic_number)
    analysis_record = get_analysis_record(magic_number, symbol, scoped_strategy)
    previous_intraday = (
        analysis_record.get("intraday_analysis") if analysis_record else None
    )
    print(f"[brain] Positions: {len(db_positions)} open" if db_positions else "[brain] No open positions")
    print("[brain] Previous intraday note found" if previous_intraday else "[brain] No previous intraday note")

    strategy_prompt_text = None
    if strategy_name:
        strategy = get_strategy(strategy_name)
        if strategy:
            strategy_prompt_text = strategy["strategy_prompt"]
            print(f"[brain] Strategy loaded: '{strategy_name}'")
        else:
            print(f"[brain] WARNING: Strategy '{strategy_name}' not found in DB — proceeding without it")

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
    # Step 2: Assemble full context
    # ------------------------------------------------------------------
    print("\n[brain] Step 2: Assembling decision context...")

    context_parts = _user_run_instructions("sod", "sod_analysis")
    context_parts.extend(_strategy_mandate_lines(strategy_name))
    context_parts.extend([
        f"MAGIC_NUMBER: {magic_number}",
        f"SYMBOL: {symbol}",
        f"STRATEGY: {scoped_strategy or '(none)'}",
        f"ANALYSIS DATE: {datetime.now(timezone.utc).isoformat()}",
        f"CURRENT TIME (London): {_london_time_str()}",
        "NOTE: All OHLC candle timestamps and chart images are in London local time.",
        "      next_review_time must be output as London local time (format: YYYY-MM-DDTHH:MM:SS, no Z).",
        "",
        "=== SYSTEM INFO ===",
        "SOD (Start of Day) analysis — runs automatically at 07:00 London time every morning.",
        "Use this analysis to set your bias and trading plan for the full day ahead.",
        ""
    ])

    if previous_intraday:
        context_parts.extend([
            "=== PREVIOUS INTRADAY ANALYSIS (before today's SOD) ===",
            previous_intraday,
            ""
        ])

    if db_positions:
        context_parts.extend([
            "=== CURRENT OPEN POSITIONS (From Database) ===",
            json.dumps(db_positions, indent=2),
            ""
        ])

    if positions:
        context_parts.extend([
            "=== EA REPORTED POSITIONS (For Validation) ===",
            json.dumps(positions, indent=2),
            ""
        ])

    # Account context (latest snapshot for this magic_number)
    account_ctx = get_account_context_for_analysis(magic_number)
    _append_account_context(context_parts, account_ctx)

    context_parts.extend([
        "=== OHLC DATA ANALYSIS ===",
        json.dumps(processed_ohlc, indent=2),
        "",
        "=== CHART VISUAL ANALYSIS (GPT Vision — pure visual observations) ===",
        _chart_block_for_context(chart_observations),
        "",
        "=== MARKET INTELLIGENCE (pre-synthesized: regime, risk profile, forex outlook, catalysts) ===",
        json.dumps(market_context, indent=2)
    ])

    full_context = "\n".join(context_parts)

    # ------------------------------------------------------------------
    # Step 3: Trading decision — GPT-4o-mini with full assembled context
    # ------------------------------------------------------------------
    print("\n[brain] Step 3: Sending to GPT-4o-mini for SOD trading decision...")
    if strategy_name:
        print(f"[brain] Using strategy: '{strategy_name}'" if strategy_prompt_text else f"[brain] Strategy '{strategy_name}' unavailable — no strategy section in prompt")
    try:
        sod_prompt    = compose_sod_prompt(strategy_prompt_text)
        response_text = call_gpt(
            system_prompt=sod_prompt,
            user_prompt=full_context,
            model="gpt-4o-mini",
        )
        print(f"[brain] GPT-4o-mini response received ({len(response_text)} chars)")

        parsed = _parse_gpt_response(response_text)
        result = _normalize_trading_response(parsed, "sod_analysis", symbol)

        print("[brain] Saving SOD analysis + bot_action to database...")
        try:
            result["bot_action"] = _persist_trading_run(
                magic_number,
                symbol,
                scoped_strategy,
                "sod_analysis",
                result.get("sod_analysis") or "",
                result["bot_action"],
            )
            print("[brain] sod_analysis + bot_action saved")
        except Exception as e:
            print(f"[brain] Error saving SOD to database: {e}")

        result["sod_metadata"] = {
            "magic_number": magic_number,
            "symbol": symbol,
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "ohlc_timeframes_analyzed": list(ohlc_data.keys()),
            "chart_timeframes_analyzed": timeframes,
            "previous_intraday_context_used": previous_intraday is not None,
            "strategy_used": strategy_name if strategy_prompt_text else None,
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
    positions: List[Dict[str, Any]] = None,  # DEPRECATED - now retrieved from DB
    strategy_name: str = None,
) -> Dict[str, Any]:
    """
    Intraday action - active trading analysis during the day.

    Workflow:
      0. Load DB positions + SOD note + last run note + strategy (if specified)
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
        positions:     DEPRECATED - positions now retrieved from database
        strategy_name: Name of the strategy to load from DB (optional)

    Returns:
        Intraday trading decision as JSON dictionary
    """
    print("=" * 60)
    print(f"Intraday Analysis starting: {symbol} (magic {magic_number})")
    print("=" * 60)

    scoped_strategy = strategy_name or ''

    # ------------------------------------------------------------------
    # Step 0: DB reads (fast, sequential — no API calls)
    # ------------------------------------------------------------------
    print("\n[brain] Loading DB context...")
    db_positions = get_current_positions(symbol, magic_number)
    analysis_record = get_analysis_record(magic_number, symbol, scoped_strategy)
    sod_text = analysis_record.get("sod_analysis") if analysis_record else None
    last_intraday = analysis_record.get("intraday_analysis") if analysis_record else None
    print(f"[brain] Positions: {len(db_positions)} open" if db_positions else "[brain] No open positions")
    print(f"[brain] SOD analysis: {'found' if sod_text else 'missing'} | Prior intraday: {'found' if last_intraday else 'missing'}")

    strategy_prompt_text = None
    if strategy_name:
        strategy = get_strategy(strategy_name)
        if strategy:
            strategy_prompt_text = strategy["strategy_prompt"]
            print(f"[brain] Strategy loaded: '{strategy_name}'")
        else:
            print(f"[brain] WARNING: Strategy '{strategy_name}' not found in DB — proceeding without it")

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
    # Step 2: Assemble full context
    # ------------------------------------------------------------------
    print("\n[brain] Step 2: Assembling decision context...")

    context_parts = _user_run_instructions("intraday", "intraday_analysis")
    context_parts.extend(_strategy_mandate_lines(strategy_name))
    context_parts.extend([
        f"MAGIC_NUMBER: {magic_number}",
        f"SYMBOL: {symbol}",
        f"STRATEGY: {scoped_strategy or '(none)'}",
        f"ANALYSIS TIME: {datetime.now(timezone.utc).isoformat()}",
        f"CURRENT TIME (London): {_london_time_str()}",
        "NOTE: All OHLC candle timestamps and chart images are in London local time.",
        "      next_review_time must be output as London local time (format: YYYY-MM-DDTHH:MM:SS, no Z).",
        "",
        "=== SYSTEM INFO ===",
        "SOD (Start of Day) analysis runs automatically at 07:00 London time every morning.",
        "You are currently in an INTRADAY analysis run.",
        "Use the SOD note below to understand today's overall bias and trading plan.",
        ""
    ])

    if sod_text:
        context_parts.extend([
            "=== START OF DAY ANALYSIS (Today's SOD) ===",
            sod_text,
            ""
        ])

    if last_intraday:
        context_parts.extend([
            "=== LAST INTRADAY ANALYSIS (Previous Check) ===",
            last_intraday,
            ""
        ])

    if db_positions:
        context_parts.extend([
            "=== CURRENT OPEN POSITIONS (From Database) ===",
            json.dumps(db_positions, indent=2),
            ""
        ])

    if positions:
        context_parts.extend([
            "=== EA REPORTED POSITIONS (For Validation) ===",
            json.dumps(positions, indent=2),
            ""
        ])

    # Account context (latest snapshot for this magic_number)
    account_ctx = get_account_context_for_analysis(magic_number)
    _append_account_context(context_parts, account_ctx)

    context_parts.extend([
        "=== OHLC DATA ANALYSIS ===",
        json.dumps(processed_ohlc, indent=2),
        "",
        "=== CHART VISUAL ANALYSIS (GPT Vision — pure visual observations) ===",
        _chart_block_for_context(chart_observations),
        "",
        "=== MARKET INTELLIGENCE (pre-synthesized: regime, risk profile, forex outlook, catalysts) ===",
        json.dumps(market_context, indent=2)
    ])

    full_context = "\n".join(context_parts)

    # ------------------------------------------------------------------
    # Step 3: Trading decision — GPT-4o-mini with full assembled context
    # ------------------------------------------------------------------
    print("\n[brain] Step 3: Sending to GPT-4o-mini for intraday trading decision...")
    if strategy_name:
        print(f"[brain] Using strategy: '{strategy_name}'" if strategy_prompt_text else f"[brain] Strategy '{strategy_name}' unavailable — no strategy section in prompt")
    try:
        intraday_prompt = compose_intraday_prompt(strategy_prompt_text)
        response_text = call_gpt(
            system_prompt=intraday_prompt,
            user_prompt=full_context,
            model="gpt-4o-mini",
        )
        print(f"[brain] GPT-4o-mini response received ({len(response_text)} chars)")

        parsed = _parse_gpt_response(response_text)
        result = _normalize_trading_response(parsed, "intraday_analysis", symbol)

        print("[brain] Saving intraday analysis + bot_action to database...")
        try:
            result["bot_action"] = _persist_trading_run(
                magic_number,
                symbol,
                scoped_strategy,
                "intraday_analysis",
                result.get("intraday_analysis") or "",
                result["bot_action"],
            )
            print("[brain] intraday_analysis + bot_action saved")
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
            "strategy_used": strategy_name if strategy_prompt_text else None,
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
