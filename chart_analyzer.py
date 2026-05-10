"""
Chart Analyzer

Handles everything chart-related:
  1. Fetch chart images from chart-img.com (returns PNG bytes → convert to base64)
  2. Send charts to GPT Vision for pure visual analysis (no text context)
  3. Return per-timeframe visual observations to brain.py

Called by brain.py via: analyze_charts_with_gpt_vision(...)
  Returns: {"chart_analysis": "<plain-text vision output>", "_metadata": {...}}

brain.py then uses these observations as part of its full decision context.
Test helpers: save_chart_image(...), get_chart_url(...)
"""

import requests
import base64
import os
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

CHART_IMG_API_KEY = os.getenv("CHART_IMG_API_KEY")
CHART_IMG_BASE_URL = "https://api.chart-img.com/v1/tradingview/advanced-chart"

# Lazy-initialised — avoids crashing the server at import time if the key
# is not yet set in the environment (e.g. during Railway container startup).
_openai_client: OpenAI | None = None


def _get_openai_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is not set")
        _openai_client = OpenAI(api_key=api_key)
    return _openai_client


# =============================================================================
# GPT VISION CHART ANALYSIS PROMPT
# Edit this to change how GPT reads and interprets charts.
# =============================================================================

CHART_ANALYSIS_PROMPT = """
You are BotCore — a highly advanced AI trading system built for the Global Trading Society Team, you are now performing pure visual technical analysis on TradingView chart images.

You will receive one or more labelled chart images. Each image is preceded by a label like "--- H1 TIMEFRAME CHART ---".
For EACH chart image, provide a compressed but detailed, objective visual analysis describing exactly what you see.
You are to output your analysis as a bullet point list, no essay, but precise, to the point bullet points of what you see.

ANALYZE EACH CHART FOR:
- The overall trend direction, recent market structure sequence and recent breaks of structure.
- The current direction and strenght of order flow of the market, who is dominating and by reactions does it look like momentum, volatility, squeeze, or equal. 
- EXACT key price levels where readable, major and/or equal high and low levels(HLs), support and resistance levels (SRs).
- EXACT key price levels where readable of imbalances, 3-candle pattern where the middle candle creates a price gap not also hit by the first and third candle wicks; and whether price has returned to fill them or they remain open.
- EXACT key price levels where readable of Fair Value Gaps (FVGs), the same pattern as an imbalance, but where a key level of imblanace/HLs/SRs has been broken into, then as price breaks back out of the key level into the direction it came from, the middle candle is formed.
- EXACT key price levels (where readable) and characteristics of candlestick patterns.
- Where is the current candle and price relative to the above, what is its EXACT key price levels where readable

IMPORTANT:
- Be objective — describe only what you can SEE on the chart
- Use specific price levels wherever visible
- Do NOT make trading decisions — only describe what you observe

OUTPUT FORMAT:
Return your output list with one key per timeframe label (e.g. "H1:...", "H4:...", "D1:...", "W1:...").
Each value must be a detailed string of your visual observations for that chart.

Return ONLY the string output. No markdown, no extra text outside this.
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
            "timezone": "Europe/London",
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
            "chart_analysis": "Plain-text bullet/analysis from Vision (no JSON parse).",
            "_metadata": {
                "symbol": "FX:EURUSD",
                "timeframes_analyzed": [...],
                "timeframes_requested": [...]
            }
        }

    Raises:
        ValueError if OPENAI_API_KEY is missing or all chart fetches fail.
    """
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY not set — cannot run chart analysis")

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
        response = _get_openai_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=3000,
            temperature=0.2
        )
        raw = response.choices[0].message.content or ""
        chart_text = _normalize_chart_vision_text(raw)
        print(f"[analysis] GPT Vision response received ({len(chart_text)} chars)")

        return {
            "chart_analysis": chart_text,
            "_metadata": {
                "symbol": formatted_symbol,
                "timeframes_analyzed": [img["timeframe"] for img in chart_images],
                "timeframes_requested": timeframes,
            }
        }

    except Exception as e:
        print(f"[analysis] GPT Vision call failed: {e}")
        raise


def _normalize_chart_vision_text(text: str) -> str:
    """Strip whitespace; if the model wrapped prose in markdown fences, unwrap."""
    s = (text or "").strip()
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1 :]
        end = s.rfind("```")
        if end != -1:
            s = s[:end]
    return s.strip()
