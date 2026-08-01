"""
Technical indicator calculations and scoring.
Each scoring function returns a value in [-2, +2].
Positive = bullish, Negative = bearish.
"""

import numpy as np
import pandas as pd
from typing import Tuple


# ─────────────────────────────────────────────
# Indicator calculations
# ─────────────────────────────────────────────

def calc_rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return 50.0
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return float(val) if not np.isnan(val) else 50.0


def calc_macd(close: pd.Series,
              fast: int = 12, slow: int = 26, signal: int = 9
              ) -> Tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calc_bollinger(close: pd.Series, period: int = 20, n_std: float = 2.0
                   ) -> Tuple[pd.Series, pd.Series, pd.Series]:
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = sma + n_std * std
    lower = sma - n_std * std
    return upper, sma, lower


def calc_sma(close: pd.Series, period: int) -> float:
    if len(close) < period:
        return float(close.iloc[-1]) if len(close) else 0.0
    val = close.rolling(period).mean().iloc[-1]
    return float(val) if not np.isnan(val) else float(close.iloc[-1])


# ─────────────────────────────────────────────
# Individual indicator scoring (each → [-2, +2])
# ─────────────────────────────────────────────

def score_rsi(rsi: float) -> float:
    if rsi < 20:   return  2.0
    if rsi < 30:   return  1.5
    if rsi < 40:   return  0.5
    if rsi < 55:   return  0.0
    if rsi < 65:   return -0.5
    if rsi < 75:   return -1.5
    return -2.0


def score_macd(macd: float, signal: float, hist: float, prev_hist: float) -> float:
    score = 1.0 if macd > signal else -1.0

    # Histogram momentum
    if hist > 0 and hist > prev_hist:       # growing bullish
        score += 1.0
    elif hist > 0:                           # shrinking bullish
        score += 0.3
    elif hist < 0 and abs(hist) < abs(prev_hist):  # improving bearish
        score -= 0.3
    else:                                    # deepening bearish
        score -= 1.0

    return max(-2.0, min(2.0, score))


def score_bollinger(price: float, upper: float, middle: float, lower: float) -> float:
    band_width = upper - lower
    if band_width <= 0:
        return 0.0

    if price < lower:
        return 2.0
    if price > upper:
        return -2.0

    position = (price - lower) / band_width  # 0 = lower, 1 = upper
    if position < 0.2:   return  1.0
    if position < 0.45:  return  0.3
    if position < 0.55:  return  0.0
    if position < 0.8:   return -0.3
    return -1.0


def score_moving_avg(price: float, sma_50: float, sma_200: float) -> float:
    score = 0.0
    if sma_50 > 0:
        score += 0.7 if price > sma_50 else -0.7
    if sma_200 > 0:
        score += 0.7 if price > sma_200 else -0.7
    if sma_50 > 0 and sma_200 > 0:
        score += 0.6 if sma_50 > sma_200 else -0.6  # golden / death cross
    return max(-2.0, min(2.0, score))


def score_volume(volume_ratio: float, change_pct: float) -> float:
    if volume_ratio >= 1.5:
        return 1.0 if change_pct >= 0 else -1.0
    if volume_ratio >= 1.2:
        return 0.5 if change_pct >= 0 else -0.5
    return 0.0


# ─────────────────────────────────────────────
# Composite technical score [-2, +2]
# ─────────────────────────────────────────────

def calculate_tech_score(
    close: pd.Series,
    volume: pd.Series,
) -> dict:
    """
    Compute all technical indicators and return a dict with:
    - individual indicator values
    - per-indicator scores
    - weighted composite tech_score
    """
    if len(close) < 2:
        return _empty_tech()

    price = float(close.iloc[-1])
    prev_price = float(close.iloc[-2])
    change_pct = ((price - prev_price) / prev_price * 100) if prev_price else 0.0

    # ── RSI ──────────────────────────────────
    rsi = calc_rsi(close)

    # ── MACD ─────────────────────────────────
    macd_line, macd_sig, macd_hist = calc_macd(close)
    m_val   = float(macd_line.iloc[-1]) if not macd_line.empty else 0.0
    s_val   = float(macd_sig.iloc[-1])  if not macd_sig.empty  else 0.0
    h_val   = float(macd_hist.iloc[-1]) if not macd_hist.empty else 0.0
    h_prev  = float(macd_hist.iloc[-2]) if len(macd_hist) >= 2 else 0.0

    # ── Bollinger Bands ──────────────────────
    bb_upper, bb_mid, bb_lower = calc_bollinger(close)
    bbu = float(bb_upper.iloc[-1]) if not bb_upper.empty else price * 1.05
    bbm = float(bb_mid.iloc[-1])   if not bb_mid.empty   else price
    bbl = float(bb_lower.iloc[-1]) if not bb_lower.empty else price * 0.95

    # ── Moving Averages ───────────────────────
    sma20  = calc_sma(close, 20)
    sma50  = calc_sma(close, 50)
    sma200 = calc_sma(close, 200)

    # ── Volume ───────────────────────────────
    vol_current = float(volume.iloc[-1]) if not volume.empty else 0
    vol_avg     = float(volume.tail(20).mean()) if len(volume) >= 5 else vol_current
    vol_ratio   = (vol_current / vol_avg) if vol_avg > 0 else 1.0

    # ── Scores ───────────────────────────────
    r_score = score_rsi(rsi)
    m_score = score_macd(m_val, s_val, h_val, h_prev)
    b_score = score_bollinger(price, bbu, bbm, bbl)
    a_score = score_moving_avg(price, sma50, sma200)
    v_score = score_volume(vol_ratio, change_pct)

    from config import TECH_WEIGHTS as W
    tech = (
        r_score * W["rsi"]       +
        m_score * W["macd"]      +
        b_score * W["bollinger"] +
        a_score * W["ma"]        +
        v_score * W["volume"]
    )

    return {
        "price":       price,
        "change_pct":  change_pct,
        "volume":      int(vol_current),
        "avg_volume":  int(vol_avg),
        "volume_ratio": vol_ratio,
        "rsi":         rsi,
        "macd_line":   m_val,
        "macd_signal": s_val,
        "macd_hist":   h_val,
        "bb_upper":    bbu,
        "bb_middle":   bbm,
        "bb_lower":    bbl,
        "sma_20":      sma20,
        "sma_50":      sma50,
        "sma_200":     sma200,
        "rsi_score":   r_score,
        "macd_score":  m_score,
        "bb_score":    b_score,
        "ma_score":    a_score,
        "vol_score":   v_score,
        "tech_score":  tech,
    }


def _empty_tech() -> dict:
    return {k: 0.0 for k in [
        "price", "change_pct", "volume", "avg_volume", "volume_ratio",
        "rsi", "macd_line", "macd_signal", "macd_hist",
        "bb_upper", "bb_middle", "bb_lower",
        "sma_20", "sma_50", "sma_200",
        "rsi_score", "macd_score", "bb_score", "ma_score", "vol_score",
        "tech_score",
    ]}


# ─────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────

def bb_label(price: float, upper: float, lower: float) -> str:
    if upper == lower:
        return "MID"
    pos = (price - lower) / (upper - lower)
    if pos < 0.2:   return "LOW"
    if pos > 0.8:   return "HI"
    return "MID"


def ma_arrow(price: float, sma_50: float, sma_200: float) -> str:
    above_50  = price > sma_50  if sma_50  else False
    above_200 = price > sma_200 if sma_200 else False
    golden    = (sma_50 > sma_200) if (sma_50 and sma_200) else False

    if above_50 and above_200 and golden: return "▲▲"
    if above_50 and above_200:            return "▲"
    if above_50 and not above_200:        return "─"
    if not above_50 and above_200:        return "▼"
    return "▼▼"


def macd_arrow(hist: float) -> str:
    if hist > 0:   return "▲"
    if hist < 0:   return "▼"
    return "─"
