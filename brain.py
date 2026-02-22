"""
Brain - Core Trading Logic

Orchestrates the trading decision-making process by coordinating
OHLC analysis, chart analysis, market data, and AI reasoning.
"""

import json
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


def sod_action(
    symbol: str,
    ohlc_data: Dict[str, List[Dict[str, Any]]],
    positions: List[Dict[str, Any]] = None  # DEPRECATED - now retrieved from DB
) -> Dict[str, Any]:
    """
    Start of Day (SOD) action - comprehensive daily market analysis.
    
    This function orchestrates the complete SOD workflow:
    1. Analyzes OHLC data across all timeframes
    2. Fetches and analyzes chart images using GPT Vision
    3. Retrieves market data (news, sentiment, events)
    4. Retrieves current positions from database
    5. Sends all data to GPT for comprehensive SOD analysis
    6. Returns detailed analysis in JSON format
    
    Args:
        symbol: Trading symbol (e.g., "GBPUSD")
        ohlc_data: Dictionary with timeframe keys and OHLC candle arrays
                  Format: {
                      "1h_DATA": [...],
                      "4h_DATA": [...],
                      "1D_DATA": [...],
                      "1W_DATA": [...]
                  }
        positions: DEPRECATED - positions now retrieved from database
    
    Returns:
        Comprehensive SOD analysis as JSON dictionary
    """
    print("=" * 60)
    print(f"🚀 Starting SOD Analysis for {symbol}")
    print("=" * 60)
    
    # Step 0a: Get current positions from database
    print("\n📍 Retrieving current positions from database...")
    db_positions = get_current_positions(symbol)
    if db_positions:
        print(f"✓ Found {len(db_positions)} open position(s)")
    else:
        print("ℹ️  No open positions")
    
    # Step 0b: Get previous last_run_note for context (from yesterday or overnight)
    print("\n📝 Retrieving previous run context...")
    previous_run = get_analysis_note(symbol, 'last_run_note')
    if previous_run:
        print(f"✓ Found previous run note")
    else:
        print("ℹ️  No previous run note found (first run or fresh start)")
    
    # Step 1: Analyze OHLC data
    print("\n📊 Step 1: Analyzing OHLC data...")
    try:
        processed_ohlc = analyze_ohlc_data(ohlc_data)
        print(f"✓ OHLC analysis complete - {len(processed_ohlc.get('timeframes', {}))} timeframes processed")
    except Exception as e:
        print(f"✗ Error in OHLC analysis: {e}")
        processed_ohlc = {}
    
    # Step 2: Prepare timeframes for chart analysis
    print("\n📈 Step 2: Preparing chart analysis...")
    timeframes = ["H1", "H4", "D1", "W1"]  # SOD timeframes
    chart_analysis = {"status": "ready"}  # Charts will be analyzed in final GPT call
    
    # Step 3: Get market data
    print("\n🌐 Step 3: Retrieving market data...")
    try:
        market_context = get_market_data(symbol)
        print(f"✓ Market data retrieved")
    except Exception as e:
        print(f"✗ Error retrieving market data: {e}")
        market_context = {}
    
    # Step 4: Prepare comprehensive context for GPT
    print("\n🤖 Step 4: Preparing comprehensive analysis...")
    
    # Build context string
    context_parts = [
        f"SYMBOL: {symbol}",
        f"ANALYSIS DATE: {datetime.now(timezone.utc).isoformat()}",
        f"CURRENT TIME: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        "=== SYSTEM INFO ===",
        "SOD (Start of Day) analysis runs automatically at 07:00 UTC every morning.",
        "This is your comprehensive daily analysis. Use it to set your bias and trading plan for the day.",
        ""
    ]
    
    # Add previous run context if available (full response)
    if previous_run:
        context_parts.extend([
            "=== PREVIOUS RUN CONTEXT (Last Analysis) ===",
            json.dumps(previous_run, indent=2),
            ""
        ])
    
    # Add current positions from database
    if db_positions:
        context_parts.extend([
            "=== CURRENT OPEN POSITIONS (From Database) ===",
            json.dumps(db_positions, indent=2),
            ""
        ])
    
    # Add positions sent from EA (if any) - for comparison/validation
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
        "=== CHART ANALYSIS ===",
        "Charts will be analyzed visually by GPT Vision",
        "",
        "=== MARKET DATA ===",
        json.dumps(market_context, indent=2)
    ])
    
    full_context = "\n".join(context_parts)
    
    # Step 5: Send to GPT with SOD prompt (includes chart analysis)
    print("\n🧠 Step 5: Sending to GPT for comprehensive SOD analysis...")
    try:
        sod_prompt = get_sod_prompt()
        
        # Use chart analyzer which fetches charts and analyzes with GPT Vision
        print("Fetching chart images and analyzing with GPT Vision...")
        result = analyze_charts_with_gpt_vision(
            symbol=symbol,
            timeframes=timeframes,
            context=full_context,
            system_prompt=sod_prompt
        )
        
        print("✓ GPT analysis complete")
        
        # Save to database (sod_note) - save entire response
        print("\n💾 Saving SOD analysis to database...")
        try:
            save_analysis_note(symbol, 'sod_note', result)
            print("✓ SOD analysis (full response) saved to sod_note")
        except Exception as e:
            print(f"✗ Error saving to database: {e}")
        
        # Now clear last_run_note after saving SOD
        print("\n🗑️  Clearing last_run_note for fresh day...")
        try:
            clear_analysis_notes(symbol, ['last_run_note'])
            print("✓ last_run_note cleared")
        except Exception as e:
            print(f"✗ Error clearing note: {e}")
        
        # Add metadata
        result["sod_metadata"] = {
            "symbol": symbol,
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "ohlc_timeframes_analyzed": list(ohlc_data.keys()),
            "chart_timeframes_analyzed": timeframes,
            "previous_run_context_used": previous_run is not None
        }
        
        print("\n" + "=" * 60)
        print("✅ SOD Analysis Complete")
        print("=" * 60)
        
        return result
        
    except Exception as e:
        print(f"✗ Error in GPT analysis: {e}")
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
    
    This function orchestrates intraday trading workflow:
    1. Analyzes OHLC data for entry signals and price action
    2. Fetches and analyzes chart images for visual confirmation
    3. Retrieves market data for context
    4. Retrieves current positions from database
    5. Sends all data to GPT with intraday prompt
    6. Returns trading decision in JSON format
    
    Args:
        symbol: Trading symbol (e.g., "GBPUSD")
        ohlc_data: Dictionary with timeframe keys and OHLC candle arrays
                  Typically: H1, M15, M5, M1 for active trading
        positions: DEPRECATED - positions now retrieved from database
    
    Returns:
        Intraday trading decision as JSON dictionary
    """
    print("=" * 60)
    print(f"📈 Starting Intraday Analysis for {symbol}")
    print("=" * 60)
    
    # Step 0a: Get current positions from database
    print("\n📍 Retrieving current positions from database...")
    db_positions = get_current_positions(symbol)
    if db_positions:
        print(f"✓ Found {len(db_positions)} open position(s)")
    else:
        print("ℹ️  No open positions")
    
    # Step 0b: Get SOD and last run notes for context
    print("\n📝 Retrieving previous analysis notes...")
    sod_note = get_analysis_note(symbol, 'sod_note')
    last_run_note = get_analysis_note(symbol, 'last_run_note')
    
    if sod_note:
        print(f"✓ Found SOD note")
    else:
        print("ℹ️  No SOD note found")
    
    if last_run_note:
        print(f"✓ Found last run note")
    else:
        print("ℹ️  No previous intraday run note found")
    
    # Step 1: Analyze OHLC data
    print("\n📊 Step 1: Analyzing OHLC data...")
    try:
        processed_ohlc = analyze_ohlc_data(ohlc_data)
        print(f"✓ OHLC analysis complete")
    except Exception as e:
        print(f"✗ Error in OHLC analysis: {e}")
        processed_ohlc = {}
    
    # Step 2: Determine timeframes (typically shorter for intraday)
    print("\n📈 Step 2: Preparing chart analysis...")
    timeframes = list(ohlc_data.keys())[:4]  # Use provided timeframes
    if not timeframes:
        timeframes = ["H1", "M15"]  # Default intraday timeframes
    
    # Step 3: Get market data
    print("\n🌐 Step 3: Retrieving market data...")
    try:
        market_context = get_market_data(symbol)
        print(f"✓ Market data retrieved")
    except Exception as e:
        print(f"✗ Error retrieving market data: {e}")
        market_context = {}
    
    # Step 4: Prepare context
    print("\n🤖 Step 4: Preparing comprehensive analysis...")
    context_parts = [
        f"SYMBOL: {symbol}",
        f"ANALYSIS TIME: {datetime.now(timezone.utc).isoformat()}",
        f"CURRENT TIME: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        "=== SYSTEM INFO ===",
        "SOD (Start of Day) analysis runs automatically at 07:00 UTC every morning.",
        "You are currently in an INTRADAY analysis run.",
        "Use the SOD note below to understand today's overall trading plan.",
        ""
    ]
    
    # Add SOD context (full response)
    if sod_note:
        context_parts.extend([
            "=== START OF DAY ANALYSIS (Today's SOD) ===",
            json.dumps(sod_note, indent=2),
            ""
        ])
    
    # Add last run context (full response)
    if last_run_note:
        context_parts.extend([
            "=== LAST RUN ANALYSIS (Previous Intraday Check) ===",
            json.dumps(last_run_note, indent=2),
            ""
        ])
    
    # Add current positions from database
    if db_positions:
        context_parts.extend([
            "=== CURRENT OPEN POSITIONS (From Database) ===",
            json.dumps(db_positions, indent=2),
            ""
        ])
    
    # Add positions sent from EA (if any) - for comparison/validation
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
        "=== CHART ANALYSIS ===",
        "Charts will be analyzed visually by GPT Vision",
        "",
        "=== MARKET DATA ===",
        json.dumps(market_context, indent=2)
    ])
    full_context = "\n".join(context_parts)
    
    # Step 5: Send to GPT with intraday prompt
    print("\n🧠 Step 5: Sending to GPT for intraday trading analysis...")
    try:
        intraday_prompt = get_intraday_prompt()
        
        result = analyze_charts_with_gpt_vision(
            symbol=symbol,
            timeframes=timeframes,
            context=full_context,
            system_prompt=intraday_prompt
        )
        
        print("✓ GPT analysis complete")
        
        # Save to database as last_run_note - save entire response
        print("\n💾 Saving intraday analysis to database...")
        try:
            save_analysis_note(symbol, 'last_run_note', result)
            print("✓ Intraday analysis (full response) saved as last_run_note")
        except Exception as e:
            print(f"✗ Error saving to database: {e}")
        
        # Add metadata
        result["intraday_metadata"] = {
            "symbol": symbol,
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "timeframes_analyzed": timeframes,
            "sod_context_used": sod_note is not None,
            "last_run_context_used": last_run_note is not None
        }
        
        print("\n" + "=" * 60)
        print("✅ Intraday Analysis Complete")
        print("=" * 60)
        
        return result
        
    except Exception as e:
        print(f"✗ Error in GPT analysis: {e}")
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
