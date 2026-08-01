"""
Rich terminal dashboard for the live trading analysis system.

Layout (top to bottom):
  ┌─ header ─────────────────────────────────────────┐
  │ LIVE TRADING DASHBOARD  •  14:32:05 ET  •  OPEN  │
  ├─ summary ────────────────────────────────────────┤
  │ signal counts  •  market-index snapshot          │
  ├─ stock table ────────────────────────────────────┤
  │ (sorted by final_score, best first)              │
  ├─ movers ──────────────┬─ news ───────────────────┤
  │ top gainers / losers  │ recent headlines         │
  └───────────────────────┴──────────────────────────┘
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

from market_data import StockSnapshot
from signals import (
    signal_text, score_text, change_text,
    rsi_text, macd_text, bb_text, ma_text, analyst_text, reason_text,
    signal_counts, top_movers, recent_news,
)
from technical_analysis import bb_label, ma_arrow, macd_arrow
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
    title.append("  LIVE TRADING DASHBOARD  ", style=Style(color="bright_cyan", bold=True))
    title.append("│  ", style=Style(color="bright_black"))
    title.append(f"⏱ {now} ET  ", style=Style(color="white"))
    title.append("│  Market: ", style=Style(color="bright_black"))
    title.append(market_status, style=_STATUS_STYLES.get(market_status, Style()))
    title.append(f"  │  Last data: {last_update}", style=Style(color="bright_black"))
    return Panel(title, box=box.HEAVY, style=Style(color="bright_cyan"), height=3)


# ─────────────────────────────────────────────────────────────────────────────
# Summary bar
# ─────────────────────────────────────────────────────────────────────────────

def _summary(snapshots: List[StockSnapshot]) -> Panel:
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
    row.append("   │   Tracking ", style=Style(color="bright_black"))
    total = len(snapshots)
    loaded = sum(1 for s in snapshots if not s.is_loading)
    row.append(f"{loaded}/{total} stocks", style=Style(color="white"))
    return Panel(row, title="[bold]Signal Summary[/bold]", box=box.SIMPLE, height=3)


# ─────────────────────────────────────────────────────────────────────────────
# Main stock table
# ─────────────────────────────────────────────────────────────────────────────

def _sorted_rows(snapshots: List[StockSnapshot]) -> List[StockSnapshot]:
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


def _stock_table(rows: List[StockSnapshot]) -> Table:
    t = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold bright_white",
        row_styles=["", "dim"],   # alternating rows
        expand=True,
    )

    t.add_column("SYM",    style="bold white",  width=6,  no_wrap=True)
    t.add_column("SECT",   style="bright_black", width=6,  no_wrap=True)
    t.add_column("PRICE",  justify="right",      width=9,  no_wrap=True)
    t.add_column("CHG %",  justify="right",      width=8,  no_wrap=True)
    t.add_column("RSI",    justify="center",     width=5,  no_wrap=True)
    t.add_column("MACD",   justify="center",     width=5,  no_wrap=True)
    t.add_column("BB",     justify="center",     width=5,  no_wrap=True)
    t.add_column("MA",     justify="center",     width=5,  no_wrap=True)
    t.add_column("ANLST",  justify="left",       width=12, no_wrap=True)
    t.add_column("TGPRC",  justify="right",      width=7,  no_wrap=True)
    t.add_column("UPSD",   justify="right",      width=7,  no_wrap=True)
    t.add_column("REASON", justify="left",       width=20, no_wrap=True)
    t.add_column("SCORE",  justify="right",      width=7,  no_wrap=True)
    t.add_column("SIGNAL", justify="left",       width=13, no_wrap=True)

    for snap in rows:
        if snap.is_loading:
            t.add_row(
                snap.symbol, _abbr(snap.sector),
                Text("…", style=Style(color="bright_black")),
                Text("…"), Text("…"), Text("…"), Text("…"), Text("…"),
                Text("…"), Text("…"), Text("…"), Text("…"), Text("…"),
                Text("Loading…", style=Style(color="bright_black", italic=True)),
            )
            continue

        price_str = f"${snap.price:,.2f}" if snap.price >= 1 else f"${snap.price:.4f}"
        target_str = f"${snap.analyst_target:.0f}" if snap.analyst_target else "—"
        upside_str = (f"{snap.upside_pct:+.1f}%" if snap.upside_pct else "—")
        upside_style = (Style(color="green") if snap.upside_pct > 0
                        else Style(color="red") if snap.upside_pct < 0
                        else Style())

        t.add_row(
            snap.symbol,
            _abbr(snap.sector),
            price_str,
            change_text(snap.change_pct),
            rsi_text(snap.rsi),
            macd_text(snap.macd_hist),
            bb_text(bb_label(snap.price, snap.bb_upper, snap.bb_lower)),
            ma_text(ma_arrow(snap.price, snap.sma_50, snap.sma_200)),
            analyst_text(snap.analyst_rating),
            Text(target_str, justify="right"),
            Text(upside_str, style=upside_style, justify="right"),
            reason_text(snap),
            score_text(snap.final_score),
            signal_text(snap.signal),
        )

    return t


def _abbr(sector: str) -> str:
    return SECTOR_ABBR.get(sector, sector[:5])


# ─────────────────────────────────────────────────────────────────────────────
# Bottom panels
# ─────────────────────────────────────────────────────────────────────────────

def _movers_panel(snapshots: List[StockSnapshot]) -> Panel:
    gainers, losers = top_movers(snapshots, n=5)
    t = Table(box=None, show_header=False, expand=True, padding=(0, 1))
    t.add_column("sym", style="bold white", width=6)
    t.add_column("pct", justify="right", width=8)
    t.add_column("", width=2)
    t.add_column("sym2", style="bold white", width=6)
    t.add_column("pct2", justify="right", width=8)

    for g, l in zip(gainers, losers):
        t.add_row(
            g.symbol,
            change_text(g.change_pct),
            Text("│", style=Style(color="bright_black")),
            l.symbol,
            change_text(l.change_pct),
        )

    header = Text()
    header.append("  GAINERS  ▲", style=Style(color="green", bold=True))
    header.append("              LOSERS  ▼", style=Style(color="red", bold=True))

    return Panel(
        Group(header, t),
        title="[bold]Top Movers[/bold]",
        box=box.SIMPLE,
    )


def _news_panel(snapshots: List[StockSnapshot]) -> Panel:
    items = recent_news(snapshots, n=10)
    t = Table(box=None, show_header=False, expand=True, padding=(0, 0))
    t.add_column("sym", style="bold bright_cyan", width=6,  no_wrap=True)
    t.add_column("headline", no_wrap=True)

    if not items:
        t.add_row("", Text("No news loaded yet…", style=Style(color="bright_black", italic=True)))
    else:
        for sym, headline in items:
            t.add_row(f"{sym}:", Text(headline[:80], overflow="ellipsis"))

    return Panel(t, title="[bold]Recent News[/bold]", box=box.SIMPLE)


# ─────────────────────────────────────────────────────────────────────────────
# Score breakdown tooltip (shown as a small table when requested)
# ─────────────────────────────────────────────────────────────────────────────

def score_breakdown(snap: StockSnapshot) -> Table:
    t = Table(title=f"Score Breakdown — {snap.symbol}", box=box.ROUNDED)
    t.add_column("Indicator", style="bold")
    t.add_column("Value")
    t.add_column("Score", justify="right")
    t.add_column("Weight", justify="right")

    rows = [
        ("RSI",             f"{snap.rsi:.1f}",       snap.rsi_score,              "25%"),
        ("MACD Hist",       f"{snap.macd_hist:.4f}",  snap.macd_score,             "30%"),
        ("Bollinger",       bb_label(snap.price, snap.bb_upper, snap.bb_lower),    snap.bb_score,   "20%"),
        ("Moving Avg",      ma_arrow(snap.price, snap.sma_50, snap.sma_200),       snap.ma_score,   "20%"),
        ("Volume",          f"{snap.volume_ratio:.1f}x",                           snap.vol_score,  "5%"),
        ("— Tech Total—",   "",                                                     snap.tech_score, "60%"),
        ("Analyst",         snap.analyst_rating,                                   snap.analyst_score,           "22%"),
        ("News Senti",      f"{snap.news_score:+.2f}",                             snap.news_score,              "12%"),
        ("Earnings Surp",   f"{snap.earnings_surprise_score:+.2f}",                snap.earnings_surprise_score, "6%"),
        ("═ FINAL ═",       "",                                                     snap.final_score, "—"),
    ]
    for name, val, score, weight in rows:
        style = Style(color="bright_green") if score > 0 else (
                Style(color="bright_red")   if score < 0 else Style())
        t.add_row(name, str(val), Text(f"{score:+.3f}", style=style), weight)
    return t


# ─────────────────────────────────────────────────────────────────────────────
# Master layout builder
# ─────────────────────────────────────────────────────────────────────────────

def build_dashboard(
    snapshots: List[StockSnapshot],
    market_status: str = "UNKNOWN",
    last_update: str  = "—",
    scroll_offset: int = 0,
    console_height: int = 50,
) -> Layout:
    layout = Layout()

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
            title="[bold]Stock Analysis — sorted by signal strength[/bold]",
            subtitle=f"[bright_black]{subtitle}[/bright_black]",
            box=box.SIMPLE,
            padding=(0, 0),
        )
    )

    layout["bottom"].split_row(
        Layout(name="movers", ratio=1),
        Layout(name="news",   ratio=2),
    )
    layout["bottom"]["movers"].update(_movers_panel(snapshots))
    layout["bottom"]["news"].update(_news_panel(snapshots))

    return layout
