"""
Chart Analyzer

Handles everything chart-related:
  1. Fetch chart images from chart-img.com (returns PNG bytes → convert to base64)
  2. Send charts to GPT Vision for pure visual analysis (no text context)
  3. Return per-timeframe visual observations to brain.py

Called by brain.py via: analyze_charts_with_gpt_vision(...)
  Returns: {"chart_analysis": {"H1": "...", "H4": "..."}, "_metadata": {...}}

brain.py then uses these observations as part of its full decision context.
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
You are a professional chart analyst performing pure visual technical analysis on TradingView chart images.

You will receive one or more labelled chart images. Each image is preceded by a label like "--- H1 TIMEFRAME CHART ---".

For EACH chart image, provide a detailed, objective visual analysis describing exactly what you see.

ANALYZE EACH CHART FOR:

1. TREND & STRUCTURE:
   - Overall trend direction (uptrend, downtrend, sideways/ranging)
   - Market structure sequence: Higher Highs (HH), Higher Lows (HL), Lower Highs (LH), Lower Lows (LL)
   - Visible trendlines or channels
   - Recent breaks of structure (BOS) — where price broke through a prior swing high/low

2. KEY PRICE LEVELS (use exact prices where readable on the chart):
   - Major support and resistance zones (levels price has reacted to multiple times)
   - Swing highs and swing lows (notable peaks and troughs)
   - Round numbers or psychological levels (e.g. 1.3000, 1.2500)
   - Areas of price congestion or consolidation

3. CANDLESTICK PATTERNS & PRICE ACTION:
   - Notable recent candle formations (engulfing, pin bars, doji, inside bars, etc.)
   - Strong wick rejections at levels
   - Consolidation patterns (triangles, flags, rectangles, wedges)

4. FAIR VALUE GAPS (FVG) / IMBALANCES:
   - 3-candle patterns where the middle candle creates a gap not covered by the first and third candle wicks
   - Note approximate price range of any visible FVGs
   - Whether price has returned to fill them or they remain open

5. LIQUIDITY ZONES:
   - Equal highs or equal lows (clustered levels that attract price for liquidity grabs)
   - Obvious stop clusters: above swing highs (sell-side liq.) or below swing lows (buy-side liq.)
   - Areas of dense candle bodies indicating high-activity zones

6. CURRENT PRICE POSITION:
   - Where is the current candle/price relative to key levels?
   - Is price at support, at resistance, breaking out, or in the middle of a range?
   - How far is price from the nearest significant level?

7. MOMENTUM & VOLATILITY:
   - Expanding or contracting candle bodies (acceleration vs. exhaustion)?
   - Speed of recent price movement
   - Any visible signs of momentum shift (smaller bodies, longer wicks, spinning tops)?

IMPORTANT:
- Be objective — describe only what you can SEE on the chart
- Use specific price levels wherever visible
- Identify BOTH bullish and bearish scenarios present on the chart
- Flag any conflicting signals across the chart
- Do NOT make trading decisions — only describe what you observe

OUTPUT FORMAT:
Return a JSON object with one key per timeframe label (e.g. "H1", "H4", "D1", "W1").
Each value must be a detailed string of your visual observations for that chart.

Example:
{
  "H1": "The H1 chart shows a clear downtrend with LH/LL structure since the swing high at 1.0950. Price is currently testing a support zone at 1.0870 that has held twice before. A small bullish engulfing formed on the last closed candle, but momentum is still bearish with expanding red bodies...",
  "H4": "On the 4H chart, price has been consolidating between 1.0860 support and 1.0930 resistance for the past 8 candles. There is an open FVG between 1.0900 and 1.0915. No clear directional bias — equal highs at 1.0930 could attract price for a liquidity grab before a reversal..."
}

Return ONLY the JSON object. No markdown, no extra text outside the JSON.
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
    asset_type: str = "forex"
) -> Dict[str, Any]:
    """
    Fetch charts for every requested timeframe and send them to GPT Vision
    for pure visual technical analysis — no text context, no trading decision.

    GPT Vision receives only the CHART_ANALYSIS_PROMPT and the chart images.
    This lets it focus entirely on what it sees, producing richer observations.

    brain.py then combines these observations with OHLC data, market data,
    positions, and analysis notes before making the final trading decision.

    Args:
        symbol:     Raw symbol e.g. "EURUSD"
        timeframes: List of timeframes e.g. ["H1", "H4", "D1", "W1"]
        asset_type: "forex" | "crypto" | "stock"

    Returns:
        {
            "chart_analysis": {
                "H1": "detailed visual description...",
                "H4": "detailed visual description...",
                ...
            },
            "_metadata": {
                "symbol": "FX:EURUSD",
                "timeframes_analyzed": [...],
                "timeframes_requested": [...]
            }
        }

    Raises:
        ValueError if OPENAI_API_KEY is missing or all chart fetches fail.
    """
    if not client:
        raise ValueError("OPENAI_API_KEY not set -- cannot run analysis")

    formatted_symbol = format_symbol_for_chart(symbol, asset_type)
    print(f"\n[analysis] Starting visual chart analysis: {formatted_symbol} {', '.join(timeframes)}")

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
    print("[analysis] Sending to GPT Vision for pure visual analysis...")

    # --- build Vision API message ---
    # System: chart analysis instructions only — no trading context
    # User: one label + one image per timeframe
    content = [
        {
            "type": "text",
            "text": CHART_ANALYSIS_PROMPT
        }
    ]

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
        {"role": "user", "content": content}
    ]

    # --- call OpenAI Vision ---
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=3000,
            temperature=0.2
        )
        response_text = response.choices[0].message.content
        print(f"[analysis] GPT Vision response received ({len(response_text)} chars)")

        chart_observations = _parse_chart_response(response_text, chart_images)

        return {
            "chart_analysis": chart_observations,
            "_metadata": {
                "symbol": formatted_symbol,
                "timeframes_analyzed": [img["timeframe"] for img in chart_images],
                "timeframes_requested": timeframes,
            }
        }

    except Exception as e:
        print(f"[analysis] GPT Vision call failed: {e}")
        raise


# =============================================================================
# INTERNAL — CHART RESPONSE PARSER
# =============================================================================

def _parse_chart_response(response_text: str, chart_images: list) -> Dict[str, Any]:
    """
    Parse the per-timeframe JSON dict returned by GPT Vision.

    GPT is asked to return: {"H1": "...", "H4": "...", ...}
    If it wraps in markdown fences we strip them first.
    On parse failure we fall back to storing the raw text under each timeframe key
    so brain.py still receives something useful.
    """
    try:
        # Strip markdown fences if present
        if "```json" in response_text:
            start    = response_text.find("```json") + 7
            end      = response_text.find("```", start)
            json_str = response_text[start:end].strip()
        elif "```" in response_text:
            start    = response_text.find("```") + 3
            end      = response_text.find("```", start)
            json_str = response_text[start:end].strip()
        else:
            json_str = response_text.strip()

        match = re.search(r"\{.*\}", json_str, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            print("[parse] Chart analysis JSON parsed OK")
            return parsed

        parsed = json.loads(json_str)
        print("[parse] Chart analysis JSON parsed OK")
        return parsed

    except json.JSONDecodeError as e:
        print(f"[parse] Could not parse chart analysis JSON: {e}")
        # Fall back: store the raw text under each timeframe key
        fallback = {img["timeframe"]: response_text for img in chart_images}
        fallback["_parse_error"] = str(e)
        return fallback
