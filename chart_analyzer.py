"""
Chart Analyzer

Handles everything chart-related:
  1. Fetch chart images from chart-img.com (returns PNG bytes → convert to base64)
  2. Send charts + context to GPT Vision for analysis
  3. Parse JSON response back to brain.py

Called by brain.py via: analyze_charts_with_gpt_vision(...)
Test helpers: save_chart_image(...), get_chart_url(...)
"""

import requests
import base64
import os
import json
import re
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

CHART_IMG_API_KEY = os.getenv("CHART_IMG_API_KEY")
CHART_IMG_BASE_URL = "https://api.chart-img.com/v1/tradingview/advanced-chart"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


# =============================================================================
# GPT VISION CHART ANALYSIS PROMPT
# Edit this to change how GPT reads and interprets charts.
# =============================================================================

CHART_ANALYSIS_PROMPT = """
You are analyzing a TradingView chart image for trading decisions.

ANALYZE THE FOLLOWING VISUAL ELEMENTS:

1. TREND & STRUCTURE:
   - Overall trend direction (uptrend, downtrend, sideways/ranging)
   - Market structure: Higher Highs (HH), Higher Lows (HL), Lower Highs (LH), Lower Lows (LL)
   - Trendlines and channels visible on the chart
   - Break of structure (BOS) - when price breaks recent swing highs/lows

2. KEY PRICE LEVELS:
   - Major support and resistance zones (horizontal levels where price has reacted multiple times)
   - Swing highs and swing lows (local peaks and troughs)
   - Round numbers / psychological levels (e.g., 1.3000, 1.2500)
   - Areas of consolidation or price congestion

3. CANDLESTICK PATTERNS & PRICE ACTION:
   - Recent candle formations (engulfing, pin bars, doji, etc.)
   - Wicks and rejection at levels (long wicks indicate rejection)
   - Consolidation patterns (triangles, rectangles, flags)
   - Gap analysis (if visible)

4. FAIR VALUE GAPS (FVG):
   - Look for 3-candle patterns where the middle candle's wick doesn't overlap with candles on either side
   - These appear as "gaps" or "imbalances" on the chart
   - Price often returns to fill these gaps

5. LIQUIDITY ZONES:
   - Areas where stops might be clustered (above swing highs for shorts, below swing lows for longs)
   - Equal highs/lows that could be "liquidity grabs"
   - Areas of high trading activity (visible volume if shown)

6. CURRENT PRICE POSITION:
   - Where is price relative to key levels?
   - Is price at support, resistance, or in between?
   - Distance from recent highs/lows
   - Any immediate reaction zones nearby?

7. MOMENTUM & VOLATILITY:
   - Are candles getting larger or smaller? (expansion vs contraction)
   - Speed of price movement (fast moves vs slow grinding)
   - Any signs of exhaustion? (smaller bodies, longer wicks)

IMPORTANT NOTES:
- Be objective - describe what you SEE, not what you think should happen
- Identify BOTH bullish and bearish scenarios
- Point out conflicting signals if present
- Use specific price levels when describing zones
- Mention timeframe context (e.g., "on the 4H chart...")

Your analysis should be factual, concise, and actionable for trading decisions.
"""


# =============================================================================
# TIMEFRAME MAP
# Our format (H1, H4, etc.) → chart-img.com interval format
# =============================================================================

TIMEFRAME_MAP = {
    "M1":  "1m",
    "M5":  "5m",
    "M15": "15m",
    "M30": "30m",
    "H1":  "1h",
    "H4":  "4h",
    "D1":  "1D",
    "W1":  "1W",
}


# =============================================================================
# CHART FETCHING
# =============================================================================

def get_chart_image_base64(
    symbol: str,
    timeframe: str = "H1",
    width: int = 800,
    height: int = 600,
    theme: str = "dark",
    studies: str = ""
) -> Optional[str]:
    """
    Fetch a chart image from chart-img.com and return it as a base64 string.

    chart-img.com returns a raw PNG in the response body.
    We convert the PNG bytes to base64 so it can be embedded in the
    OpenAI Vision API request (data:image/png;base64,<string>).

    Args:
        symbol:    EXCHANGE:SYMBOL format, e.g. "FX:EURUSD", "BINANCE:BTCUSDT"
        timeframe: One of M1 M5 M15 M30 H1 H4 D1 W1
        width:     Image width in pixels  (default 1200)
        height:    Image height in pixels (default 800)
        theme:     "dark" or "light"
        studies:   Comma-separated indicators e.g. "RSI,MACD" — leave empty for clean chart

    Returns:
        Base64-encoded PNG string, or None if the fetch failed.
    """
    if not CHART_IMG_API_KEY:
        print("ERROR: CHART_IMG_API_KEY not set in environment")
        return None

    try:
        chart_interval = TIMEFRAME_MAP.get(timeframe, "1h")

        params = {
            "symbol":   symbol,
            "interval": chart_interval,
            "width":    width,
            "height":   height,
            "theme":    theme,
            "studies":  studies,
        }

        headers = {"Authorization": f"Bearer {CHART_IMG_API_KEY}"}

        print(f"[chart] Fetching {symbol} {timeframe} from chart-img.com...")

        response = requests.get(
            CHART_IMG_BASE_URL,
            params=params,
            headers=headers,
            timeout=15
        )

        if response.status_code == 200:
            image_bytes = response.content
            image_base64 = base64.b64encode(image_bytes).decode("utf-8")
            print(f"[chart] OK: {symbol} {timeframe} ({len(image_bytes):,} bytes)")
            return image_base64
        else:
            print(f"[chart] ERROR {response.status_code}: {response.text[:200]}")
            return None

    except requests.exceptions.Timeout:
        print(f"[chart] Timeout fetching {symbol} {timeframe}")
        return None
    except Exception as e:
        print(f"[chart] Error: {e}")
        return None


# =============================================================================
# HELPER FUNCTIONS (testing / debugging)
# =============================================================================

def save_chart_image(
    symbol: str,
    timeframe: str,
    output_path: str,
    width: int = 800,
    height: int = 600
) -> bool:
    """
    Fetch a chart and save it to disk — useful for visually checking what
    GPT Vision receives.

    Args:
        symbol:      e.g. "FX:EURUSD"
        timeframe:   e.g. "H1"
        output_path: e.g. "test_eurusd.png"
        width / height: image dimensions

    Returns:
        True if saved successfully, False otherwise.
    """
    image_base64 = get_chart_image_base64(symbol, timeframe, width, height)
    if not image_base64:
        return False

    try:
        image_bytes = base64.b64decode(image_base64)
        with open(output_path, "wb") as f:
            f.write(image_bytes)
        print(f"[chart] Saved to {output_path}")
        return True
    except Exception as e:
        print(f"[chart] Error saving: {e}")
        return False


def get_chart_url(
    symbol: str,
    timeframe: str = "H1",
    width: int = 800,
    height: int = 600,
    theme: str = "dark"
) -> Optional[str]:
    """
    Return the direct URL for a chart (without fetching it).
    Paste into a browser to see exactly what chart-img.com will return.
    """
    if not CHART_IMG_API_KEY:
        return None
    chart_interval = TIMEFRAME_MAP.get(timeframe, "1h")
    return (
        f"{CHART_IMG_BASE_URL}"
        f"?symbol={symbol}&interval={chart_interval}"
        f"&width={width}&height={height}&theme={theme}"
        f"&key={CHART_IMG_API_KEY}"
    )


def format_symbol_for_chart(symbol: str, asset_type: str = "forex") -> str:
    """
    Add the exchange prefix that chart-img.com requires.

    e.g.  "EURUSD"  + "forex"  → "FX:EURUSD"
          "BTCUSDT" + "crypto" → "BINANCE:BTCUSDT"
          "FX:EURUSD"          → "FX:EURUSD"  (already prefixed, unchanged)
    """
    if ":" in symbol:
        return symbol
    if asset_type == "forex":
        return f"FX:{symbol}"
    elif asset_type == "crypto":
        return f"BINANCE:{symbol}"
    elif asset_type == "stock":
        return f"NASDAQ:{symbol}"
    return symbol


# =============================================================================
# GPT VISION ANALYSIS  — called by brain.py
# =============================================================================

def analyze_charts_with_gpt_vision(
    symbol: str,
    timeframes: List[str],
    context: str,
    system_prompt: str,
    asset_type: str = "forex"
) -> Dict[str, Any]:
    """
    Fetch charts for every requested timeframe, send them + context to
    GPT Vision, and return the parsed JSON trading decision.

    Args:
        symbol:        Raw symbol e.g. "EURUSD"
        timeframes:    List of timeframes e.g. ["H1", "H4", "D1"]
        context:       Full text context built by brain.py (OHLC summary,
                       market data, current positions, previous analysis notes)
        system_prompt: The SOD or Intraday prompt from prompt.py
        asset_type:    "forex" | "crypto" | "stock"

    Returns:
        Parsed JSON dict with the trading decision + _metadata key.

    Raises:
        ValueError if OPENAI_API_KEY is missing or all chart fetches fail.
    """
    if not client:
        raise ValueError("OPENAI_API_KEY not set -- cannot run analysis")

    formatted_symbol = format_symbol_for_chart(symbol, asset_type)
    print(f"\n[analysis] Starting: {formatted_symbol} {', '.join(timeframes)}")

    # --- fetch all charts ---
    chart_images = []
    for tf in timeframes:
        image_base64 = get_chart_image_base64(
            symbol=formatted_symbol,
            timeframe=tf,
            width=800,
            height=600,
            theme="dark"
        )
        if image_base64:
            chart_images.append({"timeframe": tf, "base64": image_base64})
        else:
            print(f"[analysis] Skipping {tf} -- fetch failed")

    if not chart_images:
        raise ValueError(
            f"Could not fetch any chart images for {formatted_symbol}. "
            "Check CHART_IMG_API_KEY and chart-img.com status."
        )

    print(f"[analysis] Fetched {len(chart_images)}/{len(timeframes)} charts")
    print("[analysis] Sending to GPT Vision...")

    # --- build Vision API message ---
    # First part: chart analysis instructions + all context text
    content = [
        {
            "type": "text",
            "text": f"{CHART_ANALYSIS_PROMPT}\n\n---\n\nCONTEXT DATA:\n{context}"
        }
    ]

    # Then one label + one image per timeframe
    for img in chart_images:
        content.append({
            "type": "text",
            "text": f"\n\n--- {img['timeframe']} TIMEFRAME CHART ---"
        })
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{img['base64']}"}
        })

    messages = [
        {"role": "system",  "content": system_prompt},
        {"role": "user",    "content": content}
    ]

    # --- call OpenAI Vision ---
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=4000,
            temperature=0.3
        )
        response_text = response.choices[0].message.content
        print(f"[analysis] GPT Vision response received ({len(response_text)} chars)")

        result = _parse_gpt_response(response_text)
        result["_metadata"] = {
            "symbol": formatted_symbol,
            "timeframes_analyzed": [img["timeframe"] for img in chart_images],
            "timeframes_requested": timeframes,
        }
        return result

    except Exception as e:
        print(f"[analysis] GPT Vision call failed: {e}")
        raise


# =============================================================================
# INTERNAL — JSON PARSER
# =============================================================================

def _parse_gpt_response(response_text: str) -> Dict[str, Any]:
    """
    Extract JSON from GPT's response.

    GPT sometimes wraps the JSON in ```json ... ``` code fences.
    We try several approaches to pull out the dict.
    On total failure we return a safe WAIT default so no trade is placed.
    """
    try:
        # Strip markdown fences if present
        if "```json" in response_text:
            start = response_text.find("```json") + 7
            end   = response_text.find("```", start)
            json_str = response_text[start:end].strip()
        elif "```" in response_text:
            start = response_text.find("```") + 3
            end   = response_text.find("```", start)
            json_str = response_text[start:end].strip()
        else:
            json_str = response_text.strip()

        # Regex grab of the outermost { ... }
        match = re.search(r"\{.*\}", json_str, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            print("[parse] JSON parsed OK")
            return parsed

        parsed = json.loads(json_str)
        print("[parse] JSON parsed OK")
        return parsed

    except json.JSONDecodeError as e:
        print(f"[parse] Could not parse JSON: {e}")
        print(f"[parse] Raw response (first 500 chars): {response_text[:500]}")
        return {
            "action": "WAIT",
            "summary": "Failed to parse AI response",
            "explanation": f"JSON parse error: {e}",
            "decision": {"action": "WAIT", "next_review_time": None},
            "_error": "parse_failure",
        }
