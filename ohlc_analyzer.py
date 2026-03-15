"""
OHLC Analyzer

Converts raw candle arrays into structured technical analysis for GPT-4o.

- Swing points (highs/lows)
- Imbalances (detect_imb): 3-candle gaps
- FVGs (detect_fvg): imbalance after swing level is broken and price crosses back; one of the 3 candles must touch the level
- Session highs/lows (H1 only): 00:00–06:00, 08:00–12:00, 13:00–17:00 UTC

Candle arrays arrive newest-first (index 0 = most recent bar).
Each candle: {"time": int, "open": float, "high": float, "low": float, "close": float, "volume": int}

Output feeds directly into the === OHLC DATA ANALYSIS === section of the trading context.
"""

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone

# =============================================================================
# TIMEFRAME ORDERING  (highest → lowest)
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


def _is_h1_key(tf: str) -> bool:
    return tf.upper() in ("1H_DATA", "H1_DATA")


# =============================================================================
# SWING POINTS
# =============================================================================

def _swing_points(candles: List[Dict], strength: int = 3) -> Tuple[List[Dict], List[Dict]]:
    """
    Detect swing highs and swing lows.
    strength = number of bars each side that must be lower/higher.
    Candles newest-first; we work on reversed list and translate indices back.
    Returns (swing_highs, swing_lows) each as list of {"price": float, "bar_index": int}
    Sorted newest-first (smallest index first).
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

    highs.sort(key=lambda x: x["bar_index"])
    lows.sort(key=lambda x: x["bar_index"])
    return highs, lows


# =============================================================================
# IMBALANCE (3-candle gap, no level condition)
# =============================================================================

def _detect_imb(candles: List[Dict], max_imb: int = 10) -> List[Dict]:
    """
    Imbalance: 3-candle formation where candle[i] and candle[i+2] do not overlap (gap).
    Candles newest-first. Bullish: c[i].low > c[i+2].high. Bearish: c[i].high < c[i+2].low.
    Returns newest first, capped at max_imb.
    """
    imb = []
    for i in range(len(candles) - 2):
        c0, c2 = candles[i], candles[i + 2]
        if c0["low"] > c2["high"]:
            imb.append({
                "type": "BULLISH",
                "top": round(c0["low"], 5),
                "bottom": round(c2["high"], 5),
                "bar_index": i,
            })
        elif c0["high"] < c2["low"]:
            imb.append({
                "type": "BEARISH",
                "top": round(c2["low"], 5),
                "bottom": round(c0["high"], 5),
                "bar_index": i,
            })
        if len(imb) >= max_imb:
            break
    return imb


# =============================================================================
# FVG (imbalance after swing break + cross back; one of 3 candles must touch level)
# =============================================================================

def _detect_fvg(
    candles: List[Dict],
    swing_highs: List[Dict],
    swing_lows: List[Dict],
    max_fvgs: int = 5,
) -> List[Dict]:
    """
    FVG: imbalance that forms when price crosses back through a swing level after breaking it.
    - Upward FVG (buys): swing low → price breaks below → crosses back up; one of the 3 candles
      that create the upward imbalance must touch the swing low; gap = FVG range for longs.
    - Downward FVG (sells): swing high → price breaks above (raid) → crosses back down; one of
      the 3 candles must touch the swing high; gap = FVG range for shorts.
    Candles newest-first. Returns newest first.
    """
    fvgs = []
    n = len(candles)

    def _touches(c0: Dict, c1: Dict, c2: Dict, level: float) -> bool:
        for c in (c0, c1, c2):
            if c["low"] <= level <= c["high"]:
                return True
        return False

    def _broke_below(level: float, start: int) -> bool:
        for j in range(start, n):
            if candles[j]["low"] < level:
                return True
        return False

    def _broke_above(level: float, start: int) -> bool:
        for j in range(start, n):
            if candles[j]["high"] > level:
                return True
        return False

    # Upward FVG: bullish gap after break below swing low; one of 3 candles touches level
    for i in range(n - 2):
        c0, c1, c2 = candles[i], candles[i + 1], candles[i + 2]
        if c0["low"] <= c2["high"]:
            continue
        top, bottom = round(c0["low"], 5), round(c2["high"], 5)
        for sl in swing_lows:
            level = sl["price"]
            if level > bottom:
                continue
            if sl["bar_index"] < i + 2:
                continue
            if not _touches(c0, c1, c2, level):
                continue
            if not _broke_below(level, i + 2):
                continue
            fvgs.append({
                "type": "BULLISH",
                "top": top,
                "bottom": bottom,
                "swing_level": level,
                "bar_index": i,
            })
            break

    # Downward FVG: bearish gap after break above swing high; one of 3 candles touches level
    for i in range(n - 2):
        c0, c1, c2 = candles[i], candles[i + 1], candles[i + 2]
        if c0["high"] >= c2["low"]:
            continue
        top, bottom = round(c2["low"], 5), round(c0["high"], 5)
        for sh in swing_highs:
            level = sh["price"]
            if level < top:
                continue
            if sh["bar_index"] < i + 2:
                continue
            if not _touches(c0, c1, c2, level):
                continue
            if not _broke_above(level, i + 2):
                continue
            fvgs.append({
                "type": "BEARISH",
                "top": top,
                "bottom": bottom,
                "swing_level": level,
                "bar_index": i,
            })
            break

    return fvgs[:max_fvgs]


# =============================================================================
# SESSION HIGHS/LOWS (H1 only; UTC sessions)
# =============================================================================

# Sessions: (label, start_hour_utc, end_hour_utc) — end is exclusive
_SESSIONS = [
    ("00:00-06:00", 0, 6),
    ("08:00-12:00", 8, 12),
    ("13:00-17:00", 13, 17),
]


def _session_highs_lows(candles: List[Dict]) -> List[Dict]:
    """
    Compute high and low per session from H1 candles (UTC).
    Candles newest-first. Each candle has "time" (Unix).
    If we're inside a session (e.g. 09:30), report current_high / current_low for that session.
    If no data for a session, high/low are "NA".
    """
    if not candles:
        return []

    now = datetime.now(timezone.utc)
    current_hour = now.hour
    results = []

    for label, start_h, end_h in _SESSIONS:
        entry = {"session": label, "high": None, "low": None}
        # Check if we're currently inside this session
        in_progress = start_h <= current_hour < end_h

        # Collect candles that fall in this session (UTC)
        session_candles = []
        for c in candles:
            t = c.get("time")
            if t is None:
                continue
            try:
                dt = datetime.fromtimestamp(t, tz=timezone.utc)
            except (TypeError, OSError):
                continue
            h = dt.hour
            if h >= start_h and h < end_h:
                session_candles.append(c)

        if not session_candles:
            entry["high"] = "NA"
            entry["low"] = "NA"
            if in_progress:
                entry["note"] = "in progress, no completed bars yet"
        else:
            all_highs = [c["high"] for c in session_candles]
            all_lows = [c["low"] for c in session_candles]
            entry["high"] = round(max(all_highs), 5)
            entry["low"] = round(min(all_lows), 5)
            if in_progress:
                entry["note"] = "current high/low so far (session in progress)"

        results.append(entry)

    return results


# =============================================================================
# PER-TIMEFRAME ANALYSIS
# =============================================================================

def _analyze_timeframe(tf: str, candles: List[Dict]) -> Dict[str, Any]:
    """Analysis for one timeframe: swing points, imbalance, FVG; session high/low only for H1."""
    if not candles:
        return {"error": "no data"}

    current_price = candles[0]["close"]
    period_high = max(c["high"] for c in candles)
    period_low = min(c["low"] for c in candles)

    swing_h, swing_l = _swing_points(candles, strength=3)
    imb = _detect_imb(candles, max_imb=10)
    fvgs = _detect_fvg(candles, swing_h, swing_l, max_fvgs=5)

    nearest_resistance = next(
        (sh["price"] for sh in sorted(swing_h, key=lambda x: x["price"]) if sh["price"] > current_price),
        None,
    )
    nearest_support = next(
        (sl["price"] for sl in sorted(swing_l, key=lambda x: x["price"], reverse=True) if sl["price"] < current_price),
        None,
    )

    out = {
        "candle_count": len(candles),
        "current_price": round(current_price, 5),
        "period_high": round(period_high, 5),
        "period_low": round(period_low, 5),
        "nearest_resistance": nearest_resistance,
        "nearest_support": nearest_support,
        "swing_highs": swing_h[:10],
        "swing_lows": swing_l[:10],
        "detect_imb": imb,
        "fair_value_gaps": fvgs,
    }

    if _is_h1_key(tf):
        out["session_highs_lows"] = _session_highs_lows(candles)
    else:
        out["session_highs_lows"] = None  # only H1 has session analysis

    return out


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def analyze_ohlc_data(ohlc_data: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """
    Analyze OHLC data for all timeframes.

    - Swing points, imbalance (detect_imb), FVG (swing-level-based), session high/low (H1 only).
    """
    result: Dict[str, Any] = {
        "timeframes": {},
        "summary": {},
    }

    valid_tfs = {k: v for k, v in ohlc_data.items() if v and len(v) >= 3}

    if not valid_tfs:
        return {"error": "no valid OHLC data", "timeframes": {}, "summary": {}}

    for tf, candles in valid_tfs.items():
        try:
            result["timeframes"][tf] = _analyze_timeframe(tf, candles)
        except Exception as e:
            result["timeframes"][tf] = {"error": str(e)}

    sorted_tfs = sorted(valid_tfs.keys(), key=_tf_rank, reverse=True)
    lowest_tf = sorted(valid_tfs.keys(), key=_tf_rank)[0]
    current_price = valid_tfs[lowest_tf][0]["close"]

    all_swing_h = []
    all_swing_l = []
    for tf in sorted_tfs[:4]:
        tf_data = result["timeframes"].get(tf, {})
        all_swing_h += [sh["price"] for sh in tf_data.get("swing_highs", [])[:3]]
        all_swing_l += [sl["price"] for sl in tf_data.get("swing_lows", [])[:3]]

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
        "current_price": round(current_price, 5),
        "key_resistance_levels": _dedup(all_swing_h),
        "key_support_levels": _dedup(all_swing_l),
        "highest_tf_analyzed": sorted_tfs[0],
        "lowest_tf_analyzed": lowest_tf,
    }

    # Add session summary only if H1 was analyzed
    h1_key = next((k for k in valid_tfs if _is_h1_key(k)), None)
    if h1_key and "session_highs_lows" in result["timeframes"].get(h1_key, {}):
        result["summary"]["session_highs_lows"] = result["timeframes"][h1_key]["session_highs_lows"]
    else:
        result["summary"]["session_highs_lows"] = "NA (no H1 data)"

    return result
