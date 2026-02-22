"""
Brain - Core Trading Logic

Orchestrates the trading decision-making process by coordinating
OHLC analysis, chart analysis, market data, and AI reasoning.
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict, Any, List

from ohlc_analyzer import analyze_ohlc_data
from chart_analyzer import analyze_charts_with_gpt_vision
from market_data import get_market_data
from prompt import get_sod_prompt, get_intraday_prompt
from llm_model import call_gpt
from database import (
    get_analysis_note,
    save_analysis_note,
    clear_analysis_notes,
    get_current_positions
)

# Market data is cached in the DB as 'market_data_note'.
# Intraday runs re-use it if fresher than this threshold.
MARKET_DATA_CACHE_HOURS = 4


def _get_market_data_cached(symbol: str) -> Dict[str, Any]:
    """
    Load market data from DB cache (market_data_note) if fresh.
    Re-fetches from APIs only if the cache is missing or older than MARKET_DATA_CACHE_HOURS.
    Used by intraday_action inside the parallel executor.
    """
    cached = get_analysis_note(symbol, 'market_data_note')
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
        print("[market] No market_data_note in DB — fetching fresh...")

    data = get_market_data(symbol)
    save_analysis_note(symbol, 'market_data_note', data)
    print("[market] Market data refreshed and saved to DB")
    return data


def sod_action(
    symbol: str,
    ohlc_data: Dict[str, List[Dict[str, Any]]],
    positions: List[Dict[str, Any]] = None  # DEPRECATED - now retrieved from DB
) -> Dict[str, Any]:
    """
    Start of Day (SOD) action - comprehensive daily market analysis.

    Workflow:
      0. Load DB positions + previous run note
      1. OHLC analysis
      2. Market data  — fetched fresh every SOD and saved to DB (market_data_note)
      3. Chart visual analysis — GPT Vision sees only the chart images, no context
      4. Trading decision  — GPT-4o receives full context including chart observations
      5. Persist result to DB (sod_note), clear yesterday's last_run_note

    Args:
        symbol:    Trading symbol (e.g. "GBPUSD")
        ohlc_data: {timeframe_key: [candle dicts], ...}
        positions: DEPRECATED - positions now retrieved from database

    Returns:
        Comprehensive SOD analysis as JSON dictionary
    """
    print("=" * 60)
    print(f"SOD Analysis starting: {symbol}")
    print("=" * 60)

    # ------------------------------------------------------------------
    # Step 0: DB reads (fast, sequential — no API calls)
    # ------------------------------------------------------------------
    print("\n[brain] Loading DB context...")
    db_positions = get_current_positions(symbol)
    previous_run = get_analysis_note(symbol, 'last_run_note')
    print(f"[brain] Positions: {len(db_positions)} open" if db_positions else "[brain] No open positions")
    print("[brain] Previous run note found" if previous_run else "[brain] No previous run note")

    # ------------------------------------------------------------------
    # Step 1: Parallel data collection
    #   - OHLC analysis       (CPU-light, fast)
    #   - Chart visual analysis (chart-img.com fetches + GPT Vision)
    #   - Market data           (RapidAPI + 2x Perplexity — fresh every SOD)
    # All three are independent. They converge in Step 2.
    # ------------------------------------------------------------------
    timeframes = ["H1", "H4", "D1", "W1"]

    def _fetch_market_data_sod():
        data = get_market_data(symbol)
        save_analysis_note(symbol, 'market_data_note', data)
        print("[market] Market data fetched and saved to DB")
        return data

    def _run_ohlc():
        return analyze_ohlc_data(ohlc_data)

    def _run_charts():
        return analyze_charts_with_gpt_vision(symbol=symbol, timeframes=timeframes)

    print("\n[brain] Step 1: Collecting data in parallel (OHLC + Charts + Market)...")

    processed_ohlc    = {}
    chart_observations = {}
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
                    chart_observations = chart_result.get("chart_analysis", {})
                    print(f"[brain] Charts done — {len(chart_observations)} timeframe(s)")
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

    context_parts = [
        f"SYMBOL: {symbol}",
        f"ANALYSIS DATE: {datetime.now(timezone.utc).isoformat()}",
        f"CURRENT TIME: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        "=== SYSTEM INFO ===",
        "SOD (Start of Day) analysis — runs automatically at 07:00 UTC every morning.",
        "Use this analysis to set your bias and trading plan for the full day ahead.",
        ""
    ]

    if previous_run:
        context_parts.extend([
            "=== PREVIOUS RUN CONTEXT (Last Analysis) ===",
            json.dumps(previous_run, indent=2),
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

    context_parts.extend([
        "=== OHLC DATA ANALYSIS ===",
        json.dumps(processed_ohlc, indent=2),
        "",
        "=== CHART VISUAL ANALYSIS (GPT Vision — pure visual observations) ===",
        json.dumps(chart_observations, indent=2),
        "",
        "=== MARKET DATA (News, Macro, Economic Calendar) ===",
        json.dumps(market_context, indent=2)
    ])

    full_context = "\n".join(context_parts)

    # ------------------------------------------------------------------
    # Step 3: Trading decision — GPT-4o with full assembled context
    # ------------------------------------------------------------------
    print("\n[brain] Step 3: Sending to GPT-4o for SOD trading decision...")
    try:
        sod_prompt    = get_sod_prompt()
        response_text = call_gpt(system_prompt=sod_prompt, user_prompt=full_context)
        print(f"[brain] GPT-4o response received ({len(response_text)} chars)")

        result = _parse_gpt_response(response_text)

        print("[brain] Saving SOD analysis to database...")
        try:
            save_analysis_note(symbol, 'sod_note', result)
            print("[brain] sod_note saved")
        except Exception as e:
            print(f"[brain] Error saving sod_note: {e}")

        print("[brain] Clearing last_run_note...")
        try:
            clear_analysis_notes(symbol, ['last_run_note'])
            print("[brain] last_run_note cleared")
        except Exception as e:
            print(f"[brain] Error clearing last_run_note: {e}")

        result["sod_metadata"] = {
            "symbol": symbol,
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "ohlc_timeframes_analyzed": list(ohlc_data.keys()),
            "chart_timeframes_analyzed": timeframes,
            "previous_run_context_used": previous_run is not None
        }

        print("\n" + "=" * 60)
        print("SOD Analysis complete")
        print("=" * 60)
        return result

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
    positions: List[Dict[str, Any]] = None  # DEPRECATED - now retrieved from DB
) -> Dict[str, Any]:
    """
    Intraday action - active trading analysis during the day.

    Workflow:
      0. Load DB positions + SOD note + last run note
      1. OHLC analysis
      2. Market data  — loaded from DB cache (market_data_note saved by SOD).
                        Re-fetched only if cache is missing or older than MARKET_DATA_CACHE_HOURS.
      3. Chart visual analysis — GPT Vision sees only the chart images, no context
      4. Trading decision  — GPT-4o receives full context including chart observations
      5. Persist result to DB (last_run_note)

    Args:
        symbol:    Trading symbol (e.g. "GBPUSD")
        ohlc_data: {timeframe_key: [candle dicts], ...}
        positions: DEPRECATED - positions now retrieved from database

    Returns:
        Intraday trading decision as JSON dictionary
    """
    print("=" * 60)
    print(f"Intraday Analysis starting: {symbol}")
    print("=" * 60)

    # ------------------------------------------------------------------
    # Step 0: DB reads (fast, sequential — no API calls)
    # ------------------------------------------------------------------
    print("\n[brain] Loading DB context...")
    db_positions  = get_current_positions(symbol)
    sod_note      = get_analysis_note(symbol, 'sod_note')
    last_run_note = get_analysis_note(symbol, 'last_run_note')
    print(f"[brain] Positions: {len(db_positions)} open" if db_positions else "[brain] No open positions")
    print(f"[brain] SOD note: {'found' if sod_note else 'missing'} | Last run: {'found' if last_run_note else 'missing'}")

    timeframes = list(ohlc_data.keys())[:4] or ["H1", "M15"]

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
    chart_observations = {}
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
                    chart_observations = chart_result.get("chart_analysis", {})
                    print(f"[brain] Charts done — {len(chart_observations)} timeframe(s)")
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

    context_parts = [
        f"SYMBOL: {symbol}",
        f"ANALYSIS TIME: {datetime.now(timezone.utc).isoformat()}",
        f"CURRENT TIME: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        "=== SYSTEM INFO ===",
        "SOD (Start of Day) analysis runs automatically at 07:00 UTC every morning.",
        "You are currently in an INTRADAY analysis run.",
        "Use the SOD note below to understand today's overall bias and trading plan.",
        ""
    ]

    if sod_note:
        context_parts.extend([
            "=== START OF DAY ANALYSIS (Today's SOD) ===",
            json.dumps(sod_note, indent=2),
            ""
        ])

    if last_run_note:
        context_parts.extend([
            "=== LAST RUN ANALYSIS (Previous Intraday Check) ===",
            json.dumps(last_run_note, indent=2),
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

    context_parts.extend([
        "=== OHLC DATA ANALYSIS ===",
        json.dumps(processed_ohlc, indent=2),
        "",
        "=== CHART VISUAL ANALYSIS (GPT Vision — pure visual observations) ===",
        json.dumps(chart_observations, indent=2),
        "",
        "=== MARKET DATA (News, Macro, Economic Calendar) ===",
        json.dumps(market_context, indent=2)
    ])

    full_context = "\n".join(context_parts)

    # ------------------------------------------------------------------
    # Step 3: Trading decision — GPT-4o with full assembled context
    # ------------------------------------------------------------------
    print("\n[brain] Step 3: Sending to GPT-4o for intraday trading decision...")
    try:
        intraday_prompt = get_intraday_prompt()
        response_text   = call_gpt(system_prompt=intraday_prompt, user_prompt=full_context)
        print(f"[brain] GPT-4o response received ({len(response_text)} chars)")

        result = _parse_gpt_response(response_text)

        print("[brain] Saving intraday analysis to database...")
        try:
            save_analysis_note(symbol, 'last_run_note', result)
            print("[brain] last_run_note saved")
        except Exception as e:
            print(f"[brain] Error saving last_run_note: {e}")

        result["intraday_metadata"] = {
            "symbol": symbol,
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "timeframes_analyzed": timeframes,
            "sod_context_used": sod_note is not None,
            "last_run_context_used": last_run_note is not None,
            "market_data_from_cache": market_from_cache
        }

        print("\n" + "=" * 60)
        print("Intraday Analysis complete")
        print("=" * 60)
        return result

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
