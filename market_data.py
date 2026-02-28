"""
Market Data Service

Flow:
  1. Fetch raw data from 3 sources in parallel:
       - RapidAPI (Yahoo Finance) — live prices: VIX, DXY, yields, gold, oil, indices
       - Perplexity #1           — macro: CPI, NFP, Fed policy, rate expectations
       - Perplexity #2           — catalysts: economic calendar, news, geopolitics
  2. Synthesize all raw data into structured forex market intelligence (via GPT-4o-mini)
  3. Return the synthesis dict — brain.py saves this to DB as market_data_note

The synthesis is done ONCE at SOD (or when cache is stale).
Every intraday AI call reads the synthesis straight from the DB — no re-fetching.

APIs used (already in .env):
  RAPIDAPI_KEY       - Yahoo Finance via RapidAPI
  OPENROUTER_API_KEY - Perplexity via OpenRouter
  OPENAI_API_KEY     - GPT-4o-mini synthesis via OpenAI
"""

import requests
import os
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv()

RAPIDAPI_KEY   = os.getenv("RAPIDAPI_KEY")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")


# =============================================================================
# SECTION 1: RAPIDAPI — KEY RISK ASSETS
# =============================================================================

RISK_ASSETS = {
    "^VIX":     "VIX (Equity Volatility)",
    "DX-Y.NYB": "DXY (US Dollar Index)",
    "^TNX":     "US 10Y Treasury Yield",
    "^UST2YR":  "US 2Y Treasury Yield",
    "GC=F":     "Gold",
    "CL=F":     "Crude Oil",
    "^GSPC":    "S&P 500",
    "NQ=F":     "Nasdaq Futures",
    "BTC-USD":  "Bitcoin",
}


def fetch_risk_assets() -> Dict[str, Any]:
    """Fetch key risk asset prices via RapidAPI (Yahoo Finance)."""
    if not RAPIDAPI_KEY:
        print("[market] RAPIDAPI_KEY not set — skipping risk assets")
        return {}

    url = "https://yahoo-finance166.p.rapidapi.com/api/market/get-quote-v2"
    headers = {
        "x-rapidapi-key":  RAPIDAPI_KEY,
        "x-rapidapi-host": "yahoo-finance166.p.rapidapi.com"
    }
    params = {
        "symbols": ",".join(RISK_ASSETS.keys()),
        "fields":  "quoteSummary"
    }

    try:
        print("[market] Fetching risk assets from RapidAPI...")
        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        assets = {}
        results = data.get("quoteResponse", {}).get("result", [])
        for item in results:
            symbol = item.get("symbol")
            if symbol not in RISK_ASSETS:
                continue
            summary = item.get("quoteSummary", {}).get("summaryDetail", {})
            assets[RISK_ASSETS[symbol]] = {
                "price":               item.get("regularMarketPrice"),
                "change_pct":          item.get("regularMarketChangePercent"),
                "fifty_day_avg":       summary.get("fiftyDayAverage"),
                "two_hundred_day_avg": summary.get("twoHundredDayAverage"),
                "fifty_two_week_high": summary.get("fiftyTwoWeekHigh"),
                "fifty_two_week_low":  summary.get("fiftyTwoWeekLow"),
            }

        print(f"[market] Risk assets: {len(assets)}/{len(RISK_ASSETS)} fetched")
        return assets

    except Exception as e:
        print(f"[market] RapidAPI error: {e}")
        return {}


# =============================================================================
# SECTION 2: PERPLEXITY #1 — MACRO & FED POLICY
# =============================================================================

def fetch_macro_and_fed() -> str:
    """Fetch macro data and Fed policy via Perplexity (OpenRouter)."""
    if not OPENROUTER_KEY:
        print("[market] OPENROUTER_API_KEY not set — skipping macro data")
        return "Macro data unavailable (no API key)."

    now          = datetime.now(timezone.utc)
    current_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%Y-%m-%d %H:%M:%S UTC")

    prompt = f"""You are a forex market analyst. Current time: {current_time} (today is {current_date}).

Search authoritative sources and provide concise, current macro intelligence relevant to forex trading:

=== PART 1: KEY MACRO DATA ===
Prioritize: Investing.com, Federal Reserve (federalreserve.gov), BLS, BEA

Provide CURRENT readings (with date) and recent trend for:
1. Fed Funds Rate - current target rate + stance (hawkish/dovish/neutral) + next FOMC date
2. US CPI - headline and core, latest MoM and YoY readings, trend
3. US Unemployment / NFP - latest non-farm payrolls and unemployment rate
4. US GDP - latest quarterly reading, direction
5. ISM PMI - Manufacturing and Services, latest readings

For each: current value, previous value, trend direction (rising/falling/stable), brief market implication.

=== PART 2: FED POLICY & RATE EXPECTATIONS ===
- Current Fed stance and forward guidance
- Market-implied rate expectations for next 2-3 FOMC meetings
- Divergence between Fed guidance and market pricing (in basis points)
- How this affects DXY and major forex pairs (EUR/USD, GBP/USD outlook)

=== PART 3: MAJOR FOREX PAIRS FUNDAMENTAL OUTLOOK ===
Brief fundamental bias for:
- EUR/USD: ECB vs Fed policy divergence, eurozone data
- GBP/USD: BOE stance, UK economic conditions
- USD/JPY: BOJ policy, yield differential

Be concise and specific. Focus on what matters for short-term forex trading decisions."""

    try:
        print("[market] Fetching macro/Fed data from Perplexity...")
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model":      "perplexity/sonar-pro",
                "messages":   [{"role": "user", "content": prompt}],
                "max_tokens": 1500
            },
            timeout=30
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        print("[market] Macro/Fed data fetched OK")
        return content

    except Exception as e:
        print(f"[market] Perplexity #1 error: {e}")
        return f"Macro data fetch failed: {e}"


# =============================================================================
# SECTION 3: PERPLEXITY #2 — CATALYSTS & NEWS
# =============================================================================

def fetch_catalysts_and_news() -> str:
    """Fetch upcoming economic events and breaking news via Perplexity (OpenRouter)."""
    if not OPENROUTER_KEY:
        print("[market] OPENROUTER_API_KEY not set — skipping catalysts")
        return "Catalyst data unavailable (no API key)."

    now          = datetime.now(timezone.utc)
    current_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%Y-%m-%d %H:%M:%S UTC")

    prompt = f"""You are a forex market analyst. Current time: {current_time} (today is {current_date}).

Search authoritative sources and provide:

=== PART 1: TODAY'S MARKET-MOVING NEWS ===
Top 3-5 news items from TODAY ({current_date}) that affect forex markets:
- Central bank decisions or speeches (Fed, ECB, BOE, BOJ)
- Major economic data releases
- Geopolitical events affecting USD, EUR, GBP, JPY
Format: [Time UTC] Source - Headline. Brief market impact (1 sentence).

=== PART 2: UPCOMING ECONOMIC CALENDAR (Next 48 Hours) ===
High and medium impact events only. Use Investing.com or ForexFactory calendar.
For each event:
- Date/Time (UTC)
- Event name
- Currency affected
- Impact: HIGH / MEDIUM
- Forecast vs previous (if available)
- Why it matters for forex

=== PART 3: RISK ENVIRONMENT ===
- What is driving overall market sentiment right now?
- Any active geopolitical risks affecting safe havens (USD, JPY, Gold, CHF)?
- Any scheduled events this week that could cause significant volatility?

Be brief and actionable. A forex trader is reading this before entering trades."""

    try:
        print("[market] Fetching catalysts/news from Perplexity...")
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model":      "perplexity/sonar-pro",
                "messages":   [{"role": "user", "content": prompt}],
                "max_tokens": 1500
            },
            timeout=30
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        print("[market] Catalysts/news fetched OK")
        return content

    except Exception as e:
        print(f"[market] Perplexity #2 error: {e}")
        return f"Catalyst data fetch failed: {e}"


# =============================================================================
# SECTION 4: SYNTHESIS — structured forex market intelligence
# Collates all raw data into a clean JSON dict saved to the DB.
# GPT-4o reads the synthesis from DB — never the raw text.
# =============================================================================

SYNTHESIS_SYSTEM_PROMPT = """You are an elite institutional forex market analyst.

You will receive raw market data (risk asset prices, macro text, news/catalyst text).
Your job is to synthesize it into clean, structured intelligence for a forex trading AI.

CRITICAL JSON RULES:
- Output ONLY valid JSON — no markdown, no code blocks, no text outside the JSON
- All newlines inside string values MUST be escaped as \\n
- Do not use literal line breaks inside any string value

Output this exact structure:
{
  "headline": "One punchy sentence capturing the current market environment",

  "market_regime": "One of: Goldilocks | Late Cycle | Reflation | Stagflation | Soft Landing | Hard Landing | Deflation | Geopolitical Stress",

  "risk_profile": "risk-on | risk-off | transitioning",

  "market_summary": "300-400 word flowing analysis. Cover: current regime and why, risk-on/off evidence (VIX, DXY, Gold, yields), Fed stance vs market pricing, what this means for forex broadly, intermarket relationships driving flows. Use \\n\\n between paragraphs.",

  "dxy_outlook": "2-3 sentences: DXY direction, key drivers, near-term bias (bullish/bearish/neutral)",

  "forex_outlook": {
    "EURUSD": "bias (BULLISH/BEARISH/NEUTRAL) + 1-2 sentence reasoning from ECB/Fed divergence and macro",
    "GBPUSD": "bias + reasoning from BOE stance and UK data",
    "USDJPY": "bias + reasoning from BOJ policy and yield differential"
  },

  "key_takeaways": [
    "Most critical insight for trading today #1",
    "Most critical insight for trading today #2",
    "Most critical insight for trading today #3"
  ],

  "upcoming_catalysts": "Bullet list of HIGH/MEDIUM impact events next 48h with dates/times. Use \\n for line breaks.",

  "risk_environment": "2 sentences: current risk environment and any active event risks to be aware of."
}"""


def synthesize_market_data(raw_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pass all raw market data to GPT-4o-mini for synthesis into structured
    forex market intelligence.

    Returns the parsed intelligence dict, or a fallback error dict on failure.
    """
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        print("[market] OPENAI_API_KEY not set — skipping synthesis")
        return {"synthesis_error": "OPENAI_API_KEY not set", "raw_data": raw_data}

    user_prompt = f"""Synthesize this market data into the required JSON intelligence report:

TIMESTAMP: {raw_data.get('timestamp')}

=== RISK ASSET PRICES ===
{json.dumps(raw_data.get('risk_assets', {}), indent=2)}

=== MACRO & FED POLICY (Perplexity) ===
{raw_data.get('macro_and_fed', 'Not available')}

=== NEWS, CATALYSTS & RISK ENVIRONMENT (Perplexity) ===
{raw_data.get('catalysts_news', 'Not available')}

Output ONLY valid JSON as specified."""

    try:
        print("[market] Synthesizing market intelligence via GPT-4o-mini...")
        from openai import OpenAI
        client = OpenAI(api_key=openai_key)

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt}
            ],
            max_tokens=2000,
            temperature=0.2
        )

        response_text = response.choices[0].message.content

        # Strip markdown fences if present
        if "```json" in response_text:
            start = response_text.find("```json") + 7
            end   = response_text.find("```", start)
            response_text = response_text[start:end].strip()
        elif "```" in response_text:
            start = response_text.find("```") + 3
            end   = response_text.find("```", start)
            response_text = response_text[start:end].strip()

        match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if match:
            intelligence = json.loads(match.group())
        else:
            intelligence = json.loads(response_text)

        print("[market] Synthesis complete OK")
        return intelligence

    except Exception as e:
        print(f"[market] Synthesis error: {e}")
        return {
            "synthesis_error": str(e),
            "headline":        "Market intelligence synthesis failed",
            "market_summary":  "Raw data was collected but synthesis failed. Check logs.",
            "raw_data":        raw_data
        }


# =============================================================================
# MAIN FUNCTION — called by brain.py
# =============================================================================

def get_market_data(symbol: str) -> Dict[str, Any]:
    """
    Collect and synthesize full market intelligence for a trading symbol.

    Step 1 — Parallel fetch (all independent):
      - RapidAPI  : live risk asset prices
      - Perplexity: macro/Fed data
      - Perplexity: news/catalysts

    Step 2 — Synthesis (GPT-4o-mini):
      Collates all raw data into structured forex intelligence JSON.

    Step 3 — Return synthesis dict.
      brain.py saves this to DB as market_data_note.
      Every intraday AI decision reads the synthesis from DB — no re-fetching.

    Args:
        symbol: Trading symbol being analysed (e.g. "EURUSD") — for logging only,
                market intelligence is global across all forex pairs.

    Returns:
        Structured market intelligence dict (synthesis output).
    """
    print(f"\n[market] Starting market data collection for {symbol}...")
    timestamp = datetime.now(timezone.utc).isoformat()

    # --- Step 1: Parallel fetch ---
    risk_assets     = {}
    macro_and_fed   = ""
    catalysts_news  = ""

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_assets    = executor.submit(fetch_risk_assets)
        future_macro     = executor.submit(fetch_macro_and_fed)
        future_catalysts = executor.submit(fetch_catalysts_and_news)

        for future in as_completed([future_assets, future_macro, future_catalysts]):
            if future is future_assets:
                try:
                    risk_assets = future.result()
                except Exception as e:
                    print(f"[market] Risk assets error: {e}")

            elif future is future_macro:
                try:
                    macro_and_fed = future.result()
                except Exception as e:
                    print(f"[market] Macro error: {e}")

            elif future is future_catalysts:
                try:
                    catalysts_news = future.result()
                except Exception as e:
                    print(f"[market] Catalysts error: {e}")

    raw_data = {
        "timestamp":      timestamp,
        "symbol":         symbol,
        "risk_assets":    risk_assets,
        "macro_and_fed":  macro_and_fed,
        "catalysts_news": catalysts_news,
    }

    # --- Step 2: Synthesize ---
    intelligence = synthesize_market_data(raw_data)

    # Attach timestamp so brain.py cache logic can check freshness
    intelligence["_fetched_at"] = timestamp

    print(f"[market] Market intelligence ready for {symbol}")
    return intelligence
