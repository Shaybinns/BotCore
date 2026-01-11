"""
BotCore Brain - AI Trading Decision Orchestrator

This module orchestrates the full trading decision process:
1. Receives OHLC data and context from MT5 EA via API
2. Retrieves locked levels from database
3. Fetches chart images when needed (chart-img.com)
4. Gets market data context
5. Calls GPT with vision for chart analysis
6. Analyzes OHLC data for FVG/BOS/imbalances
7. Makes trading decisions (WAIT/WATCH/ENTER/MANAGE/EXIT)
8. Saves locked levels and setup states to database
"""

import os
import json
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from dotenv import load_dotenv

from chart_service import get_chart_image
from market_data import get_market_data
from ohlc_analyzer import analyze_ohlc_data
from gpt_vision import analyze_chart_with_gpt
from database import (
    get_locked_levels,
    save_locked_levels,
    get_active_setup,
    save_setup_state,
    save_trade_event
)
from llm_model import call_gpt
from prompt import get_trading_prompt

load_dotenv()


def process_trading_snapshot(
    symbol: str,
    ohlc_data: Dict[str, Any],
    account_state: Dict[str, Any],
    session_context: Optional[str] = None,
    requested_timeframes: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Main entry point for trading decision processing.
    
    Called by API server when MT5 EA requests analysis.
    
    Args:
        symbol: Trading symbol (e.g., "EURUSD")
        ohlc_data: OHLC data from MT5 (multiple timeframes)
        account_state: Account info from MT5 (balance, positions, etc.)
        session_context: Optional session identifier (London/NY/etc.)
        requested_timeframes: Optional list of timeframes that were requested
    
    Returns:
        Trading decision JSON with action, levels, order_intent, next_requested_timeframes, etc.
    """
    try:
        # Detect if this is a start-of-day request
        is_start_of_day = _is_start_of_day_request(requested_timeframes, ohlc_data)
        
        if is_start_of_day:
            print(f"[Brain] ===== START OF DAY REQUEST FOR {symbol} =====")
            print(f"[Brain] Timeframes received: {list(ohlc_data.keys())}")
        
        # Step 1: Get locked levels from database
        locked_levels = get_locked_levels(symbol, session_context)
        print(f"[Brain] Retrieved {len(locked_levels)} locked levels from database")
        
        # Step 2: Get market data context (always fetch for context)
        print(f"[Brain] Fetching market data for {symbol}...")
        market_context = get_market_data(symbol)
        print(f"[Brain] Market data retrieved: {len(market_context)} fields")
        
        # Step 3: Analyze OHLC data for patterns
        print(f"[Brain] Analyzing OHLC data for patterns...")
        ohlc_analysis = analyze_ohlc_data(ohlc_data, locked_levels)
        print(f"[Brain] OHLC analysis complete: {len(ohlc_analysis.get('fvgs', []))} FVGs, {len(ohlc_analysis.get('bos_signals', []))} BOS signals")
        
        # Step 4: Fetch chart image(s)
        # For start-of-day, always fetch chart images for context building
        # For other requests, fetch if needed
        chart_images = _fetch_chart_images(
            symbol=symbol,
            ohlc_data=ohlc_data,
            is_start_of_day=is_start_of_day,
            locked_levels=locked_levels,
            account_state=account_state,
            session_context=session_context
        )
        
        # Step 5: Build context for AI
        print(f"[Brain] Building AI context...")
        ai_context = _build_ai_context(
            symbol=symbol,
            ohlc_data=ohlc_data,
            ohlc_analysis=ohlc_analysis,
            locked_levels=locked_levels,
            account_state=account_state,
            market_context=market_context,
            chart_images=chart_images,
            session_context=session_context,
            is_start_of_day=is_start_of_day
        )
        
        # Step 6: Call GPT for trading decision
        print(f"[Brain] Calling GPT for trading decision...")
        if chart_images:
            # Use vision model for chart analysis
            # If multiple images, use the primary one (H1 or first available)
            primary_chart = chart_images.get("H1") or chart_images.get("H4") or (list(chart_images.values())[0] if chart_images else None)
            if primary_chart:
                print(f"[Brain] Using GPT Vision with chart image: {primary_chart[:50]}...")
                trading_decision = analyze_chart_with_gpt(ai_context, primary_chart)
            else:
                print(f"[Brain] Chart images available but no valid URL, using text-only")
                trading_decision = _call_gpt_text_only(ai_context)
        else:
            # Use text-only model
            print(f"[Brain] No chart images, using text-only GPT")
            trading_decision = _call_gpt_text_only(ai_context)
        
        print(f"[Brain] GPT decision received: action={trading_decision.get('action', 'UNKNOWN')}")
        
        # Step 8: Validate and process decision
        validated_decision = _validate_decision(trading_decision, account_state)
        
        # Step 9: Save state to database
        if validated_decision.get("levels_update"):
            save_locked_levels(
                symbol=symbol,
                levels=validated_decision["levels_update"],
                session=session_context
            )
        
        if validated_decision.get("setup_id"):
            save_setup_state(
                symbol=symbol,
                setup_id=validated_decision["setup_id"],
                state=validated_decision.get("state_update", {}),
                session=session_context
            )
        
        # Step 10: Determine next requested timeframes
        next_timeframes = _determine_next_timeframes(
            validated_decision,
            is_start_of_day,
            ohlc_data
        )
        validated_decision["next_requested_timeframes"] = next_timeframes
        
        # Step 11: Return clean JSON for EA
        return _format_ea_response(validated_decision)
        
    except Exception as e:
        return {
            "action": "ERROR",
            "error": str(e),
            "next_run_at_utc": _get_next_check_time(60)  # Retry in 1 minute
        }


def _fetch_chart_images(
    symbol: str,
    ohlc_data: Dict[str, Any],
    is_start_of_day: bool,
    locked_levels: List[Dict[str, Any]],
    account_state: Dict[str, Any],
    session_context: Optional[str]
) -> Dict[str, Optional[str]]:
    """
    Fetch chart images based on context.
    
    For start-of-day: Always fetch H1 and H4 charts for context building
    For other requests: Fetch if needed (no levels, hot zone, active setup)
    
    Returns:
        Dictionary mapping timeframe to chart image URL (e.g., {"H1": "url", "H4": "url"})
    """
    chart_images = {}
    
    if is_start_of_day:
        # Start of day: Always fetch H1 and H4 for context building
        print(f"[Start of Day] Fetching chart images for {symbol}...")
        
        h1_chart = get_chart_image(
            symbol=symbol,
            timeframe="H1",
            session=session_context
        )
        if h1_chart:
            chart_images["H1"] = h1_chart
            print(f"[Start of Day] H1 chart fetched successfully")
        
        h4_chart = get_chart_image(
            symbol=symbol,
            timeframe="H4",
            session=session_context
        )
        if h4_chart:
            chart_images["H4"] = h4_chart
            print(f"[Start of Day] H4 chart fetched successfully")
        
        return chart_images
    
    # For non-start-of-day requests, check if chart is needed
    needs_chart = False
    
    # No levels = need initial chart
    if not locked_levels:
        needs_chart = True
    
    # Check if price is near any locked level (hot zone)
    current_price = ohlc_data.get("current_price")
    if current_price and not needs_chart:
        for level in locked_levels:
            level_price = level.get("price")
            if level_price:
                distance_pips = abs(current_price - level_price) * 10000  # Rough pips
                if distance_pips < 50:  # Within 50 pips
                    needs_chart = True
                    break
    
    # Check for active setup
    if not needs_chart:
        active_positions = account_state.get("open_positions", [])
        if active_positions:
            needs_chart = True
    
    if needs_chart:
        # Fetch primary timeframe chart
        primary_tf = ohlc_data.get("primary_timeframe", "H1")
        chart_url = get_chart_image(
            symbol=symbol,
            timeframe=primary_tf,
            session=session_context
        )
        if chart_url:
            chart_images[primary_tf] = chart_url
    
    return chart_images


def _is_start_of_day_request(
    requested_timeframes: Optional[List[str]],
    ohlc_data: Dict[str, Any]
) -> bool:
    """
    Detect if this is a start-of-day request.
    
    Start of day is indicated by:
    - Requested timeframes are H1, H4, D1, W1 (in any order)
    - OR no locked levels exist (first run)
    """
    if not requested_timeframes:
        # Check if we have the start-of-day timeframes in ohlc_data
        has_h1 = "H1" in ohlc_data
        has_h4 = "H4" in ohlc_data
        has_d1 = "D1" in ohlc_data
        has_w1 = "W1" in ohlc_data
        
        if has_h1 and has_h4 and has_d1 and has_w1:
            return True
    
    if requested_timeframes:
        # Check if all start-of-day timeframes are present
        start_of_day_tfs = {"H1", "H4", "D1", "W1"}
        requested_set = set(requested_timeframes)
        
        if start_of_day_tfs.issubset(requested_set):
            return True
    
    return False


def _build_ai_context(
    symbol: str,
    ohlc_data: Dict[str, Any],
    ohlc_analysis: Dict[str, Any],
    locked_levels: List[Dict[str, Any]],
    account_state: Dict[str, Any],
    market_context: Dict[str, Any],
    chart_images: Dict[str, Optional[str]],
    session_context: Optional[str],
    is_start_of_day: bool = False
) -> str:
    """Build comprehensive context string for AI."""
    
    context_parts = [
        f"=== TRADING CONTEXT ===",
        f"Symbol: {symbol}",
        f"Session: {session_context or 'N/A'}",
        f"Current Time (UTC): {datetime.now(timezone.utc).isoformat()}",
        f"Start of Day: {is_start_of_day}",
        "",
        "=== ACCOUNT STATE ===",
        json.dumps(account_state, indent=2),
        "",
        "=== OHLC DATA ===",
        json.dumps(ohlc_data, indent=2),
        "",
        "=== OHLC ANALYSIS ===",
        json.dumps(ohlc_analysis, indent=2),
        "",
        "=== LOCKED LEVELS ===",
        json.dumps(locked_levels, indent=2),
        "",
        "=== MARKET CONTEXT ===",
        json.dumps(market_context, indent=2),
    ]
    
    # Add chart image information
    if chart_images:
        context_parts.extend([
            "",
            "=== CHART IMAGES ==="
        ])
        
        for timeframe, chart_url in chart_images.items():
            if chart_url:
                context_parts.append(f"{timeframe} Chart URL: {chart_url}")
        
        if is_start_of_day:
            context_parts.append("")
            context_parts.append("START OF DAY ANALYSIS:")
            context_parts.append("- Analyze the H1 and H4 chart images to identify obvious swing highs/lows")
            context_parts.append("- Look for clustering zones (multiple touches)")
            context_parts.append("- Map visual levels to exact OHLC prices from the data")
            context_parts.append("- Lock these levels for the session/day")
            context_parts.append("- Establish market bias and key narrative")
        else:
            context_parts.append("")
            context_parts.append("Analyze the chart image(s) for visual structure, obvious highs/lows, and market sentiment.")
    
    return "\n".join(context_parts)


def _call_gpt_text_only(context: str) -> Dict[str, Any]:
    """Call GPT with text-only context (no vision)."""
    system_prompt = get_trading_prompt()
    
    response = call_gpt(system_prompt, context)
    
    # Parse JSON response
    try:
        # Extract JSON from response if wrapped in markdown
        if "```json" in response:
            json_start = response.find("```json") + 7
            json_end = response.find("```", json_start)
            response = response[json_start:json_end].strip()
        elif "```" in response:
            json_start = response.find("```") + 3
            json_end = response.find("```", json_start)
            response = response[json_start:json_end].strip()
        
        return json.loads(response)
    except json.JSONDecodeError:
        # Fallback: try to extract JSON object
        import re
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        raise ValueError(f"Could not parse JSON from GPT response: {response}")


def _validate_decision(
    decision: Dict[str, Any],
    account_state: Dict[str, Any]
) -> Dict[str, Any]:
    """Validate AI decision and ensure it's safe."""
    
    # Ensure required fields
    if "action" not in decision:
        decision["action"] = "WAIT"
    
    if "next_run_at_utc" not in decision:
        decision["next_run_at_utc"] = _get_next_check_time(15)
    
    # Validate action types
    valid_actions = ["WAIT", "WATCH", "ENTER", "MANAGE", "EXIT", "STAND_DOWN", "ERROR"]
    if decision["action"] not in valid_actions:
        decision["action"] = "WAIT"
    
    # Validate order_intent if present
    if decision.get("order_intent"):
        order = decision["order_intent"]
        required_fields = ["type", "price", "stop_loss", "take_profit", "risk_pct"]
        for field in required_fields:
            if field not in order:
                decision["action"] = "WAIT"
                decision["order_intent"] = None
                break
    
    # Check account constraints
    if decision["action"] == "ENTER":
        open_positions = account_state.get("open_positions", [])
        max_trades = account_state.get("max_trades_per_day", 10)
        if len(open_positions) >= max_trades:
            decision["action"] = "WAIT"
            decision["reason_codes"] = decision.get("reason_codes", []) + ["MAX_TRADES_REACHED"]
            decision["order_intent"] = None
    
    return decision


def _determine_next_timeframes(
    decision: Dict[str, Any],
    is_start_of_day: bool,
    ohlc_data: Dict[str, Any]
) -> List[str]:
    """
    Determine which timeframes to request next based on AI decision.
    
    The AI can specify next_requested_timeframes in its response,
    or we can infer based on the action and context.
    """
    # Check if AI explicitly requested timeframes
    if "next_requested_timeframes" in decision:
        return decision["next_requested_timeframes"]
    
    # Infer based on action
    action = decision.get("action", "WAIT")
    
    if action == "WAIT":
        # Low activity - check H1 and M15 periodically
        return ["H1", "M15"]
    
    elif action == "WATCH":
        # Monitoring - need M15 and M5
        return ["M15", "M5"]
    
    elif action == "ENTER" or action == "HOT_ZONE":
        # Hot zone - need M5 and M1 for precision
        return ["M5", "M1"]
    
    elif action == "MANAGE" or action == "IN_TRADE":
        # Managing position - M1 for tight monitoring
        return ["M1", "M5"]
    
    else:
        # Default: H1 and M15
        return ["H1", "M15"]


def _format_ea_response(decision: Dict[str, Any]) -> Dict[str, Any]:
    """Format response for MT5 EA consumption."""
    
    return {
        "action": decision.get("action", "WAIT"),
        "next_run_at_utc": decision.get("next_run_at_utc"),
        "setup_id": decision.get("setup_id"),
        "levels_update": decision.get("levels_update"),
        "order_intent": decision.get("order_intent"),
        "reason_codes": decision.get("reason_codes", []),
        "state_update": decision.get("state_update", {}),
        "next_requested_timeframes": decision.get("next_requested_timeframes", []),
        "error": decision.get("error")
    }


def _get_next_check_time(minutes: int) -> str:
    """Get next check time in UTC ISO format."""
    next_time = datetime.now(timezone.utc)
    from datetime import timedelta
    next_time += timedelta(minutes=minutes)
    return next_time.isoformat()
