"""
News and analyst data fetching + sentiment scoring.

Analyst score: converted from yfinance recommendationMean (1–5) to [-2, +2].
News score   : TextBlob polarity averaged over recent headlines → [-1, +1].
"""

import logging
from datetime import datetime
from typing import Tuple, List

import yfinance as yf

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Keyword boosts applied on top of TextBlob polarity
# ─────────────────────────────────────────────────────────────────────────────
_BULL_WORDS = {
    "surge", "soar", "beat", "beats", "exceed", "upgrade", "outperform",
    "record", "profit", "rally", "breakthrough", "bullish", "buyback",
    "dividend", "growth", "strong", "raises", "guidance", "acquisition",
}
_BEAR_WORDS = {
    "plunge", "plunges", "drop", "miss", "misses", "downgrade", "underperform",
    "loss", "losses", "recall", "investigation", "lawsuit", "warning", "layoffs",
    "bearish", "cut", "cuts", "concerns", "risk", "risks", "decline", "fraud",
}


def _keyword_boost(text: str) -> float:
    """Return a small sentiment boost from domain-specific keywords."""
    words = set(text.lower().split())
    bull = len(words & _BULL_WORDS)
    bear = len(words & _BEAR_WORDS)
    net = bull - bear
    # Cap contribution at ±0.3
    return max(-0.3, min(0.3, net * 0.1))


def _textblob_polarity(text: str) -> float:
    try:
        from textblob import TextBlob
        return TextBlob(text).sentiment.polarity  # -1 to +1
    except Exception:
        return 0.0


def analyze_headlines(headlines: List[str]) -> float:
    """
    Return a sentiment score in [-1, +1] for a list of headlines.
    Combines TextBlob polarity with keyword boosts.
    """
    if not headlines:
        return 0.0
    scores = []
    for h in headlines[:8]:
        polarity = _textblob_polarity(h)
        boost    = _keyword_boost(h)
        scores.append(max(-1.0, min(1.0, polarity + boost)))
    return sum(scores) / len(scores)


# ─────────────────────────────────────────────────────────────────────────────
# Analyst rating helpers
# ─────────────────────────────────────────────────────────────────────────────

_REC_LABELS = {
    "strong_buy":  "Strong Buy",
    "strongbuy":   "Strong Buy",
    "buy":         "Buy",
    "hold":        "Hold",
    "sell":        "Sell",
    "strong_sell": "Strong Sell",
    "strongsell":  "Strong Sell",
    "underperform":"Sell",
    "outperform":  "Buy",
    "overweight":  "Buy",
    "underweight": "Sell",
    "neutral":     "Hold",
    "market perform": "Hold",
}


def rec_mean_to_score(mean: float) -> float:
    """Map yfinance recommendationMean (1=Strong Buy … 5=Strong Sell) to [-2, +2]."""
    return (3.0 - float(mean)) if mean else 0.0


def rec_key_to_label(key: str) -> str:
    clean = (key or "hold").lower().replace(" ", "_")
    return _REC_LABELS.get(clean, "Hold")


# ─────────────────────────────────────────────────────────────────────────────
# Main fetcher
# ─────────────────────────────────────────────────────────────────────────────

def fetch_fundamental(symbol: str) -> dict:
    """
    Fetch analyst ratings and recent news for one symbol.

    Returns a dict with:
      analyst_score   float  [-2, +2]
      analyst_rating  str
      analyst_target  float
      num_analysts    int
      upside_pct      float
      market_cap      float
      pe_ratio        float
      news_score      float  [-1, +1]
      headlines       list[str]
      updated_at      datetime
    """
    result = _default_fundamental(symbol)
    try:
        ticker = yf.Ticker(symbol)
        info   = ticker.info or {}

        # ── Analyst ──────────────────────────────────────────────────────────
        rec_mean = float(info.get("recommendationMean") or 3.0)
        rec_key  = str(info.get("recommendationKey") or "hold")
        num_ana  = int(info.get("numberOfAnalystOpinions") or 0)
        target   = float(info.get("targetMeanPrice") or 0.0)
        price    = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0.0)
        mkt_cap  = float(info.get("marketCap") or 0.0)
        pe       = float(info.get("trailingPE") or 0.0)

        result["analyst_score"]  = rec_mean_to_score(rec_mean)
        result["analyst_rating"] = rec_key_to_label(rec_key)
        result["analyst_target"] = target
        result["num_analysts"]   = num_ana
        result["market_cap"]     = mkt_cap
        result["pe_ratio"]       = pe
        if price > 0 and target > 0:
            result["upside_pct"] = (target - price) / price * 100

        # ── News ─────────────────────────────────────────────────────────────
        try:
            raw_news = ticker.news or []
            headlines = []
            for item in raw_news[:10]:
                # yfinance news items may be dicts or objects depending on version
                if isinstance(item, dict):
                    title = item.get("title") or item.get("content", {}).get("title", "")
                else:
                    title = getattr(item, "title", "")
                if title:
                    headlines.append(str(title))

            result["headlines"]   = headlines[:6]
            result["news_score"]  = analyze_headlines(headlines)
        except Exception as e:
            logger.debug(f"News fetch failed for {symbol}: {e}")

        result["earnings_surprise_score"] = fetch_earnings_surprise(symbol)
        result["updated_at"] = datetime.now()

    except Exception as e:
        logger.warning(f"Fundamental fetch failed for {symbol}: {e}")

    return result


def fetch_earnings_surprise(symbol: str) -> float:
    """
    Score [-1, +1] based on EPS beat/miss history over the last 4 quarters.
    Consistent beats → positive, consistent misses → negative.
    Returns 0.0 if data is unavailable.
    """
    try:
        hist = yf.Ticker(symbol).earnings_history
        if hist is None or hist.empty:
            return 0.0

        recent = hist.tail(4)
        scores = []
        for _, row in recent.iterrows():
            estimate = float(row.get("epsEstimate") or 0.0)
            actual   = float(row.get("epsActual")   or 0.0)
            if estimate == 0:
                continue
            surprise_pct = (actual - estimate) / abs(estimate)
            scores.append(max(-1.0, min(1.0, surprise_pct)))

        return round(sum(scores) / len(scores), 3) if scores else 0.0

    except Exception as e:
        logger.debug("Earnings surprise fetch failed for %s: %s", symbol, e)
        return 0.0


def _default_fundamental(symbol: str) -> dict:
    return {
        "analyst_score":          0.0,
        "analyst_rating":         "Hold",
        "analyst_target":         0.0,
        "num_analysts":           0,
        "upside_pct":             0.0,
        "market_cap":             0.0,
        "pe_ratio":               0.0,
        "news_score":             0.0,
        "headlines":              [],
        "earnings_surprise_score": 0.0,
        "updated_at":             None,
    }
