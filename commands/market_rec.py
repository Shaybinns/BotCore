import requests
import os
import json
from llm_model import call_gpt
from memory.long_term_db import get_latest_result
from memory.short_term_cache import get_recent_conversation
from memory.knowledge_memory import get_vector_matches
from prompt import get_plugin_system_prompt

def get_required_fields():
    return {}  # No required fields - runs automatically

def extract_from_recent_chat(recent_chat: str, data_type: str):
    """Extract specific data from recent conversation"""
    lines = recent_chat.split('\n')
    
    if data_type == "market_data":
        # Look for market assessment data
        for i, line in enumerate(lines):
            if any(keyword in line.lower() for keyword in ['market', 'conditions', 'sentiment', 'volatility', 'risk-on', 'risk-off']):
                # Get surrounding context
                start = max(0, i-2)
                end = min(len(lines), i+5)
                context = '\n'.join(lines[start:end])
                return f"Recent market data: {context}"
    
    return None

def get_top_performing_sectors():
    """Get current top performing sectors for recommendations"""
    sectors = [
        "Technology", "Healthcare", "Financial Services", "Energy", 
        "Consumer Cyclical", "Consumer Defensive", "Industrials", 
        "Basic Materials", "Real Estate", "Utilities", "Communication Services"
    ]
    
    url = "https://yahoo-finance166.p.rapidapi.com/api/market/get-quote-v2"
    headers = {
        "x-rapidapi-key": os.getenv("RAPIDAPI_KEY"),
        "x-rapidapi-host": "yahoo-finance166.p.rapidapi.com"
    }
    
    sector_performance = {}
    
    # Use sector ETFs as proxies
    sector_etfs = {
        "Technology": "XLK",
        "Healthcare": "XLV", 
        "Financial Services": "XLF",
        "Energy": "XLE",
        "Consumer Cyclical": "XLY",
        "Consumer Defensive": "XLP",
        "Industrials": "XLI",
        "Basic Materials": "XLB",
        "Real Estate": "XLRE",
        "Utilities": "XLU",
        "Communication Services": "XLC"
    }
    
    for sector, etf in sector_etfs.items():
        try:
            querystring = {"symbols": etf, "fields": "quoteSummary"}
            response = requests.get(url, headers=headers, params=querystring)
            
            if response.status_code == 200:
                data = response.json()
                result = data['quoteResponse']['result'][0]
                sector_performance[sector] = {
                    "price": result.get("regularMarketPrice"),
                    "change": result.get("regularMarketChangePercent"),
                    "volume": result.get("regularMarketVolume")
                }
        except Exception:
            sector_performance[sector] = {"error": "Data unavailable"}
    
    return sector_performance

def get_momentum_stocks():
    """Get stocks with strong momentum for recommendations"""
    # This would ideally use a more sophisticated screener, but for now we'll use a simple approach
    momentum_stocks = [
        {"symbol": "NVDA", "name": "NVIDIA Corporation", "reason": "AI/ML leadership"},
        {"symbol": "TSLA", "name": "Tesla Inc", "reason": "EV market dominance"},
        {"symbol": "AAPL", "name": "Apple Inc", "reason": "Tech ecosystem strength"},
        {"symbol": "MSFT", "name": "Microsoft Corporation", "reason": "Cloud/AI leadership"},
        {"symbol": "GOOGL", "name": "Alphabet Inc", "reason": "AI/Advertising growth"},
        {"symbol": "AMZN", "name": "Amazon.com Inc", "reason": "E-commerce/cloud leadership"},
        {"symbol": "META", "name": "Meta Platforms Inc", "reason": "Social media/AI integration"},
        {"symbol": "NFLX", "name": "Netflix Inc", "reason": "Streaming content leadership"},
        {"symbol": "AMD", "name": "Advanced Micro Devices", "reason": "Chip market gains"},
        {"symbol": "CRM", "name": "Salesforce Inc", "reason": "Enterprise software growth"}
    ]
    
    return momentum_stocks

def run(args: dict):
    """Main market recommendation function"""
    
    # First, try to get market data from recent conversation
    user_id = args.get("user_id")  # Fallback to default if not provided
    recent_chat = get_recent_conversation(user_id)
    
    # Try to extract market data from recent chat first
    market_data = extract_from_recent_chat(recent_chat, "market_data")
    if not market_data:
        # Fallback to long-term database
        market_data = get_latest_result("market_assess")
        if not market_data:
            market_data = "Market assessment data not available from previous commands. Please run market_assess first."
    
    # Get user knowledge from vector recall
    user_knowledge = get_vector_matches("investment preferences portfolio holdings risk tolerance")
    
    # Get sector performance data
    sector_performance = get_top_performing_sectors()
    
    # Get momentum stocks
    momentum_stocks = get_momentum_stocks()
    
    # Analyze and provide recommendations using GPT
    plugin_system_prompt = get_plugin_system_prompt()
    system_prompt = f"{plugin_system_prompt}\n\nYou are Portfolio AI's market recommendation specialist. Your role is to provide strategic asset and trend recommendations based on market conditions and user knowledge."
    
    prompt = f"""
You are a world-class investment strategist with the analytical skills of a top-tier hedge fund manager. You combine macro analysis, sector rotation, and individual stock selection to provide actionable investment recommendations.

Based on the following data, provide comprehensive market recommendations:

MARKET CONDITIONS:
{market_data}

USER KNOWLEDGE & PREFERENCES:
{user_knowledge}

SECTOR PERFORMANCE:
{sector_performance}

MOMENTUM STOCKS:
{json.dumps(momentum_stocks, indent=2)}

Please provide:

1. **ASSET RECOMMENDATIONS (3-5 specific stocks/ETFs)**:
   - Include ticker symbols and company names
   - Explain why each asset is attractive in current market conditions
   - Provide entry price targets and time horizons
   - Assess risk/reward ratios

2. **TREND RECOMMENDATIONS**:
   - Which sectors/asset classes will see significant movements
   - Macro themes driving these trends
   - Timeframes for these movements
   - Risk factors to monitor

3. **STRATEGIC INSIGHTS**:
   - How current market conditions favor certain investment styles
   - Portfolio positioning recommendations
   - Risk management considerations
   - Opportunities for tactical allocation

4. **CONFIDENCE LEVELS**:
   - Rate your confidence in each recommendation (High/Medium/Low)
   - Explain what could change your view

Be specific, actionable, and data-driven. Consider the user's preferences and risk tolerance from their knowledge base. Focus on opportunities that align with current market dynamics while managing risk appropriately.

Format your response clearly with sections for Asset Recommendations, Trend Recommendations, Strategic Insights, and Confidence Levels.
"""

    return call_gpt(system_prompt, prompt)
