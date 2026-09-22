"""
Short-term (1-5 day horizon) technical indicators and scoring.

Unlike technical_analysis.py (which uses 50/200-day trend indicators meant
for position trading), everything here reacts to the last few days of price
action: fast EMA stacks, a sensitive Stochastic oscillator, short-window
Rate-of-Change, overnight gaps, relative volume, and candlestick patterns.

Each scoring function returns a value in [-2, +2]. Positive = bullish.
"""

import numpy as np
import pandas as pd
from typing import Tuple


# ─────────────────────────────────────────────
# Indicator calculations
# ─────────────────────────────────────────────

def calc_ema(close: pd.Series, period: int) -> float:
    if len(close) < 2:
        return float(close.iloc[-1]) if len(close) else 0.0
    val = close.ewm(span=period, adjust=False).mean().iloc[-1]
    return float(val) if not np.isnan(val) else float(close.iloc[-1])


def calc_stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
                     k_period: int = 14, d_period: int = 3) -> Tuple[float, float]:
    if len(close) < k_period + d_period:
        return 50.0, 50.0
    lowest_low   = low.rolling(k_period).min()
    highest_high = high.rolling(k_period).max()
    rng = (highest_high - lowest_low).replace(0, np.nan)
    percent_k = 100 * (close - lowest_low) / rng
    percent_d = percent_k.rolling(d_period).mean()
    k = percent_k.iloc[-1]
    d = percent_d.iloc[-1]
    k = float(k) if not np.isnan(k) else 50.0
    d = float(d) if not np.isnan(d) else 50.0
    return k, d


def calc_roc(close: pd.Series, period: int) -> float:
    if len(close) <= period:
        return 0.0
    prev = close.iloc[-1 - period]
    if prev == 0 or np.isnan(prev):
        return 0.0
    return float((close.iloc[-1] - prev) / prev * 100)


def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    if len(close) < 2:
        return 0.0
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    val = tr.rolling(min(period, len(tr))).mean().iloc[-1]
    return float(val) if not np.isnan(val) else float(tr.iloc[-1])


# ─────────────────────────────────────────────
# Individual indicator scoring (each → [-2, +2])
# ─────────────────────────────────────────────

def score_ema_stack(price: float, ema5: float, ema10: float, ema20: float) -> float:
    score = 0.0
    score += 0.5 if price > ema5  else -0.5
    score += 0.5 if ema5  > ema10 else -0.5
    score += 0.5 if ema10 > ema20 else -0.5
    if price > ema5 > ema10 > ema20:
        score += 0.5
    elif price < ema5 < ema10 < ema20:
        score -= 0.5
    return max(-2.0, min(2.0, score))


def score_stochastic(k: float, d: float) -> float:
    score = 0.0
    if   k < 20: score += 1.5   # oversold → bullish reversal setup
    elif k > 80: score -= 1.5   # overbought → bearish reversal setup
    score += 0.5 if k > d else -0.5   # %K/%D momentum crossover
    return max(-2.0, min(2.0, score))


def score_roc(roc3: float, roc5: float) -> float:
    score = 0.0
    if   roc3 >  3: score += 1.0
    elif roc3 >  1: score += 0.5
    elif roc3 < -3: score -= 1.0
    elif roc3 < -1: score -= 0.5

    if   roc5 >  5: score += 1.0
    elif roc5 < -5: score -= 1.0
    return max(-2.0, min(2.0, score))


def score_gap(open_today: float, prev_close: float) -> float:
    if prev_close <= 0:
        return 0.0
    gap_pct = (open_today - prev_close) / prev_close * 100
    if gap_pct >  2: return  1.0
    if gap_pct < -2: return -1.0
    return 0.0


def score_rel_volume(volume_ratio: float, change_pct: float) -> float:
    if volume_ratio >= 2.0:
        return 1.5 if change_pct >= 0 else -1.5
    if volume_ratio >= 1.4:
        return 0.7 if change_pct >= 0 else -0.7
    return 0.0


def detect_candle_pattern(df: pd.DataFrame) -> Tuple[str, float]:
    """Simple heuristic candlestick recognition on the last 2 daily bars."""
    if len(df) < 2:
        return "—", 0.0

    last, prev = df.iloc[-1], df.iloc[-2]
    o, h, l, c   = float(last["Open"]), float(last["High"]), float(last["Low"]), float(last["Close"])
    po, pc       = float(prev["Open"]), float(prev["Close"])

    body  = abs(c - o)
    rng   = (h - l) if h != l else 1e-9
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)

    # Bullish engulfing: prior red candle fully engulfed by a green candle
    if pc < po and c > o and c >= po and o <= pc:
        return "Bull Engulf", 1.5
    # Bearish engulfing
    if pc > po and c < o and c <= po and o >= pc:
        return "Bear Engulf", -1.5
    # Hammer: long lower wick, small body near the top, closes green
    if lower_wick > body * 2 and upper_wick < body * 0.5 and c >= o:
        return "Hammer", 1.0
    # Shooting star: long upper wick, small body near the bottom, closes red
    if upper_wick > body * 2 and lower_wick < body * 0.5 and c <= o:
        return "Shoot Star", -1.0
    # Doji: indecision, body is tiny relative to range
    if body < rng * 0.1:
        return "Doji", 0.0
    return "—", 0.0


# ─────────────────────────────────────────────
# Candidate indicators — measured, NOT yet weighted
# ─────────────────────────────────────────────
# These are computed on every bar so backtest.py can measure their
# Information Coefficient, but they carry ZERO weight in short_score until
# they clear the |IC| > 0.03 bar. Prior backtesting found that none of the
# existing six factors cleared it, so new ideas get measured, not assumed.
#
# All three target short-horizon MEAN REVERSION, which is the effect the
# literature actually supports at a 1-5 day holding period (momentum's edge
# shows up at 3-12 months). Windows are kept <= 60 bars so the live manager
# (which downloads 3 months) computes them identically to the backtest.

def calc_rsi2(close: pd.Series, period: int = 2) -> float:
    """Very short RSI — the Connors-style mean-reversion oscillator."""
    if len(close) < period + 1:
        return 50.0
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    val = (100 - 100 / (1 + rs)).iloc[-1]
    return float(val) if not np.isnan(val) else 50.0


def score_rsi2(rsi2: float) -> float:
    if rsi2 < 5:   return  2.0
    if rsi2 < 10:  return  1.5
    if rsi2 < 25:  return  0.5
    if rsi2 > 95:  return -2.0
    if rsi2 > 90:  return -1.5
    if rsi2 > 75:  return -0.5
    return 0.0


def calc_zscore(close: pd.Series, period: int = 20) -> float:
    """How many standard deviations price sits from its 20-day mean."""
    if len(close) < period:
        return 0.0
    window = close.tail(period)
    mu, sd = float(window.mean()), float(window.std())
    if sd <= 0 or np.isnan(sd):
        return 0.0
    return float((float(close.iloc[-1]) - mu) / sd)


def score_zscore(z: float) -> float:
    """Stretched below the mean is bullish under a reversion hypothesis."""
    return max(-2.0, min(2.0, -z))


def calc_range_position(close: pd.Series, period: int = 60) -> float:
    """0.0 = at the period low, 1.0 = at the period high."""
    if len(close) < 5:
        return 0.5
    window = close.tail(period)
    lo, hi = float(window.min()), float(window.max())
    if hi <= lo:
        return 0.5
    return (float(close.iloc[-1]) - lo) / (hi - lo)


def score_range_position(pos: float) -> float:
    if pos < 0.10:  return  1.5
    if pos < 0.25:  return  0.7
    if pos > 0.90:  return -1.5
    if pos > 0.75:  return -0.7
    return 0.0


# ─────────────────────────────────────────────
# Composite short-term score [-2, +2]
# ─────────────────────────────────────────────

TECH_WEIGHTS = {
    "ema":    0.20,
    "stoch":  0.20,
    # NEGATIVE ON PURPOSE. roc_score's Information Coefficient came out
    # consistently negative in BOTH the in-sample and out-of-sample periods,
    # at all three horizons (IS -0.010/-0.027/-0.025, OOS -0.012/-0.025/-0.024)
    # -- i.e. rewarding a recent run-up predicted *worse* forward returns, which
    # matches the literature that 1-5 day equity returns are mean-reverting.
    # Flipping the sign restored a monotonic score->return ordering OOS and put
    # the BUY bucket ahead of SPY. Magnitudes still sum to 1.0, so score scale
    # (and therefore the thresholds) is unchanged.
    "roc":   -0.25,
    "gap":    0.10,
    "volume": 0.10,
    "candle": 0.15,
}
_WEIGHTS = TECH_WEIGHTS


def calculate_short_term_score(df: pd.DataFrame) -> dict:
    """
    df must have columns Open, High, Low, Close, Volume (daily bars, most
    recent ~60 days). Returns indicator values, per-indicator scores, and
    the weighted composite short_score.
    """
    if len(df) < 5:
        return _empty()

    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]
    open_ = df["Open"]

    price      = float(close.iloc[-1])
    prev_close = float(close.iloc[-2]) if len(close) >= 2 else price
    change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0.0

    ema5  = calc_ema(close, 5)
    ema10 = calc_ema(close, 10)
    ema20 = calc_ema(close, 20)

    k, d = calc_stochastic(high, low, close)
    roc3 = calc_roc(close, 3)
    roc5 = calc_roc(close, 5)
    atr  = calc_atr(high, low, close)
    atr_pct = (atr / price * 100) if price else 0.0

    gap_pct = ((float(open_.iloc[-1]) - prev_close) / prev_close * 100) if prev_close else 0.0

    vol_current = float(vol.iloc[-1])
    vol_avg     = float(vol.tail(10).mean()) if len(vol) >= 5 else vol_current
    vol_ratio   = (vol_current / vol_avg) if vol_avg > 0 else 1.0

    candle_label, candle_raw = detect_candle_pattern(df)

    ema_score   = score_ema_stack(price, ema5, ema10, ema20)
    stoch_score = score_stochastic(k, d)
    roc_score   = score_roc(roc3, roc5)
    gap_score   = score_gap(float(open_.iloc[-1]), prev_close)
    vol_score   = score_rel_volume(vol_ratio, change_pct)
    candle_score = max(-2.0, min(2.0, candle_raw))

    short_score = (
        ema_score    * _WEIGHTS["ema"]    +
        stoch_score  * _WEIGHTS["stoch"]  +
        roc_score    * _WEIGHTS["roc"]    +
        gap_score    * _WEIGHTS["gap"]    +
        vol_score    * _WEIGHTS["volume"] +
        candle_score * _WEIGHTS["candle"]
    )

    # Candidate factors: computed for measurement, deliberately excluded from
    # the weighted short_score above until backtest.py validates them.
    rsi2      = calc_rsi2(close)
    zscore    = calc_zscore(close)
    range_pos = calc_range_position(close)

    return {
        "rsi2": rsi2, "rsi2_score": score_rsi2(rsi2),
        "zscore": zscore, "zscore_score": score_zscore(zscore),
        "range_pos": range_pos, "range_pos_score": score_range_position(range_pos),
        "price": price, "change_pct": change_pct,
        "ema5": ema5, "ema10": ema10, "ema20": ema20,
        "stoch_k": k, "stoch_d": d,
        "roc3": roc3, "roc5": roc5,
        "atr": atr, "atr_pct": atr_pct,
        "gap_pct": gap_pct,
        "volume": int(vol_current), "avg_volume": int(vol_avg), "volume_ratio": vol_ratio,
        "candle_label": candle_label,
        "ema_score": ema_score, "stoch_score": stoch_score, "roc_score": roc_score,
        "gap_score": gap_score, "vol_score": vol_score, "candle_score": candle_score,
        "short_score": short_score,
    }


def _empty() -> dict:
    d = {k: 0.0 for k in [
        "price", "change_pct", "ema5", "ema10", "ema20", "stoch_k", "stoch_d",
        "roc3", "roc5", "atr", "atr_pct", "gap_pct", "volume", "avg_volume",
        "volume_ratio", "ema_score", "stoch_score", "roc_score", "gap_score",
        "vol_score", "candle_score", "short_score",
        "rsi2", "rsi2_score", "zscore", "zscore_score",
        "range_pos", "range_pos_score",
    ]}
    d["candle_label"] = "—"
    return d


# ─────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────

def ema_arrow(price: float, ema5: float, ema10: float, ema20: float) -> str:
    if price > ema5 > ema10 > ema20: return "▲▲"
    if price > ema5 and ema5 > ema10: return "▲"
    if price < ema5 < ema10 < ema20: return "▼▼"
    if price < ema5 and ema5 < ema10: return "▼"
    return "─"


def stoch_label(k: float) -> str:
    if k < 20: return "OVSOLD"
    if k > 80: return "OVBOT"
    return f"{k:.0f}"


def volatility_tier(atr_pct: float) -> str:
    if atr_pct > 4.0: return "HIGH"
    if atr_pct > 2.0: return "MED"
    return "LOW"
