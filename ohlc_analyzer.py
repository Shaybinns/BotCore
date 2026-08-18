"""
OHLC Analyzer

Converts raw candle arrays into structured technical analysis for GPT-4o.

- Swing points (highs/lows) with forming-candle time + OHLC for BOS / order-block math
- Imbalances (detect_imb): 3-candle gaps (all timeframes)
- FVGs (detect_fvg): ICT 3-candle gap on M1/M2/M5 only — entry midpoint of the gap
- Session highs/lows (H1 only): 01:00–07:00, 08:00–12:00, 13:00–17:00 London time
- last_3_candles: newest-first open/high/low/close (+ time) per timeframe

Candle arrays arrive newest-first (index 0 = most recent bar).
Each candle: {"time": int, "open": float, "high": float, "low": float, "close": float, "volume": int}

Output feeds directly into the === OHLC DATA ANALYSIS === section of the trading context.
"""

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_LONDON_TZ = ZoneInfo("Europe/London")

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
    "M2_DATA":  1,
    "M1_DATA":  1,
}


def _tf_rank(key: str) -> int:
    return _TF_RANK.get(key.upper(), 0) or _TF_RANK.get(key, 0)


def _is_h1_key(tf: str) -> bool:
    return tf.upper() in ("1H_DATA", "H1_DATA")


def _is_fvg_timeframe(tf: str) -> bool:
    """FVG detection is entry-TF only (M1 / M2 / M5)."""
    key = tf.upper().replace("_DATA", "")
    return key in ("M1", "1M", "M2", "2M", "M5", "5M")


def _candle_ohlc(c: Dict) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "open": round(c["open"], 5),
        "high": round(c["high"], 5),
        "low": round(c["low"], 5),
        "close": round(c["close"], 5),
    }
    if c.get("time") is not None:
        out["time"] = c["time"]
    return out


# =============================================================================
# SWING POINTS
# =============================================================================

def _swing_points(candles: List[Dict], strength: int = 3) -> Tuple[List[Dict], List[Dict]]:
    """
    Detect swing highs and swing lows.
    strength = number of bars each side that must be lower/higher.
    Candles newest-first; we work on reversed list and translate indices back.
    Returns (swing_highs, swing_lows) each as:
      {price, bar_index, time, open, high, low, close}
    — time + OHLC are from the candle that printed the swing extreme.
    Sorted newest-first (smallest bar_index first).
    """
    rev = list(reversed(candles))
    n = len(rev)
    highs, lows = [], []

    def _swing_payload(rev_i: int, price: float) -> Dict[str, Any]:
        c = rev[rev_i]
        bar_index = n - 1 - rev_i
        out: Dict[str, Any] = {
            "price": round(price, 5),
            "bar_index": bar_index,
            "open": round(c["open"], 5),
            "high": round(c["high"], 5),
            "low": round(c["low"], 5),
            "close": round(c["close"], 5),
        }
        if c.get("time") is not None:
            out["time"] = c["time"]
        return out

    for i in range(strength, n - strength):
        h = rev[i]["high"]
        l = rev[i]["low"]
        if all(rev[i]["high"] >= rev[j]["high"] for j in range(i - strength, i + strength + 1) if j != i):
            highs.append(_swing_payload(i, h))
        if all(rev[i]["low"] <= rev[j]["low"] for j in range(i - strength, i + strength + 1) if j != i):
            lows.append(_swing_payload(i, l))

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
# FVG (ICT 3-candle gap — M1/M2/M5 only)
# Candle 1 = oldest of the three, candle 3 = newest (closed).
# Bullish: candle 3 low > candle 1 high; range = [c1 high, c3 low]; entry = midpoint.
# Bearish: candle 3 high < candle 1 low; range = [c3 high, c1 low]; entry = midpoint.
# =============================================================================

def _detect_fvg(candles: List[Dict], max_fvgs: int = 10) -> List[Dict]:
    """
    3-candle FVG. Candles newest-first:
      c3 = newest, c2 = middle, c1 = oldest of the three.
    Returns newest first.
    """
    fvgs = []
    for i in range(len(candles) - 2):
        c3, c2, c1 = candles[i], candles[i + 1], candles[i + 2]
        if c3["low"] > c1["high"]:
            top = round(c3["low"], 5)
            bottom = round(c1["high"], 5)
            fvgs.append({
                "type": "BULLISH",
                "top": top,
                "bottom": bottom,
                "midpoint": round((top + bottom) / 2, 5),
                "bar_index": i,
                "candle_1": _candle_ohlc(c1),
                "candle_2": _candle_ohlc(c2),
                "candle_3": _candle_ohlc(c3),
            })
        elif c3["high"] < c1["low"]:
            top = round(c1["low"], 5)
            bottom = round(c3["high"], 5)
            fvgs.append({
                "type": "BEARISH",
                "top": top,
                "bottom": bottom,
                "midpoint": round((top + bottom) / 2, 5),
                "bar_index": i,
                "candle_1": _candle_ohlc(c1),
                "candle_2": _candle_ohlc(c2),
                "candle_3": _candle_ohlc(c3),
            })
        if len(fvgs) >= max_fvgs:
            break
    return fvgs


# =============================================================================
# SESSION HIGHS/LOWS (H1 only; London-time sessions)
# =============================================================================

# Sessions: (label, start_hour_london, end_hour_london) — end is exclusive.
# All times are London local (BST or GMT) — they never shift because OHLC
# timestamps are already converted to London local time by the EA.
_SESSIONS = [
    ("Asian  01:00-07:00", 1,  7),
    ("London 08:00-12:00", 8,  12),
    ("NY     13:00-17:00", 13, 17),
]


def _session_highs_lows(candles: List[Dict]) -> List[Dict]:
    """
    Compute high and low per session from H1 candles (London local time).
    Candles newest-first. Each candle has "time" (London-local epoch from EA).
    If we're currently inside a session, note it as in-progress.
    If no data for a session, high/low are "NA".
    """
    if not candles:
        return []

    now_london = datetime.now(_LONDON_TZ)
    current_hour = now_london.hour
    today = now_london.date()
    results = []

    for label, start_h, end_h in _SESSIONS:
        entry = {"session": label, "high": None, "low": None}
        in_progress = start_h <= current_hour < end_h

        # Collect TODAY's candles that fall in this London-time session window.
        # fromtimestamp with utc reads the London-local epoch as a clock value.
        session_candles = []
        for c in candles:
            t = c.get("time")
            if t is None:
                continue
            try:
                dt = datetime.fromtimestamp(t, tz=timezone.utc)
            except (TypeError, OSError):
                continue
            if dt.date() != today:
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
    fvgs = _detect_fvg(candles) if _is_fvg_timeframe(tf) else []

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
        # Newest-first: index 0 = most recent (forming/last closed depending on EA), then prior 2.
        "last_3_candles": [
            {
                "open": round(c["open"], 5),
                "high": round(c["high"], 5),
                "low": round(c["low"], 5),
                "close": round(c["close"], 5),
                **({"time": c["time"]} if c.get("time") is not None else {}),
            }
            for c in candles[:3]
        ],
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
        "key_resistance_levels": [l for l in _dedup(all_swing_h) if l > current_price],
        "key_support_levels": [l for l in _dedup(all_swing_l) if l < current_price],
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
