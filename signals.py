"""
Signal helpers: colors, labels, and summary counts.
Core scoring logic lives in market_data._compute_signal().
"""

from typing import Tuple

from rich.style import Style
from rich.text import Text

from config import TECH_WEIGHTS, TECHNICAL_WEIGHT, ANALYST_WEIGHT, NEWS_WEIGHT, EARNINGS_WEIGHT
from technical_analysis import bb_label, ma_arrow

# ─────────────────────────────────────────────
# Style constants
# ─────────────────────────────────────────────

SIGNAL_STYLES = {
    "STRONG BUY":  Style(color="bright_green", bold=True),
    "BUY":         Style(color="green"),
    "HOLD":        Style(color="yellow"),
    "SELL":        Style(color="red"),
    "STRONG SELL": Style(color="bright_red", bold=True),
}

SIGNAL_ICONS = {
    "STRONG BUY":  "⬆⬆",
    "BUY":         "⬆ ",
    "HOLD":        "──",
    "SELL":        "⬇ ",
    "STRONG SELL": "⬇⬇",
}

CONFIDENCE_STYLES = {
    "HIGH": Style(color="bright_white", bold=True),
    "MED":  Style(color="white"),
    "LOW":  Style(color="bright_black"),
}


def signal_text(signal: str) -> Text:
    icon  = SIGNAL_ICONS.get(signal, "  ")
    style = SIGNAL_STYLES.get(signal, Style())
    return Text(f"{icon} {signal}", style=style)


def score_text(score: float) -> Text:
    if   score >= 0.8:  style = Style(color="bright_green", bold=True)
    elif score >= 0.3:  style = Style(color="green")
    elif score >= -0.3: style = Style(color="yellow")
    elif score >= -0.8: style = Style(color="red")
    else:               style = Style(color="bright_red", bold=True)
    return Text(f"{score:+.2f}", style=style)


def change_text(pct: float) -> Text:
    sign   = "+" if pct >= 0 else ""
    label  = f"{sign}{pct:.2f}%"
    if   pct >=  2: style = Style(color="bright_green", bold=True)
    elif pct >=  0: style = Style(color="green")
    elif pct >= -2: style = Style(color="red")
    else:           style = Style(color="bright_red", bold=True)
    return Text(label, style=style)


def rsi_text(rsi: float) -> Text:
    label = f"{rsi:.0f}"
    if   rsi < 30: style = Style(color="bright_green", bold=True)
    elif rsi > 70: style = Style(color="bright_red",   bold=True)
    else:          style = Style(color="white")
    return Text(label, style=style)


def macd_text(hist: float) -> Text:
    if   hist > 0: return Text("▲", style=Style(color="green"))
    elif hist < 0: return Text("▼", style=Style(color="red"))
    return Text("─", style=Style(color="bright_black"))


def bb_text(label: str) -> Text:
    styles = {
        "LOW": Style(color="bright_green"),
        "MID": Style(color="white"),
        "HI":  Style(color="bright_red"),
    }
    return Text(label, style=styles.get(label, Style()))


def ma_text(arrow: str) -> Text:
    if   "▲▲" in arrow: return Text(arrow, style=Style(color="bright_green", bold=True))
    elif "▲"  in arrow: return Text(arrow, style=Style(color="green"))
    elif "▼▼" in arrow: return Text(arrow, style=Style(color="bright_red",   bold=True))
    elif "▼"  in arrow: return Text(arrow, style=Style(color="red"))
    return Text(arrow, style=Style(color="bright_black"))


def analyst_text(rating: str) -> Text:
    mapping = {
        "Strong Buy":  Style(color="bright_green", bold=True),
        "Buy":         Style(color="green"),
        "Hold":        Style(color="yellow"),
        "Sell":        Style(color="red"),
        "Strong Sell": Style(color="bright_red", bold=True),
    }
    return Text(rating, style=mapping.get(rating, Style()))


def _dominant_reason(snap) -> Tuple[str, float]:
    """Whichever scoring component contributes most to final_score, scaled the
    same way it actually feeds it -- so "dominant" matches what's really
    moving the signal, not just the largest raw sub-score."""
    candidates = []

    if snap.rsi < 20:   rsi_label = "RSI deeply oversold"
    elif snap.rsi < 30: rsi_label = "RSI oversold"
    elif snap.rsi < 40: rsi_label = "RSI mildly oversold"
    elif snap.rsi > 75: rsi_label = "RSI deeply overbought"
    elif snap.rsi > 65: rsi_label = "RSI overbought"
    elif snap.rsi > 55: rsi_label = "RSI mildly overbought"
    else:               rsi_label = "RSI neutral"
    candidates.append((rsi_label, snap.rsi_score * TECH_WEIGHTS["rsi"] * TECHNICAL_WEIGHT))

    macd_label = "Bullish MACD" if snap.macd_score > 0 else "Bearish MACD"
    candidates.append((macd_label, snap.macd_score * TECH_WEIGHTS["macd"] * TECHNICAL_WEIGHT))

    bb_pos = bb_label(snap.price, snap.bb_upper, snap.bb_lower)
    bb_labels = {"LOW": "Near lower Bollinger Band", "HI": "Near upper Bollinger Band", "MID": "Mid Bollinger Band"}
    candidates.append((bb_labels.get(bb_pos, "Bollinger Band"), snap.bb_score * TECH_WEIGHTS["bollinger"] * TECHNICAL_WEIGHT))

    ma_labels = {
        "▲▲": "Golden cross uptrend", "▲": "Above 50/200-day SMA",
        "─":  "Mixed vs SMAs", "▼": "Below 50-day, above 200-day SMA",
        "▼▼": "Death cross downtrend",
    }
    ma_pos = ma_arrow(snap.price, snap.sma_50, snap.sma_200)
    candidates.append((ma_labels.get(ma_pos, "vs SMAs"), snap.ma_score * TECH_WEIGHTS["ma"] * TECHNICAL_WEIGHT))

    if snap.vol_score != 0:
        vol_label = f"Vol {snap.volume_ratio:.1f}x up" if snap.vol_score > 0 else f"Vol {snap.volume_ratio:.1f}x down"
        candidates.append((vol_label, snap.vol_score * TECH_WEIGHTS["volume"] * TECHNICAL_WEIGHT))

    if snap.num_analysts > 0:
        candidates.append((f"Analysts: {snap.analyst_rating}", snap.analyst_score * ANALYST_WEIGHT))

    if snap.news_score:
        news_label = "Positive news" if snap.news_score > 0 else "Negative news"
        candidates.append((news_label, snap.news_score * NEWS_WEIGHT))

    if snap.earnings_surprise_score:
        earn_label = "Consistent earnings beats" if snap.earnings_surprise_score > 0 else "Earnings misses"
        candidates.append((earn_label, snap.earnings_surprise_score * EARNINGS_WEIGHT))

    return max(candidates, key=lambda c: abs(c[1]))


def reason_text(snap) -> Text:
    """Explain why a stock's signal is what it is -- always returns something,
    never a blank dash. Picks whichever indicator/analyst/news/earnings factor
    is actually moving the score the most."""
    label, contrib = _dominant_reason(snap)
    if abs(contrib) < 0.02:
        return Text("Neutral — no driver", style=Style(color="bright_black"))
    style = Style(color="bright_green") if contrib > 0 else Style(color="bright_red")
    return Text(label[:20], style=style)


# ─────────────────────────────────────────────
# Summary helpers
# ─────────────────────────────────────────────

def signal_counts(snapshots) -> dict:
    counts = {"STRONG BUY": 0, "BUY": 0, "HOLD": 0, "SELL": 0, "STRONG SELL": 0}
    for s in snapshots:
        if s.signal in counts:
            counts[s.signal] += 1
    return counts


def top_movers(snapshots, n: int = 5):
    loaded = [s for s in snapshots if not s.is_loading and s.price > 0]
    gainers = sorted(loaded, key=lambda s: s.change_pct, reverse=True)[:n]
    losers  = sorted(loaded, key=lambda s: s.change_pct)[:n]
    return gainers, losers


def recent_news(snapshots, n: int = 8):
    """Collect the most recent headlines across all stocks."""
    items = []
    for s in snapshots:
        for h in s.headlines[:2]:
            items.append((s.symbol, h))
    return items[:n]
