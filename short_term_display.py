"""
Rich terminal dashboard for the short-term (1-5 day horizon) trading system.

Same overall layout as display.py, but the table swaps long-term trend
columns (50/200 SMA, analyst price target) for fast-reacting columns
(Stochastic, ROC, EMA stack, candlestick pattern, fresh catalyst) and adds
a HOLD TIME column with a rule-based suggested holding window.
"""

from datetime import datetime
from typing import List

from rich.console import Console, Group
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.style import Style
from rich import box

from short_term_data import ShortTermSnapshot
from signals import signal_text, score_text, change_text, signal_counts, top_movers
from short_term_signals import (
    stoch_text, roc_text, ema_text, candle_text, catalyst_text,
    hold_time_text, premarket_text, recent_news,
)
from short_term_analysis import ema_arrow
from config import SECTOR_ABBR

_console = Console(force_terminal=True, color_system="truecolor")


# ─────────────────────────────────────────────────────────────────────────────
# Header
# ─────────────────────────────────────────────────────────────────────────────

_STATUS_STYLES = {
    "OPEN":         Style(color="bright_green", bold=True),
    "PRE-MARKET":   Style(color="yellow",       bold=True),
    "AFTER-HOURS":  Style(color="yellow",       bold=True),
    "CLOSED":       Style(color="bright_red",   bold=True),
    "UNKNOWN":      Style(color="bright_black"),
}


def _header(market_status: str, last_update: str) -> Panel:
    now = datetime.now().strftime("%H:%M:%S")
    title = Text()
    title.append("  SHORT-TERM TRADING DASHBOARD  ", style=Style(color="bright_magenta", bold=True))
    title.append("│  ", style=Style(color="bright_black"))
    title.append(f"⏱ {now} ET  ", style=Style(color="white"))
    title.append("│  Market: ", style=Style(color="bright_black"))
    title.append(market_status, style=_STATUS_STYLES.get(market_status, Style()))
    title.append(f"  │  Last data: {last_update}", style=Style(color="bright_black"))
    title.append("  │  Horizon: 1-5 Days", style=Style(color="bright_cyan"))
    return Panel(title, box=box.HEAVY, style=Style(color="bright_magenta"), height=3)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

def _summary(snapshots: List[ShortTermSnapshot]) -> Panel:
    counts = signal_counts(snapshots)
    row = Text()
    parts = [
        ("STRONG BUY",  "bright_green", True),
        ("BUY",         "green",        False),
        ("HOLD",        "yellow",       False),
        ("SELL",        "red",          False),
        ("STRONG SELL", "bright_red",   True),
    ]
    for label, color, bold in parts:
        n = counts[label]
        row.append(f"  {label}: ", style=Style(color=color, bold=bold))
        row.append(str(n), style=Style(color=color, bold=True))

    earnings_warn = sum(1 for s in snapshots if s.earnings_soon)
    row.append("   │   ", style=Style(color="bright_black"))
    row.append(f"⚠ {earnings_warn} reporting earnings soon", style=Style(color="yellow"))

    total = len(snapshots)
    loaded = sum(1 for s in snapshots if not s.is_loading)
    row.append(f"   │   Tracking {loaded}/{total}", style=Style(color="white"))
    return Panel(row, title="[bold]Signal Summary[/bold]", box=box.SIMPLE, height=3)


# ─────────────────────────────────────────────────────────────────────────────
# Main stock table
# ─────────────────────────────────────────────────────────────────────────────

def _sorted_rows(snapshots: List[ShortTermSnapshot]) -> List[ShortTermSnapshot]:
    """Non-loading rows sorted by score desc, loading rows last."""
    loaded  = sorted([s for s in snapshots if not s.is_loading],
                     key=lambda s: s.final_score, reverse=True)
    loading = [s for s in snapshots if s.is_loading]
    return loaded + loading


def compute_visible_rows(console_height: int) -> int:
    """How many table data rows fit given the terminal height.

    Fixed layout overhead: header(3) + summary(3) + bottom panels(10) = 16,
    plus the table panel's own border (2 lines) and header/separator (2
    lines). Clamped to a sane minimum so a tiny terminal doesn't crash.
    """
    return max(5, console_height - 20)


def _stock_table(rows: List[ShortTermSnapshot]) -> Table:
    t = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold bright_white",
        row_styles=["", "dim"],
        expand=True,
    )

    t.add_column("SYM",     style="bold white",   width=6,  no_wrap=True)
    t.add_column("PRICE",   justify="right",       width=9,  no_wrap=True)
    t.add_column("CHG %",   justify="right",       width=8,  no_wrap=True)
    t.add_column("PM %",    justify="right",       width=7,  no_wrap=True)
    t.add_column("STOCH",   justify="center",      width=8,  no_wrap=True)
    t.add_column("ROC 3D",  justify="right",       width=8,  no_wrap=True)
    t.add_column("EMA",     justify="center",      width=4,  no_wrap=True)
    t.add_column("CANDLE",  justify="left",        width=10, no_wrap=True)
    t.add_column("REASON", justify="left",         width=20, no_wrap=True)
    t.add_column("SCORE",   justify="right",       width=7,  no_wrap=True)
    t.add_column("HOLD",    justify="left",        width=16, no_wrap=True)
    t.add_column("SIGNAL",  justify="left",        width=13, no_wrap=True)

    for snap in rows:
        if snap.is_loading:
            dots = Text("…", style=Style(color="bright_black"))
            t.add_row(snap.symbol, dots, dots, dots, dots, dots, dots, dots, dots, dots, dots,
                      Text("Loading…", style=Style(color="bright_black", italic=True)), dots)
            continue

        price_str = f"${snap.price:,.2f}" if snap.price >= 1 else f"${snap.price:.4f}"

        t.add_row(
            snap.symbol,
            price_str,
            change_text(snap.change_pct),
            premarket_text(snap.premarket_pct),
            stoch_text(snap.stoch_k),
            roc_text(snap.roc3),
            ema_text(ema_arrow(snap.price, snap.ema5, snap.ema10, snap.ema20)),
            candle_text(snap.candle_label, snap.candle_score),
            catalyst_text(
                snap.rating_label, snap.options_label, snap.squeeze_label,
                snap.catalyst_score, snap.earnings_soon, snap,
            ),
            score_text(snap.final_score),
            hold_time_text(snap.hold_time),
            signal_text(snap.signal),
        )

    return t


# ─────────────────────────────────────────────────────────────────────────────
# Bottom panels
# ─────────────────────────────────────────────────────────────────────────────

def _movers_panel(snapshots: List[ShortTermSnapshot]) -> Panel:
    gainers, losers = top_movers(snapshots, n=5)
    t = Table(box=None, show_header=False, expand=True, padding=(0, 1))
    t.add_column("sym", style="bold white", width=6)
    t.add_column("pct", justify="right", width=8)
    t.add_column("", width=2)
    t.add_column("sym2", style="bold white", width=6)
    t.add_column("pct2", justify="right", width=8)

    for g, l in zip(gainers, losers):
        t.add_row(
            g.symbol, change_text(g.change_pct),
            Text("│", style=Style(color="bright_black")),
            l.symbol, change_text(l.change_pct),
        )

    header = Text()
    header.append("  GAINERS  ▲", style=Style(color="green", bold=True))
    header.append("              LOSERS  ▼", style=Style(color="red", bold=True))

    return Panel(Group(header, t), title="[bold]Top Movers[/bold]", box=box.SIMPLE)


def _news_panel(snapshots: List[ShortTermSnapshot]) -> Panel:
    items = recent_news(snapshots, n=10)
    t = Table(box=None, show_header=False, expand=True, padding=(0, 0))
    t.add_column("sym", style="bold bright_cyan", width=6, no_wrap=True)
    t.add_column("headline", no_wrap=True)

    if not items:
        t.add_row("", Text("No news loaded yet…", style=Style(color="bright_black", italic=True)))
    else:
        for sym, headline in items:
            t.add_row(f"{sym}:", Text(headline[:80], overflow="ellipsis"))

    return Panel(t, title="[bold]Recent News (recency-weighted)[/bold]", box=box.SIMPLE)


# ─────────────────────────────────────────────────────────────────────────────
# Score breakdown
# ─────────────────────────────────────────────────────────────────────────────

def score_breakdown(snap: ShortTermSnapshot) -> Table:
    t = Table(title=f"Short-Term Score Breakdown — {snap.symbol}", box=box.ROUNDED)
    t.add_column("Indicator", style="bold")
    t.add_column("Value")
    t.add_column("Score", justify="right")
    t.add_column("Weight", justify="right")

    rows = [
        ("EMA stack",    ema_arrow(snap.price, snap.ema5, snap.ema10, snap.ema20), snap.ema_score,    "20%"),
        ("Stochastic",   f"K={snap.stoch_k:.0f} D={snap.stoch_d:.0f}",            snap.stoch_score,  "20%"),
        ("ROC (3d/5d)",  f"{snap.roc3:+.1f}% / {snap.roc5:+.1f}%",               snap.roc_score,    "25%"),
        ("Gap",          f"{snap.gap_pct:+.1f}%",                                  snap.gap_score,    "10%"),
        ("Rel Volume",   f"{snap.volume_ratio:.1f}x",                              snap.vol_score,    "10%"),
        ("Candlestick",  snap.candle_label,                                        snap.candle_score, "15%"),
        ("— Tech Total—","",                                                        snap.short_score,  "60%"),
        ("Rating change",snap.rating_label  or "—",                               snap.catalyst_score, "(×0.40)"),
        ("Options flow", snap.options_label or "—",                               snap.options_score,  "(×0.30)"),
        ("News senti",   f"{snap.news_score:+.2f}",                               snap.news_score,     "(×0.20)"),
        ("Squeeze",      snap.squeeze_label or "—",                               snap.squeeze_score,  "(×0.10)"),
        ("— Catalyst —", "",                                                       snap.catalyst_score, "40%"),
        ("Pre-market",   f"{snap.premarket_pct:+.1f}%",                          snap.premarket_pct,  "display"),
        ("Short float",  f"{snap.short_float_pct:.1f}%",                         snap.short_float_pct * 0.0, "info"),
        ("═ FINAL ═",    "",                                                       snap.final_score,    "—"),
    ]
    for name, val, score, weight in rows:
        style = Style(color="bright_green") if score > 0 else (
                Style(color="bright_red") if score < 0 else Style())
        t.add_row(name, str(val), Text(f"{score:+.3f}", style=style), weight)
    return t


# ─────────────────────────────────────────────────────────────────────────────
# Master layout
# ─────────────────────────────────────────────────────────────────────────────

def _positions_panel(positions: dict, equity: float, buying_power: float,
                     trade_log: List[str]) -> Panel:
    """Shows open Alpaca positions with entry, stop, target, and live P&L."""
    t = Table(box=None, show_header=True, expand=True, padding=(0, 1),
              header_style="bold bright_white")
    t.add_column("SYM",    style="bold white", width=6,  no_wrap=True)
    t.add_column("QTY",    justify="right",    width=5,  no_wrap=True)
    t.add_column("ENTRY",  justify="right",    width=8,  no_wrap=True)
    t.add_column("STOP",   justify="right",    width=8,  no_wrap=True)
    t.add_column("TARGET", justify="right",    width=8,  no_wrap=True)
    t.add_column("P&L",    justify="right",    width=8,  no_wrap=True)

    if not positions:
        t.add_row("", "", "", "", "",
                  Text("No open positions", style=Style(color="bright_black", italic=True)))
    else:
        for sym, pos in positions.items():
            pnl     = pos["pnl_pct"]
            pnl_txt = Text(f"{pnl:+.1f}%",
                           style=Style(color="bright_green" if pnl >= 0 else "bright_red", bold=True))
            t.add_row(
                sym,
                str(int(pos["qty"])),
                f"${pos['entry']:.2f}",
                Text(f"${pos['stop']:.2f}",   style=Style(color="red")),
                Text(f"${pos['target']:.2f}", style=Style(color="green")),
                pnl_txt,
            )

    # Recent trade log (last 3 entries)
    log_lines = Text()
    for line in trade_log[:3]:
        color = "bright_green" if "BOUGHT" in line else "bright_red" if "SOLD" in line else "white"
        log_lines.append(line[:90] + "\n", style=Style(color=color))

    acct_line = Text()
    acct_line.append(f"  Equity: ", style=Style(color="bright_black"))
    acct_line.append(f"${equity:,.0f}", style=Style(color="white", bold=True))
    acct_line.append(f"   Buying power: ", style=Style(color="bright_black"))
    acct_line.append(f"${buying_power:,.0f}", style=Style(color="cyan"))

    from rich.console import Group as RichGroup
    content = RichGroup(acct_line, t, log_lines)
    return Panel(content, title="[bold bright_green]Auto-Trade Positions[/bold bright_green]",
                 box=box.SIMPLE, border_style="bright_green")


def build_dashboard(
    snapshots: List[ShortTermSnapshot],
    market_status: str = "UNKNOWN",
    last_update: str  = "—",
    scroll_offset: int = 0,
    console_height: int = 50,
    auto_trader=None,        # AutoTrader instance or None
) -> Layout:
    trading = auto_trader is not None

    layout = Layout()
    if trading:
        layout.split_column(
            Layout(name="header",    size=3),
            Layout(name="summary",   size=3),
            Layout(name="table",     ratio=3),
            Layout(name="positions", size=12),
            Layout(name="bottom",    size=8),
        )
    else:
        layout.split_column(
            Layout(name="header",  size=3),
            Layout(name="summary", size=3),
            Layout(name="table",   ratio=3),
            Layout(name="bottom",  size=10),
        )

    layout["header"].update(_header(market_status, last_update))
    layout["summary"].update(_summary(snapshots))

    all_rows = _sorted_rows(snapshots)
    total = len(all_rows)
    visible_rows = compute_visible_rows(console_height)
    max_offset = max(0, total - visible_rows)
    offset = max(0, min(scroll_offset, max_offset))
    window = all_rows[offset:offset + visible_rows]
    end = min(offset + visible_rows, total)

    subtitle = (
        f"Showing {offset + 1}-{end} of {total}   │   "
        "[bold]↑↓[/bold] scroll  [bold]PgUp/PgDn[/bold] page  "
        "[bold]Home/End[/bold] jump  [bold]Q[/bold] quit"
    )

    layout["table"].update(
        Panel(
            _stock_table(window),
            title="[bold]Short-Term Signals — sorted by score (1-5 day horizon)[/bold]",
            subtitle=f"[bright_black]{subtitle}[/bright_black]",
            box=box.SIMPLE,
            padding=(0, 0),
        )
    )

    if trading:
        layout["positions"].update(
            _positions_panel(
                auto_trader.positions,
                auto_trader.equity,
                auto_trader.buying_power,
                auto_trader.log,
            )
        )

    layout["bottom"].split_row(
        Layout(name="movers", ratio=1),
        Layout(name="news",   ratio=2),
    )
    layout["bottom"]["movers"].update(_movers_panel(snapshots))
    layout["bottom"]["news"].update(_news_panel(snapshots))

    return layout
