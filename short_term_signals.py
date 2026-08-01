"""
Rich text/color helpers for the short-term dashboard columns.
"""

from typing import Tuple

from rich.style import Style
from rich.text import Text

from config import SHORT_TECH_WEIGHT, SHORT_CATALYST_WEIGHT
from short_term_analysis import TECH_WEIGHTS


def stoch_text(k: float) -> Text:
    if   k < 20: return Text(f"{k:.0f} OS", style=Style(color="bright_green", bold=True))
    elif k > 80: return Text(f"{k:.0f} OB", style=Style(color="bright_red", bold=True))
    return Text(f"{k:.0f}", style=Style(color="white"))


def roc_text(roc3: float) -> Text:
    label = f"{roc3:+.1f}%"
    if   roc3 >= 3:  style = Style(color="bright_green", bold=True)
    elif roc3 >  0:  style = Style(color="green")
    elif roc3 > -3:  style = Style(color="red")
    else:            style = Style(color="bright_red", bold=True)
    return Text(label, style=style)


def ema_text(arrow: str) -> Text:
    if   "▲▲" in arrow: return Text(arrow, style=Style(color="bright_green", bold=True))
    elif "▲"  in arrow: return Text(arrow, style=Style(color="green"))
    elif "▼▼" in arrow: return Text(arrow, style=Style(color="bright_red", bold=True))
    elif "▼"  in arrow: return Text(arrow, style=Style(color="red"))
    return Text(arrow, style=Style(color="bright_black"))


def candle_text(label: str, score: float) -> Text:
    if score > 0:  style = Style(color="bright_green")
    elif score < 0: style = Style(color="bright_red")
    else:           style = Style(color="bright_black")
    return Text(label, style=style)


def premarket_text(pct: float) -> Text:
    if pct == 0.0:
        return Text("—", style=Style(color="bright_black"))
    label = f"{pct:+.1f}%"
    if   pct >=  1.5: style = Style(color="bright_green", bold=True)
    elif pct >   0.0: style = Style(color="green")
    elif pct > -1.5:  style = Style(color="red")
    else:             style = Style(color="bright_red", bold=True)
    return Text(label, style=style)


def _dominant_technical_reason(snap) -> Tuple[str, float]:
    """Whichever technical indicator (or news) contributes most to final_score.

    Each candidate's contribution is scaled the same way it actually feeds
    final_score, so "dominant" here matches what's really moving the signal.
    """
    candidates = []

    ema_label = "Bullish EMA stack" if snap.ema_score > 0 else "Bearish EMA stack"
    candidates.append((ema_label, snap.ema_score * TECH_WEIGHTS["ema"] * SHORT_TECH_WEIGHT))

    if snap.stoch_k < 20:
        stoch_label_ = "Stoch oversold"
    elif snap.stoch_k > 80:
        stoch_label_ = "Stoch overbought"
    else:
        stoch_label_ = "Stoch bull cross" if snap.stoch_score > 0 else "Stoch bear cross"
    candidates.append((stoch_label_, snap.stoch_score * TECH_WEIGHTS["stoch"] * SHORT_TECH_WEIGHT))

    candidates.append((
        f"{snap.roc3:+.1f}% 3d move",
        snap.roc_score * TECH_WEIGHTS["roc"] * SHORT_TECH_WEIGHT,
    ))

    if snap.gap_score != 0:
        gap_label = "Gap up open" if snap.gap_score > 0 else "Gap down open"
        candidates.append((gap_label, snap.gap_score * TECH_WEIGHTS["gap"] * SHORT_TECH_WEIGHT))

    if snap.vol_score != 0:
        vol_label = f"Vol {snap.volume_ratio:.1f}x up" if snap.vol_score > 0 else f"Vol {snap.volume_ratio:.1f}x down"
        candidates.append((vol_label, snap.vol_score * TECH_WEIGHTS["volume"] * SHORT_TECH_WEIGHT))

    if snap.candle_label != "—":
        candidates.append((snap.candle_label, snap.candle_score * TECH_WEIGHTS["candle"] * SHORT_TECH_WEIGHT))

    if snap.news_score:
        news_label = "Positive news" if snap.news_score > 0 else "Negative news"
        candidates.append((news_label, snap.news_score * 0.20 * SHORT_CATALYST_WEIGHT))

    return max(candidates, key=lambda c: abs(c[1]))


def catalyst_text(
    rating_label,
    options_label,
    squeeze_label,
    catalyst_score: float,
    earnings_soon: bool,
    snap=None,
) -> Text:
    """Explain why a stock's signal is what it is — always returns something,
    never a blank dash. Priority: earnings warning > fresh rating event >
    options flow > squeeze > whichever technical/news factor is actually
    moving the score the most.
    """
    if earnings_soon:
        return Text("⚠ Earnings", style=Style(color="yellow", bold=True))
    # Priority: fresh rating event > options flow > squeeze > dominant driver
    if rating_label:
        style = Style(color="bright_green") if catalyst_score > 0 else Style(color="bright_red")
        return Text(rating_label, style=style)
    if options_label:
        style = Style(color="bright_green") if catalyst_score > 0 else Style(color="bright_red")
        return Text(options_label, style=style)
    if squeeze_label and catalyst_score > 0.2:
        return Text(squeeze_label, style=Style(color="bright_cyan"))

    if snap is not None:
        label, contrib = _dominant_technical_reason(snap)
        if abs(contrib) < 0.03:
            return Text("Neutral — no driver", style=Style(color="bright_black"))
        style = Style(color="bright_green") if contrib > 0 else Style(color="bright_red")
        return Text(label[:20], style=style)

    return Text("—", style=Style(color="bright_black"))


def hold_time_text(hold_time: str) -> Text:
    if "Exit" in hold_time:
        return Text(hold_time, style=Style(color="yellow", bold=True))
    if "Intraday" in hold_time:
        return Text(hold_time, style=Style(color="bright_magenta", bold=True))
    if "3-5" in hold_time:
        return Text(hold_time, style=Style(color="bright_cyan"))
    return Text(hold_time, style=Style(color="cyan"))


def volatility_text(atr_pct: float) -> Text:
    if atr_pct > 4.0:
        return Text(f"{atr_pct:.1f}% HI", style=Style(color="bright_red"))
    if atr_pct > 2.0:
        return Text(f"{atr_pct:.1f}% MED", style=Style(color="yellow"))
    return Text(f"{atr_pct:.1f}% LO", style=Style(color="green"))


def recent_news(snapshots, n: int = 8):
    items = []
    for s in snapshots:
        for h in s.headlines[:2]:
            items.append((s.symbol, h))
    return items[:n]
