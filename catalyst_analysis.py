"""
Short-term catalyst detection.

Combines four independent signals into a single catalyst_score [-2, +2]:

  1. Fresh analyst rating CHANGES  (upgrade/downgrade/initiation, last 5 days)
  2. Options flow                   (put/call ratio + unusual institutional volume)
  3. Recency-weighted news          (last 24 h counts most, decays to 0.1 past 7 days)
  4. Short-squeeze potential        (high short float + bullish technicals)

Also captures pre-market / after-hours price change from .info for display.
"""

import csv
import logging
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf

from news_sentiment import (
    _textblob_polarity, _keyword_boost, fetch_google_news, dedupe_news,
)
from options_analysis import fetch_options_flow
from config import SYMBOL_NAME

logger = logging.getLogger(__name__)

_FRESH_WINDOW_DAYS = 5

# How many (freshest) headlines actually feed the sentiment score. Raised from
# 12 when Google News was added -- otherwise the wider coverage was collected
# and then immediately truncated away.
_MAX_SCORED_HEADLINES = 30

# ─────────────────────────────────────────────────────────────────────────────
# Forward-tracking log
# ─────────────────────────────────────────────────────────────────────────────
# The catalyst layer (rating changes / options flow / news / squeeze) can't be
# backtested on historical data — yfinance has no free point-in-time history
# for options chains, rating-change timestamps, or old news (see backtest.py's
# module docstring). The only honest way to validate it is to record what it
# said in real time and check later whether that predicted anything. This
# appends one row per symbol every time the live app refreshes catalysts.

_LOG_PATH = Path(__file__).resolve().parent / "catalyst_log.csv"
_LOG_FIELDS = [
    "timestamp", "symbol", "price", "catalyst_score", "rating_label",
    "options_label", "squeeze_label", "news_score", "earnings_soon",
    "premarket_pct",
]


def _rotate_log_if_schema_changed() -> bool:
    """If an existing log was written with a different set of columns, move it
    aside rather than appending rows that would silently misalign. Returns
    True when a fresh header needs writing."""
    if not _LOG_PATH.exists():
        return True
    try:
        with open(_LOG_PATH, newline="") as f:
            header = (f.readline() or "").strip().split(",")
        if header == _LOG_FIELDS:
            return False
        _LOG_PATH.rename(_LOG_PATH.with_suffix(".prev.csv"))
        logger.info("catalyst_log schema changed -- previous log kept as %s",
                    _LOG_PATH.with_suffix(".prev.csv").name)
        return True
    except Exception:
        return False


def log_catalyst_snapshot(symbol: str, price: float, data: dict) -> None:
    """Append one forward-tracking row. Best-effort — never raises."""
    if price <= 0:
        return
    try:
        is_new = _rotate_log_if_schema_changed()
        with open(_LOG_PATH, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_LOG_FIELDS)
            if is_new:
                writer.writeheader()
            writer.writerow({
                "timestamp":      datetime.now().isoformat(timespec="seconds"),
                "symbol":         symbol,
                "price":          price,
                "catalyst_score": data.get("catalyst_score", 0.0),
                "rating_label":   data.get("rating_label") or "",
                "options_label":  data.get("options_label") or "",
                "squeeze_label":  data.get("squeeze_label") or "",
                "news_score":     data.get("news_score", 0.0),
                "earnings_soon":  data.get("earnings_soon", False),
                # Can't be backtested (yfinance serves only ~30 days of
                # extended-hours data), so it gets forward-logged instead.
                "premarket_pct":  data.get("premarket_pct", 0.0),
            })
    except Exception as e:
        logger.debug("Catalyst log write failed for %s: %s", symbol, e)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Rating-change catalyst
# ─────────────────────────────────────────────────────────────────────────────

def _grade_event_score(action: str, to_grade: str) -> float:
    action = (action or "").lower()
    grade  = (to_grade or "").lower()

    if action == "up":
        return 1.5
    if action == "down":
        return -1.5
    if action == "init":
        if any(w in grade for w in ("buy", "outperform", "overweight")):
            return 1.0
        if any(w in grade for w in ("sell", "underperform", "underweight")):
            return -1.0
    return 0.0


def fetch_rating_catalyst(symbol: str) -> Tuple[float, Optional[str]]:
    """Most significant fresh analyst rating event in the last 5 trading days."""
    try:
        ud = yf.Ticker(symbol).upgrades_downgrades
        if ud is None or ud.empty:
            return 0.0, None

        now    = pd.Timestamp.now()
        recent = ud[ud.index >= now - pd.Timedelta(days=_FRESH_WINDOW_DAYS)]
        if recent.empty:
            return 0.0, None

        total = 0.0
        best_label, best_abs = None, 0.0
        for grade_date, row in recent.iterrows():
            days_ago = max(0.0, (now - grade_date).total_seconds() / 86400)
            decay    = max(0.2, 1 - days_ago / _FRESH_WINDOW_DAYS)
            raw      = _grade_event_score(row.get("Action"), row.get("ToGrade"))
            weighted = raw * decay
            total   += weighted
            if abs(weighted) > best_abs:
                best_abs = abs(weighted)
                action   = (row.get("Action") or "").lower()
                firm     = row.get("Firm", "Analyst")
                if action == "up":
                    best_label = f"↑ {firm} upgrade"
                elif action == "down":
                    best_label = f"↓ {firm} downgrade"
                elif action == "init":
                    best_label = f"• {firm} init {row.get('ToGrade', '')}"

        return max(-2.0, min(2.0, total)), best_label

    except Exception as e:
        logger.debug("Rating catalyst fetch failed for %s: %s", symbol, e)
        return 0.0, None


# ─────────────────────────────────────────────────────────────────────────────
# 2. Recency-weighted news sentiment
# ─────────────────────────────────────────────────────────────────────────────

def _parse_pubdate(item: dict) -> Optional[datetime]:
    try:
        content = item.get("content", {})
        raw = content.get("pubDate") or item.get("providerPublishTime")
        if raw is None:
            return None
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw, tz=timezone.utc)
        return pd.Timestamp(raw).to_pydatetime()
    except Exception:
        return None


def _extract_title(item: dict) -> str:
    if isinstance(item, dict):
        return item.get("title") or item.get("content", {}).get("title", "") or ""
    return getattr(item, "title", "") or ""


def weighted_news_sentiment(news_items: List[dict]) -> Tuple[float, List[str]]:
    """Recency-weighted sentiment; last 24 h counts ~3× more than a week ago."""
    if not news_items:
        return 0.0, []

    now = datetime.now(timezone.utc)
    scored, headlines = [], []

    # Score the freshest items first, not merely the first ones supplied. With
    # two sources merged, ordering by arrival would let one source's stale
    # headlines crowd out the other's fresh ones.
    def _age_hours(item: dict) -> float:
        pub = _parse_pubdate(item)
        return (now - pub).total_seconds() / 3600 if pub else 999.0

    for item in sorted(news_items, key=_age_hours)[:_MAX_SCORED_HEADLINES]:
        title = _extract_title(item)
        if not title:
            continue
        hrs_ago = _age_hours(item)

        if   hrs_ago <= 24:  weight = 1.0
        elif hrs_ago <= 72:  weight = 0.6
        elif hrs_ago <= 168: weight = 0.3
        else:                weight = 0.1

        polarity  = _textblob_polarity(title)
        boost     = _keyword_boost(title)
        sentiment = max(-1.0, min(1.0, polarity + boost))

        scored.append((sentiment, weight))
        headlines.append(title)

    if not scored:
        return 0.0, []

    total_w = sum(w for _, w in scored)
    total_s = sum(s * w for s, w in scored)
    return (total_s / total_w) if total_w > 0 else 0.0, headlines[:6]


# ─────────────────────────────────────────────────────────────────────────────
# 3. Short-squeeze potential
# ─────────────────────────────────────────────────────────────────────────────

def _score_short_squeeze(short_float: float, short_ratio: float) -> Tuple[float, Optional[str]]:
    """
    High short float alone is neutral-to-slightly-bearish (market betting against it).
    High short float + strong upward signal from tech = squeeze candidate (positive).
    The tech_bullish check happens at the call site via the combined catalyst_score.
    Here we just return the raw potential score and let the blend decide.
    """
    pct = short_float * 100

    if pct < 5:
        return 0.0, None

    if pct >= 20:
        label = f"Squeeze {pct:.0f}% short"
        score = min(0.8, (pct - 20) / 25)   # scales 0 → 0.8 from 20% to 45%
        return score, label

    if pct >= 10:
        return 0.0, f"Short {pct:.0f}%"

    return 0.0, None


# ─────────────────────────────────────────────────────────────────────────────
# 4. Pre-market / extended-hours price change
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_extended_info(symbol: str) -> Dict:
    """
    Returns pre-market / after-hours change % and short interest data.
    All sourced from a single .info call to minimise API round-trips.
    """
    defaults = {
        "premarket_pct":   0.0,
        "premarket_price": 0.0,
        "short_float":     0.0,
        "short_ratio":     0.0,
    }
    try:
        info       = yf.Ticker(symbol).info or {}
        prev_close = float(info.get("previousClose") or
                           info.get("regularMarketPreviousClose") or 0.0)
        pm_price   = float(info.get("preMarketPrice") or 0.0)
        pm_pct     = ((pm_price - prev_close) / prev_close * 100
                      if prev_close > 0 and pm_price > 0 else 0.0)
        return {
            "premarket_pct":   pm_pct,
            "premarket_price": pm_price,
            "short_float":     float(info.get("shortPercentOfFloat") or 0.0),
            "short_ratio":     float(info.get("shortRatio") or 0.0),
        }
    except Exception as e:
        logger.debug("Extended info fetch failed for %s: %s", symbol, e)
        return defaults


# ─────────────────────────────────────────────────────────────────────────────
# 5. Earnings proximity
# ─────────────────────────────────────────────────────────────────────────────

def fetch_earnings_proximity(symbol: str, within_days: int = 4) -> bool:
    """True if an earnings report is expected within `within_days` calendar days."""
    try:
        cal = yf.Ticker(symbol).calendar
        if not cal:
            return False
        dates = cal.get("Earnings Date")
        if not dates:
            return False
        today = date.today()
        for d in dates:
            delta = (d - today).days
            if 0 <= delta <= within_days:
                return True
        return False
    except Exception as e:
        logger.debug("Earnings calendar fetch failed for %s: %s", symbol, e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Composite catalyst fetch
# ─────────────────────────────────────────────────────────────────────────────

def fetch_catalyst(symbol: str) -> dict:
    """
    Returns a dict with:
      catalyst_score   float [-2, +2]  — blended signal
      catalyst_label   str | None       — most notable event label
      rating_label     str | None
      options_score    float
      options_label    str | None
      squeeze_score    float
      squeeze_label    str | None
      news_score       float [-1, +1]
      headlines        list[str]
      earnings_soon    bool
      premarket_pct    float            — % change vs prev close (0 outside PM)
      premarket_price  float
      short_float_pct  float            — e.g. 18.5 means 18.5% short
      updated_at       datetime
    """
    # ── Component 1: fresh rating changes ────────────────────────────────────
    rating_score, rating_label = fetch_rating_catalyst(symbol)

    # ── Component 2: options flow ─────────────────────────────────────────────
    options_score, options_label = fetch_options_flow(symbol)

    # ── Component 3: recency-weighted news ───────────────────────────────────
    news_score, headlines = 0.0, []
    news_items = []
    try:
        news_items = list(yf.Ticker(symbol).news or [])
    except Exception as e:
        logger.debug("Yahoo news fetch failed for %s: %s", symbol, e)
    # Widen coverage with Google News; both sources feed the same
    # recency-weighted scorer, de-duplicated by headline.
    news_items += fetch_google_news(symbol, SYMBOL_NAME.get(symbol, ""))
    try:
        news_score, headlines = weighted_news_sentiment(dedupe_news(news_items))
    except Exception as e:
        logger.debug("News scoring failed for %s: %s", symbol, e)

    # ── Component 4: short squeeze + pre-market (one .info call) ─────────────
    ext = _fetch_extended_info(symbol)
    squeeze_score, squeeze_label = _score_short_squeeze(
        ext["short_float"], ext["short_ratio"]
    )

    # ── Earnings proximity ────────────────────────────────────────────────────
    earnings_soon = fetch_earnings_proximity(symbol)

    # ── Blend ─────────────────────────────────────────────────────────────────
    # Weights: rating (±2 scale) × 0.40, options (±2) × 0.30,
    #          news (±1) × 0.20, squeeze (±0.8) × 0.10
    catalyst_score = max(-2.0, min(2.0,
        rating_score  * 0.40 +
        options_score * 0.30 +
        news_score    * 0.20 +
        squeeze_score * 0.10
    ))

    # Best label to surface: priority = rating event > options signal > squeeze
    catalyst_label = rating_label or options_label or squeeze_label

    return {
        "catalyst_score":  catalyst_score,
        "catalyst_label":  catalyst_label,
        "rating_label":    rating_label,
        "options_score":   options_score,
        "options_label":   options_label,
        "squeeze_score":   squeeze_score,
        "squeeze_label":   squeeze_label,
        "news_score":      news_score,
        "headlines":       headlines,
        "earnings_soon":   earnings_soon,
        "premarket_pct":   ext["premarket_pct"],
        "premarket_price": ext["premarket_price"],
        "short_float_pct": ext["short_float"] * 100,
        "updated_at":      datetime.now(),
    }
