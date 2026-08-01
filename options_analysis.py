"""
Options flow analysis for short-term signals.

Looks at the nearest-expiry option chain and computes:
  - Put/call volume ratio  (low = call-heavy = bullish)
  - Unusual volume vs open interest  (large institutions leaving footprints)

Returns a score in [-2, +2] and a human-readable label.
Heavy call buying relative to puts and OI is one of the strongest 1-5 day
leading signals because institutional orders can't be fully hidden.
"""

import logging
from typing import Optional, Tuple

import yfinance as yf

logger = logging.getLogger(__name__)

_MIN_TOTAL_VOLUME = 100   # skip low-liquidity chains


def fetch_options_flow(symbol: str) -> Tuple[float, Optional[str]]:
    """
    Return (score in [-2, +2], label or None).

    score > 0 = net bullish options flow (calls dominating)
    score < 0 = net bearish options flow (puts dominating)
    """
    try:
        ticker   = yf.Ticker(symbol)
        expiries = ticker.options
        if not expiries:
            return 0.0, None

        chain = ticker.option_chain(expiries[0])
        calls = chain.calls
        puts  = chain.puts

        if calls.empty or puts.empty:
            return 0.0, None

        call_vol = float(calls["volume"].fillna(0).sum())
        put_vol  = float(puts["volume"].fillna(0).sum())
        call_oi  = float(calls["openInterest"].fillna(0).sum())
        put_oi   = float(puts["openInterest"].fillna(0).sum())

        total_vol = call_vol + put_vol
        if total_vol < _MIN_TOTAL_VOLUME:
            return 0.0, None

        # ── Put/call ratio ────────────────────────────────────────────────────
        pc_ratio = put_vol / call_vol if call_vol > 0 else 999.0

        if   pc_ratio < 0.40: pc_score = 2.0
        elif pc_ratio < 0.70: pc_score = 1.0
        elif pc_ratio < 1.30: pc_score = 0.0
        elif pc_ratio < 2.00: pc_score = -1.0
        else:                 pc_score = -2.0

        # ── Unusual volume vs open interest ───────────────────────────────────
        uv_score = 0.0
        label    = None

        call_vol_oi_ratio = (call_vol / call_oi) if call_oi > 0 else 0.0
        put_vol_oi_ratio  = (put_vol  / put_oi)  if put_oi  > 0 else 0.0

        if call_vol_oi_ratio > 2.0 and pc_ratio < 1.0:
            uv_score = 1.0
            label = f"Unusual calls ({call_vol_oi_ratio:.1f}x OI)"
        elif put_vol_oi_ratio > 2.0 and pc_ratio > 1.0:
            uv_score = -1.0
            label = f"Unusual puts ({put_vol_oi_ratio:.1f}x OI)"
        elif pc_ratio < 0.40:
            label = f"Heavy calls P/C {pc_ratio:.2f}"
        elif pc_ratio > 2.00:
            label = f"Heavy puts P/C {pc_ratio:.2f}"

        score = max(-2.0, min(2.0, pc_score * 0.6 + uv_score * 0.4))
        return score, label

    except Exception as e:
        logger.debug("Options flow fetch failed for %s: %s", symbol, e)
        return 0.0, None
