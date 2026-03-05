"""
OHLC Analyzer

Converts raw candle arrays into structured technical analysis for GPT-4o.

Candle arrays arrive newest-first (index 0 = most recent bar).
Each candle: {"time": int, "open": float, "high": float, "low": float, "close": float, "volume": int}

Output feeds directly into the === OHLC DATA ANALYSIS === section of the trading context.
"""

from typing import Dict, Any, List, Optional, Tuple
import math


# =============================================================================
# TIMEFRAME ORDERING  (highest → lowest, used for trend priority)
# =============================================================================

_TF_RANK = {
    "1W_DATA": 7, "W1_DATA": 7,
    "1D_DATA": 6, "D1_DATA": 6,
    "4h_DATA": 5, "H4_DATA": 5,
    "1h_DATA": 4, "H1_DATA": 4,
    "M15_DATA": 3,
    "M5_DATA":  2,
    "M1_DATA":  1,
}


def _tf_rank(key: str) -> int:
    return _TF_RANK.get(key.upper(), 0) or _TF_RANK.get(key, 0)


# =============================================================================
# LOW-LEVEL HELPERS
# =============================================================================

def _ema(values: List[float], period: int) -> List[float]:
    """Exponential moving average, index 0 = oldest value."""
    if len(values) < period:
        return [sum(values) / len(values)] * len(values)
    k = 2 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _atr(candles: List[Dict], period: int = 14) -> float:
    """
    Average True Range over `period` bars.
    Candles are newest-first; we reverse internally.
    """
    rev = list(reversed(candles))
    trs = []
    for i in range(1, len(rev)):
        high  = rev[i]["high"]
        low   = rev[i]["low"]
        prev_c = rev[i - 1]["close"]
        trs.append(max(high - low, abs(high - prev_c), abs(low - prev_c)))
    if not trs:
        return 0.0
    window = trs[-period:] if len(trs) >= period else trs
    return sum(window) / len(window)


def _swing_points(candles: List[Dict], strength: int = 3) -> Tuple[List[Dict], List[Dict]]:
    """
    Detect swing highs and swing lows.
    strength = number of bars each side that must be lower/higher.
    Candles are newest-first; we work on reversed list and translate indices back.
    Returns (swing_highs, swing_lows) each as list of {"price": float, "index": int}
    Both lists are sorted newest-first (smallest index first in original array).
    """
    rev = list(reversed(candles))
    n = len(rev)
    highs, lows = [], []

    for i in range(strength, n - strength):
        h = rev[i]["high"]
        l = rev[i]["low"]

        if all(rev[i]["high"] >= rev[j]["high"] for j in range(i - strength, i + strength + 1) if j != i):
            highs.append({"price": round(h, 5), "bar_index": (n - 1 - i)})

        if all(rev[i]["low"] <= rev[j]["low"] for j in range(i - strength, i + strength + 1) if j != i):
            lows.append({"price": round(l, 5), "bar_index": (n - 1 - i)})

    # Sort by bar_index ascending (newest = smallest index)
    highs.sort(key=lambda x: x["bar_index"])
    lows.sort(key=lambda x: x["bar_index"])
    return highs, lows


def _detect_fvgs(candles: List[Dict], max_fvgs: int = 5) -> List[Dict]:
    """
    Fair Value Gaps: a 3-candle formation where candle[i] and candle[i+2]
    do not overlap, leaving a price gap (imbalance).

    Candles newest-first. We scan from index 0 outward.
    A bullish FVG:  candles[i].low  > candles[i+2].high  (gap up)
    A bearish FVG:  candles[i].high < candles[i+2].low   (gap down)

    Returns newest FVGs first, capped at max_fvgs.
    """
    fvgs = []
    for i in range(len(candles) - 2):
        c0 = candles[i]      # newest of the three
        c2 = candles[i + 2]  # oldest of the three

        if c0["low"] > c2["high"]:
            fvgs.append({
                "type":      "BULLISH",
                "top":       round(c0["low"],  5),
                "bottom":    round(c2["high"], 5),
                "bar_index": i
            })
        elif c0["high"] < c2["low"]:
            fvgs.append({
                "type":      "BEARISH",
                "top":       round(c2["low"],  5),
                "bottom":    round(c0["high"], 5),
                "bar_index": i
            })

        if len(fvgs) >= max_fvgs:
            break

    return fvgs


def _detect_trend(candles: List[Dict], period: int = 20) -> Dict[str, Any]:
    """
    Determine trend direction using EMA relationship and recent structure.
    Returns dict with direction, ema_current, price_vs_ema.
    """
    if len(candles) < 3:
        return {"direction": "NEUTRAL", "ema": None, "price_vs_ema": "at"}

    closes_oldest_first = [c["close"] for c in reversed(candles)]
    ema_vals = _ema(closes_oldest_first, min(period, len(closes_oldest_first)))
    ema_now  = round(ema_vals[-1], 5)

    current = candles[0]["close"]
    diff_pct = (current - ema_now) / ema_now * 100 if ema_now else 0

    if diff_pct > 0.05:
        direction = "BULLISH"
        vs_ema    = "above"
    elif diff_pct < -0.05:
        direction = "BEARISH"
        vs_ema    = "below"
    else:
        direction = "NEUTRAL"
        vs_ema    = "at"

    # Confirm with recent swing structure (higher highs/lows or lower highs/lows)
    if len(candles) >= 10:
        mid = len(candles) // 2
        recent_high = max(c["high"] for c in candles[:mid])
        older_high  = max(c["high"] for c in candles[mid:])
        recent_low  = min(c["low"]  for c in candles[:mid])
        older_low   = min(c["low"]  for c in candles[mid:])

        if recent_high > older_high and recent_low > older_low:
            direction = "BULLISH"
        elif recent_high < older_high and recent_low < older_low:
            direction = "BEARISH"

    return {
        "direction":   direction,
        "ema":         ema_now,
        "price_vs_ema": vs_ema
    }


def _detect_bos(candles: List[Dict], swing_highs: List[Dict], swing_lows: List[Dict]) -> List[Dict]:
    """
    Break of Structure: current price (candles[0].close) has broken through
    the most recent significant swing high or low.
    Returns a list of recent BOS events (newest first, max 3).
    """
    if not candles:
        return []

    current = candles[0]["close"]
    bos_events = []

    # Check if we've broken above a recent swing high
    for sh in swing_highs[:5]:
        if current > sh["price"]:
            bos_events.append({
                "type":      "BULLISH_BOS",
                "level":     sh["price"],
                "bar_index": sh["bar_index"],
                "note":      "Price broke above swing high"
            })
            break

    # Check if we've broken below a recent swing low
    for sl in swing_lows[:5]:
        if current < sl["price"]:
            bos_events.append({
                "type":      "BEARISH_BOS",
                "level":     sl["price"],
                "bar_index": sl["bar_index"],
                "note":      "Price broke below swing low"
            })
            break

    return bos_events[:3]


def _recent_candles_summary(candles: List[Dict], n: int = 5) -> List[Dict]:
    """Describe the last n candles in human-readable terms."""
    summary = []
    for i, c in enumerate(candles[:n]):
        body   = c["close"] - c["open"]
        range_ = c["high"] - c["low"]
        body_pct = abs(body) / range_ * 100 if range_ > 0 else 0

        if body > 0:
            candle_type = "bullish"
        elif body < 0:
            candle_type = "bearish"
        else:
            candle_type = "doji"

        if body_pct > 70:
            candle_type = "strong " + candle_type
        elif body_pct < 25:
            candle_type = "doji/indecision"

        summary.append({
            "bar":    i,
            "open":   round(c["open"],  5),
            "high":   round(c["high"],  5),
            "low":    round(c["low"],   5),
            "close":  round(c["close"], 5),
            "type":   candle_type,
            "volume": c.get("volume", 0)
        })

    return summary


# =============================================================================
# PER-TIMEFRAME ANALYSIS
# =============================================================================

def _analyze_timeframe(tf: str, candles: List[Dict]) -> Dict[str, Any]:
    """Full analysis for a single timeframe."""
    if not candles:
        return {"error": "no data"}

    current_price = candles[0]["close"]
    period_high   = max(c["high"] for c in candles)
    period_low    = min(c["low"]  for c in candles)
    atr_val       = _atr(candles)

    trend_info  = _detect_trend(candles)
    swing_h, swing_l = _swing_points(candles, strength=3)
    fvgs        = _detect_fvgs(candles, max_fvgs=5)
    bos         = _detect_bos(candles, swing_h, swing_l)
    recent      = _recent_candles_summary(candles, n=5)

    # Nearest swing levels above and below current price
    nearest_resistance = next(
        (sh["price"] for sh in sorted(swing_h, key=lambda x: x["price"])
         if sh["price"] > current_price), None
    )
    nearest_support = next(
        (sl["price"] for sl in sorted(swing_l, key=lambda x: x["price"], reverse=True)
         if sl["price"] < current_price), None
    )

    return {
        "candle_count":        len(candles),
        "current_price":       round(current_price, 5),
        "period_high":         round(period_high,   5),
        "period_low":          round(period_low,    5),
        "atr":                 round(atr_val,        5),
        "atr_pips":            round(atr_val / 0.0001, 1),
        "trend":               trend_info,
        "nearest_resistance":  nearest_resistance,
        "nearest_support":     nearest_support,
        "swing_highs":         swing_h[:5],
        "swing_lows":          swing_l[:5],
        "fair_value_gaps":     fvgs,
        "break_of_structure":  bos,
        "recent_candles":      recent,
    }


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def analyze_ohlc_data(ohlc_data: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """
    Analyze OHLC data for all timeframes and return structured technical analysis.

    Args:
        ohlc_data: {"1h_DATA": [...], "4h_DATA": [...], "1D_DATA": [...], "1W_DATA": [...]}
                   Candles are newest-first (index 0 = most recent).

    Returns:
        Structured analysis dict ready for JSON serialization into GPT-4o context.
    """
    result: Dict[str, Any] = {
        "timeframes": {},
        "summary":    {}
    }

    valid_tfs = {
        k: v for k, v in ohlc_data.items()
        if v and len(v) >= 3
    }

    if not valid_tfs:
        return {"error": "no valid OHLC data", "timeframes": {}, "summary": {}}

    for tf, candles in valid_tfs.items():
        try:
            result["timeframes"][tf] = _analyze_timeframe(tf, candles)
        except Exception as e:
            result["timeframes"][tf] = {"error": str(e)}

    # --- Summary: use highest available timeframe for primary trend ---
    sorted_tfs = sorted(valid_tfs.keys(), key=_tf_rank, reverse=True)

    # Current price from lowest timeframe (most granular)
    lowest_tf   = sorted(valid_tfs.keys(), key=_tf_rank)[0]
    current_price = valid_tfs[lowest_tf][0]["close"]

    # Primary trend from highest timeframe
    highest_tf      = sorted_tfs[0]
    primary_tf_data = result["timeframes"].get(highest_tf, {})
    primary_trend   = primary_tf_data.get("trend", {}).get("direction", "NEUTRAL")

    # Confluence: how many timeframes agree on trend direction
    trend_votes = {
        "BULLISH": 0,
        "BEARISH": 0,
        "NEUTRAL": 0
    }
    for tf in sorted_tfs:
        d = result["timeframes"].get(tf, {}).get("trend", {}).get("direction", "NEUTRAL")
        trend_votes[d] = trend_votes.get(d, 0) + 1

    dominant_trend = max(trend_votes, key=trend_votes.get)
    confluence_pct = round(trend_votes[dominant_trend] / len(sorted_tfs) * 100)

    # Collect all key levels across timeframes (deduplicated within 10 pips)
    all_swing_h = []
    all_swing_l = []
    for tf in sorted_tfs[:3]:  # Top 3 timeframes only to keep it clean
        tf_data = result["timeframes"].get(tf, {})
        all_swing_h += [sh["price"] for sh in tf_data.get("swing_highs", [])[:3]]
        all_swing_l += [sl["price"] for sl in tf_data.get("swing_lows",  [])[:3]]

    def _dedup(levels: List[float], pip_distance: float = 0.0010) -> List[float]:
        if not levels:
            return []
        levels = sorted(set(levels))
        deduped = [levels[0]]
        for lvl in levels[1:]:
            if abs(lvl - deduped[-1]) > pip_distance:
                deduped.append(lvl)
        return [round(l, 5) for l in deduped]

    result["summary"] = {
        "current_price":      round(current_price, 5),
        "primary_trend":      primary_trend,
        "dominant_trend":     dominant_trend,
        "trend_confluence":   f"{confluence_pct}% of timeframes ({trend_votes[dominant_trend]}/{len(sorted_tfs)})",
        "timeframe_trends":   {
            tf: result["timeframes"].get(tf, {}).get("trend", {}).get("direction", "N/A")
            for tf in sorted_tfs
        },
        "key_resistance_levels": _dedup(all_swing_h),
        "key_support_levels":    _dedup(all_swing_l),
        "highest_tf_analyzed":   highest_tf,
        "lowest_tf_analyzed":    lowest_tf,
    }

    return result
