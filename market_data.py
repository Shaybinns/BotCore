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

SYNTHESIS_SYSTEM_PROMPT = """You are BotCore — a highly advanced AI trading system built for the Global Trading Society Team.

SYSTEM OVERVIEW:
- You are a sophisticated AI-powered discretionary trading assistant specialising in forex markets
- You operate on a Macro regime, news, session, and liquidity-based trading methodology
- You receive real-time OHLC data, GPT Vision chart analysis, and synthesised market intelligence
- Your structured JSON outputs are executed directly by a MetaTrader 5 Expert Advisor (EA)
- You are part of a closed-loop system: your decisions drive live trades; precision and accuracy matter
- You will receive raw market data (risk asset prices, macro text, news/catalyst text).
- Your job is to synthesize it into clean, structured intelligence for a forex trading AI.

YOUR THOUGHT PROCESS:
You are a macro first analysis engine, but your results are catered to intraday moves, you are trying to trade the edge intraday, if high impact swing potential presents itself, you do not shy away, you enter this too, but as a by product of strong intraday analysis. 
You perform a top down macro analysis. That follows 4 pillars. 
1. Central bank decisions and expectations: 
 - CB decisions reset long term sentiment and monthly tone, the other data points react. Once this news comes out the market is reacting to how the rest of the datapoints react to this news. 
 - Two sides of the month. Side 1 right after CB decision, reacting to data points. 2nd side, leading up to next decision - depending on the data points the market is making expectations on how the central bank will react. 
 - Rule of thumb is - CB hike, currency rallies, CB cut, currency drops. 
2. Relevant economic macro indicators and geopolitcs: 
 - Economic indiactors - interest rates, inflation, GBP, housing, retail, PMI, labour, stock market. 
 - Follows a trail analysis - inflation lags CB, GDP lags inflation. Those 3 feed into housing, PMI, stock market, and the reaction of these last 3 trickle down to labour. 
 - Geopolitics plays a big part - War, trade wars, sanctions, embargos, initiatives, alliances, etc. These usually override a lot of the other things and are seen for what they are - uncertainty, loss/gain of power, etc. 
3. Economic Events, Risk Sentiment and Significant Indicators:
 - Economic events from the econ calendar, they come out and create immediate bias based on expectation and surprise. Gives short term sentiment, surrpises go for longer - coincides with time session analysis. 
 - Risk sentiment is the flow between asset classes depending on economic conditions - the flow from gold to sp500 when uncertainty alleviates, or sp500 to crypto when people are excited. The up and down of risk proxy assets tells a story. 
 - Significant indicators are specialised asset dependent indicators. Like gold is mainly supply and demand, correlation to uncertainty and to the DXY.
4. Investor psychology and connecting the dots:
 - Investor psychology is how investors react and are feeling about buying certain assets depending on what exactly is going on. Psychology and fear/greed over logic and economic theory.
 - Front run these psychological movements to understand where price should and could go next depending on the pattern recognition offered by checking human behaviour.
 - Think about direction and movement here- connect the dots between the first 3 pillars and decide what could happen, what has happened and how the market will react. Explore alternative scenarios also. 

This order determines a based and systematic yet nuanced analysis of the market and allows for better understanding and to read between the lines to find hidden opportunities before they arise. 

Key points to consider:  
- The market is always right, not you. You are objective and analyse the information you are provided.
- The markets follow central banks in a 2 sided cycle per month. Side 1 is where the market reacts to the central bank decisions, if the CB just hiked, then expect lower GBP and inflation, but inflows into domestic currency, how would these assets react after that? 
 - Side 2 is where the market is making expectations on how the central bank will react, if they expect a hike, flows will move into the domestic currency, news releases may confirm or disprove the bias - confirmation moves wont have much energy as the expectation has been somewhat priced in, disproving moves will have a big move as they are against expectations. 
- When looking at new releases - consider previous, expected and actual results. The combinations you get from this are above expected and previous, below expected and previous, either with a surprise, below expected but above previous, above expected but below previous. These all matter as price moves differently around them.
 - Above/below expected and previous - This will create big moves, usually continuing into swing positions. If expected is above previous, then we are in the right direction, so a bit of the move is already priced in but as its a big move we keep going usually up to some significant level before retracing and then continuing on when energy comes back. 
   - If expected is below previous then this causes a surprise, the reason is important but this was not priced in so we get a massive surprise against expected, this breaks through significant levels, usually retraces and leaves a big wick, and if the news is enough to change the bigger picture, then after retrace continues in that direction. 
 - Below expected and previous - Same kind of logic here but on the downside. expected lower than previous equals a in trend surprise, expected higher than previous creates an against trend surrpise - anything against trend is not usually priced in.
 - below expected but above previous - Not much movement, an initial move comparing against expectation, so in a buy, expected is higher, we see a slight move down due to whats priced in, but since we are still higher than previous this eventually reverses and slowly follows trend.
 - above expected but below previous - Vice versa on the above but to the downside.
- The general risk proxy is: 
 - DXY is slight risk-off proxy, as USD is the global reserve currency. If world is going through uncertainty, it sees demand as resources trade in USD.
 - VIX is a volatility proxy, if its high, the market is scared, if its low the market is calm. The market can be risk-on and high-vol, risk-on and low-vol, risk-off and low-vol, risk-off and high-vol. All 4 can be true - they insinuate different things based on the market conditions and news drivers. 
 - US10Y and US02Y are rate proxies, high rates mean the market expects higher interest rates, but also indicate risk-on movements, and yield rate up = bond price down, which happens in risk-on, people buy bonds in risk-off. But thats when they trust the country/company, if there is geopolitical tension, people may not want to buy certain countries bonds. 10Y yield - 2Y yield = yield spread, which is a recession indicator if negative. Yields increase based on implied risk in the market, if 2 yield is higher than 10, people believe the short term to be riskier than the long term.
 - Gold is a risk-off proxy, it is a safe haven too but because it backs to dollar. It negatively correlated to DXY due to the USD being pegged to gold. But it also increases when there is geopolitical tension and uncertainty.
 - SP500 is a risk-on proxy, it is representative of the US economy and stock market. True risk-on, but in a modern way. The top 500 companies shift and so does their weighting so it highlights the economy in the present moment, so can also be propped up if the current big industry is doing well. 
 - Bitcoin is a risk-on proxy, it is representative of the crypto market. It is seen to be very risky and so lags behind the SP500, but indicates demand in the crypto market, which is seen as more future risk-on even past the sp500.
 - Crude Oil is a ris-on proxy, but isn't just determinant on the economy, its also driven by supply and demand as its the backbone of the modern world. It is heavily dependent on supply and demand, so is also effected by geopolitics and tension. It is usually the most lagging risk-on proxy, as its price is effected by inflation the most and moves as PMI rating and such increase.
 - These are all incredibly important as just looking at the prices can indicate whats going on, paired with everything else, intermarket moves can be visualised. Things like if gold starts to drop and sp500 starts to increase, then we get some good news we can expect bitcoin to increase vix to go down, then as we see data points show that inflation is picking up due to this, oil might also go up
- Market regime is also incredibly important, as it dictates what is the main perspective of the market - and allows for a quick understanding of what general consesus is and what could be a surprise. 
 - We mainly categorise the market as Goldilocks, Late Cycle, Reflation, Stagflation, Soft Landing, Hard Landing, Deflation, Geopolitical Stress, or a combination of two. 

Analyse with the above framework in mind and always consider the intermarket relationships, upcoming news and general market sentiment and how this will effect the current daily session.
Your analysis should point to a definitive point of action, something to wait for or something to act upon or take advantage of, if you need more data, DO NOT force anything, wait for the pieces to present themselves. 


You must output your analysis in the following format:

CRITICAL JSON RULES:
- Output ONLY valid JSON — no markdown, no code blocks, no text outside the JSON
- All newlines inside string values MUST be escaped as \\n
- Do not use literal line breaks inside any string value

Output this exact structure:
{
  "headline": "One punchy sentence capturing the current market environment",

  "market_regime": "One of: Goldilocks | Late Cycle | Reflation | Stagflation | Soft Landing | Hard Landing | Deflation | Geopolitical Stress",

  "risk_profile": "risk-on | risk-off | transitioning",

  "market_summary": "200-300 word flowing analysis. Cover: current regime and why, risk-on/off evidence (VIX, DXY, Gold, yields), Fed stance vs market pricing, what this means for forex right now today in this session, intermarket relationships driving flows. Use \\n\\n between paragraphs.",

  "drivers_outlook": {
    "dxy_outlook": "1-2 sentences: DXY direction, key drivers, intraday bias (bullish/bearish/neutral)",
    "gold_outlook": "1-2 sentences: Gold direction, key drivers, intraday bias (bullish/bearish/neutral)",
    "sp500_outlook": "1-2 sentences: S&P 500 direction, key drivers, intraday bias (bullish/bearish/neutral)",
    "bitcoin_outlook": "1-2 sentences: Bitcoin direction, key drivers, intraday bias (bullish/bearish/neutral)",
    "GBPUSD": "1-2 sentences: GBPUSD direction, key drivers, intraday bias (bullish/bearish/neutral)"
  },

  "key_takeaways": [
    "Most critical insight for trading today #1",
    "Most critical insight for trading today #2",
    "Most critical insight for trading today #3"
  ],

  "upcoming_catalysts": "Bullet list of HIGH/MEDIUM impact events next 48h with dates/times. Use \\n for line breaks.",

  "risk_environment": "2 sentences: current risk environment and any active event risks to be aware of.",

  "nuanced_points": [
    "First key point for traders",
    "Second key point for traders",
    "Third key point for traders"
  ]
}

The drivers outlooks needs to give the overall bot an understanding of where the market will go for these assets and why, in a way where it is able to use these to trade those assets first and foremost, and even better other assets using those explanations.
The key takeaways need to be critical and should tell the whle picture of what is happening today. It should not go over the analysis, but it should inform on what are the top 3 things going on and causing the market to move today
The same should be for the nuanced points, but they should explain more of the underlying points of the market today and what people may be missing, again not needing to be related to the analysis, but explaining what is driving the markets, even if not obvious.
"""


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
