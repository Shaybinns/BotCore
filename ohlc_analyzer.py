"""
OHLC Data Analyzer

Analyzes OHLC data for:
- Fair Value Gaps (FVG)
- Break of Structure (BOS)
- Imbalances
- Price action patterns
"""

from typing import Dict, List, Any, Optional


def analyze_ohlc_data(
    ohlc_data: Dict[str, Any],
    locked_levels: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Analyze OHLC data for trading patterns.
    
    Args:
        ohlc_data: OHLC data from MT5 (multiple timeframes)
        locked_levels: Currently locked levels/zones
    
    Returns:
        Analysis results with FVGs, BOS, imbalances, etc.
    """
    analysis = {
        "fvgs": [],
        "bos_signals": [],
        "imbalances": [],
        "price_action": {},
        "level_interactions": []
    }
    
    # Extract timeframe data
    h1_data = ohlc_data.get("H1", [])
    m15_data = ohlc_data.get("M15", [])
    m5_data = ohlc_data.get("M5", [])
    m1_data = ohlc_data.get("M1", [])
    current_price = ohlc_data.get("current_price")
    
    # Analyze each timeframe
    if h1_data:
        analysis["fvgs"].extend(_detect_fvgs(h1_data, "H1"))
        analysis["bos_signals"].extend(_detect_bos(h1_data, "H1"))
        analysis["imbalances"].extend(_detect_imbalances(h1_data, "H1"))
    
    if m15_data:
        analysis["fvgs"].extend(_detect_fvgs(m15_data, "M15"))
        analysis["bos_signals"].extend(_detect_bos(m15_data, "M15"))
    
    if m5_data:
        analysis["fvgs"].extend(_detect_fvgs(m5_data, "M5"))
        analysis["bos_signals"].extend(_detect_bos(m5_data, "M5"))
    
    if m1_data:
        analysis["fvgs"].extend(_detect_fvgs(m1_data, "M1"))
    
    # Check level interactions
    if current_price and locked_levels:
        analysis["level_interactions"] = _check_level_interactions(
            current_price, locked_levels, ohlc_data
        )
    
    # Price action summary
    analysis["price_action"] = _analyze_price_action(ohlc_data)
    
    return analysis


def _detect_fvgs(candles: List[Dict[str, Any]], timeframe: str) -> List[Dict[str, Any]]:
    """
    Detect Fair Value Gaps (FVGs).
    
    FVG: Gap between high of previous candle and low of next candle
    (for bullish) or gap between low of previous and high of next (for bearish).
    """
    fvgs = []
    
    if len(candles) < 3:
        return fvgs
    
    for i in range(1, len(candles) - 1):
        prev_candle = candles[i - 1]
        curr_candle = candles[i]
        next_candle = candles[i + 1]
        
        # Bullish FVG: gap between prev high and next low
        if prev_candle.get("high") < next_candle.get("low"):
            fvgs.append({
                "type": "bullish",
                "timeframe": timeframe,
                "top": next_candle.get("low"),
                "bottom": prev_candle.get("high"),
                "timestamp": curr_candle.get("time"),
                "filled": False
            })
        
        # Bearish FVG: gap between prev low and next high
        elif prev_candle.get("low") > next_candle.get("high"):
            fvgs.append({
                "type": "bearish",
                "timeframe": timeframe,
                "top": prev_candle.get("low"),
                "bottom": next_candle.get("high"),
                "timestamp": curr_candle.get("time"),
                "filled": False
            })
    
    return fvgs


def _detect_bos(candles: List[Dict[str, Any]], timeframe: str) -> List[Dict[str, Any]]:
    """
    Detect Break of Structure (BOS).
    
    BOS: First candle that breaks previous swing high/low.
    """
    bos_signals = []
    
    if len(candles) < 5:
        return bos_signals
    
    # Find swing highs and lows
    swings = _find_swings(candles)
    
    for i, swing in enumerate(swings):
        if i == 0:
            continue
        
        prev_swing = swings[i - 1]
        
        # Bullish BOS: break above previous swing high
        if swing["type"] == "high" and prev_swing.get("high"):
            if swing["high"] > prev_swing["high"]:
                bos_signals.append({
                    "type": "bullish",
                    "timeframe": timeframe,
                    "price": swing["high"],
                    "timestamp": swing["time"],
                    "previous_swing": prev_swing["high"]
                })
        
        # Bearish BOS: break below previous swing low
        elif swing["type"] == "low" and prev_swing.get("low"):
            if swing["low"] < prev_swing["low"]:
                bos_signals.append({
                    "type": "bearish",
                    "timeframe": timeframe,
                    "price": swing["low"],
                    "timestamp": swing["time"],
                    "previous_swing": prev_swing["low"]
                })
    
    return bos_signals


def _detect_imbalances(candles: List[Dict[str, Any]], timeframe: str) -> List[Dict[str, Any]]:
    """
    Detect order flow imbalances.
    
    Imbalance: Large candle with small wicks indicating strong directional move.
    """
    imbalances = []
    
    for candle in candles:
        body_size = abs(candle.get("close", 0) - candle.get("open", 0))
        total_range = candle.get("high", 0) - candle.get("low", 0)
        
        if total_range > 0:
            body_ratio = body_size / total_range
            
            # Strong imbalance if body is > 70% of range
            if body_ratio > 0.7:
                imbalances.append({
                    "type": "bullish" if candle.get("close") > candle.get("open") else "bearish",
                    "timeframe": timeframe,
                    "price": candle.get("close"),
                    "timestamp": candle.get("time"),
                    "strength": body_ratio
                })
    
    return imbalances


def _find_swings(candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Find swing highs and lows in candle data."""
    swings = []
    
    if len(candles) < 3:
        return swings
    
    for i in range(1, len(candles) - 1):
        prev = candles[i - 1]
        curr = candles[i]
        next_c = candles[i + 1]
        
        # Swing high
        if curr.get("high") > prev.get("high") and curr.get("high") > next_c.get("high"):
            swings.append({
                "type": "high",
                "high": curr.get("high"),
                "time": curr.get("time")
            })
        
        # Swing low
        if curr.get("low") < prev.get("low") and curr.get("low") < next_c.get("low"):
            swings.append({
                "type": "low",
                "low": curr.get("low"),
                "time": curr.get("time")
            })
    
    return swings


def _check_level_interactions(
    current_price: float,
    locked_levels: List[Dict[str, Any]],
    ohlc_data: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Check if price is interacting with locked levels."""
    interactions = []
    
    for level in locked_levels:
        level_price = level.get("price")
        if not level_price:
            continue
        
        # Calculate distance in pips (rough estimate for forex)
        distance = abs(current_price - level_price) * 10000
        
        if distance < 20:  # Within 20 pips
            interactions.append({
                "level_id": level.get("id"),
                "level_price": level_price,
                "current_price": current_price,
                "distance_pips": distance,
                "status": "near" if distance < 10 else "approaching"
            })
    
    return interactions


def _analyze_price_action(ohlc_data: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze overall price action."""
    current_price = ohlc_data.get("current_price")
    h1_data = ohlc_data.get("H1", [])
    
    if not h1_data or not current_price:
        return {}
    
    # Get recent H1 candles
    recent_candles = h1_data[-10:] if len(h1_data) >= 10 else h1_data
    
    # Calculate trend
    if len(recent_candles) >= 2:
        first_close = recent_candles[0].get("close")
        last_close = recent_candles[-1].get("close")
        trend = "bullish" if last_close > first_close else "bearish"
    else:
        trend = "neutral"
    
    return {
        "current_price": current_price,
        "trend": trend,
        "recent_candles_count": len(recent_candles)
    }

