"""
Market Data Service

Collects real-time market context for the trading AI:
  1. RapidAPI (Yahoo Finance) - key risk assets: VIX, DXY, yields, gold, oil, indices
  2. Perplexity #1 - macro data: CPI, NFP, Fed policy, rate expectations
  3. Perplexity #2 - upcoming catalysts: economic calendar, news, geopolitics

Called by brain.py:  market_context = get_market_data(symbol)
Result is JSON-dumped into the GPT Vision context string.

APIs used (already in .env):
  RAPIDAPI_KEY     - Yahoo Finance via RapidAPI
  OPENROUTER_API_KEY - Perplexity via OpenRouter
"""

import requests
import os
from datetime import datetime, timezone
from typing import Dict, Any
from dotenv import load_dotenv

load_dotenv()

RAPIDAPI_KEY      = os.getenv("RAPIDAPI_KEY")
OPENROUTER_KEY    = os.getenv("OPENROUTER_API_KEY")


# =============================================================================
# SECTION 1: RAPIDAPI - KEY RISK ASSETS
# These are the assets most relevant to forex trading decisions.
# =============================================================================

# Symbols to fetch and their labels
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
    """
    Fetch key risk asset prices via RapidAPI (Yahoo Finance).

    Returns a dict of asset name -> price data, or empty dict on failure.
    """
    if not RAPIDAPI_KEY:
        print("[market_data] RAPIDAPI_KEY not set - skipping risk assets")
        return {}

    url = "https://yahoo-finance166.p.rapidapi.com/api/market/get-quote-v2"
    headers = {
        "x-rapidapi-key":  RAPIDAPI_KEY,
        "x-rapidapi-host": "yahoo-finance166.p.rapidapi.com"
    }
    params = {
        "symbols": ",".join(RISK_ASSETS.keys()),
        "fields": "quoteSummary"
    }

    try:
        print("[market_data] Fetching risk assets from RapidAPI...")
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
                "price":              item.get("regularMarketPrice"),
                "change_pct":         item.get("regularMarketChangePercent"),
                "fifty_day_avg":      summary.get("fiftyDayAverage"),
                "two_hundred_day_avg": summary.get("twoHundredDayAverage"),
                "fifty_two_week_high": summary.get("fiftyTwoWeekHigh"),
                "fifty_two_week_low":  summary.get("fiftyTwoWeekLow"),
            }

        print(f"[market_data] Risk assets fetched: {len(assets)}/{len(RISK_ASSETS)}")
        return assets

    except Exception as e:
        print(f"[market_data] RapidAPI error: {e}")
        return {}


# =============================================================================
# SECTION 2: PERPLEXITY #1 - MACRO & FED POLICY
# =============================================================================

def fetch_macro_and_fed() -> str:
    """
    Fetch macroeconomic data and Fed policy via Perplexity (OpenRouter).

    Returns raw text string for GPT to read, or error message on failure.
    """
    if not OPENROUTER_KEY:
        print("[market_data] OPENROUTER_API_KEY not set - skipping macro data")
        return "Macro data unavailable (no API key)."

    now = datetime.now(timezone.utc)
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
- Market-implied rate expectations for next 2-3 FOMC meetings (from FedWatch or Investing.com)
- Divergence between Fed guidance and market pricing (in basis points)
- How this affects DXY and major forex pairs (EUR/USD, GBP/USD outlook)

=== PART 3: MAJOR FOREX PAIRS FUNDAMENTAL OUTLOOK ===
Brief fundamental bias for:
- EUR/USD: ECB vs Fed policy divergence, eurozone data
- GBP/USD: BOE stance, UK economic conditions
- USD/JPY: BOJ policy, yield differential

Be concise and specific. Focus on what matters for short-term forex trading decisions."""

    try:
        print("[market_data] Fetching macro/Fed data from Perplexity...")
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model": "perplexity/sonar-pro",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1500
            },
            timeout=30
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        print("[market_data] Macro/Fed data fetched OK")
        return content

    except Exception as e:
        print(f"[market_data] Perplexity #1 error: {e}")
        return f"Macro data fetch failed: {e}"


# =============================================================================
# SECTION 3: PERPLEXITY #2 - UPCOMING CATALYSTS & NEWS
# =============================================================================

def fetch_catalysts_and_news() -> str:
    """
    Fetch upcoming economic events and breaking news via Perplexity (OpenRouter).

    Returns raw text string for GPT to read, or error message on failure.
    """
    if not OPENROUTER_KEY:
        print("[market_data] OPENROUTER_API_KEY not set - skipping catalysts")
        return "Catalyst data unavailable (no API key)."

    now = datetime.now(timezone.utc)
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
Current risk-on / risk-off conditions:
- What is driving overall market sentiment right now?
- Any active geopolitical risks affecting safe havens (USD, JPY, Gold, CHF)?
- Are there any scheduled events this week that could cause a significant volatility spike?

Be brief and actionable. A forex trader is reading this before entering trades."""

    try:
        print("[market_data] Fetching catalysts/news from Perplexity...")
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model": "perplexity/sonar-pro",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1500
            },
            timeout=30
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        print("[market_data] Catalysts/news fetched OK")
        return content

    except Exception as e:
        print(f"[market_data] Perplexity #2 error: {e}")
        return f"Catalyst data fetch failed: {e}"


# =============================================================================
# MAIN FUNCTION — called by brain.py
# =============================================================================

def get_market_data(symbol: str) -> Dict[str, Any]:
    """
    Collect full market context for a trading symbol.

    Runs 3 data fetches in sequence:
      1. RapidAPI  - live prices for VIX, DXY, yields, gold, oil, indices
      2. Perplexity - macro data: CPI, NFP, Fed policy, rate expectations
      3. Perplexity - upcoming events, breaking news, risk environment

    Each step is independent - if one fails, the others still run.

    Args:
        symbol: Trading symbol being analysed, e.g. "EURUSD"

    Returns:
        Dict that brain.py JSON-dumps into the GPT Vision context string.
    """
    print(f"\n[market_data] Starting market data collection for {symbol}...")
    timestamp = datetime.now(timezone.utc).isoformat()

    # --- 1. Risk assets ---
    risk_assets = fetch_risk_assets()

    # --- 2. Macro & Fed ---
    macro_and_fed = fetch_macro_and_fed()

    # --- 3. Catalysts & news ---
    catalysts_and_news = fetch_catalysts_and_news()

    print(f"[market_data] Collection complete for {symbol}")

    return {
        "symbol":           symbol,
        "timestamp":        timestamp,
        "risk_assets":      risk_assets,       # Live prices: VIX, DXY, Gold, etc.
        "macro_and_fed":    macro_and_fed,     # Perplexity: CPI, NFP, Fed policy
        "catalysts_news":   catalysts_and_news # Perplexity: calendar, breaking news
    }
