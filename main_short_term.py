#!/usr/bin/env python3
"""
Short-Term Trading Dashboard
============================
Tracks the same 50-stock watchlist but optimized for 1-5 day swing trades.
Signals come from fast technical patterns (EMA stack, Stochastic, ROC,
candlesticks, volatility) plus FRESH catalysts (analyst upgrades/downgrades
in the last 5 days, recency-weighted news) instead of static long-term
analyst price targets. Also suggests a holding window per stock.

Usage:
    python main_short_term.py
    python main_short_term.py --help

Keyboard controls (while running):
    Up/Down       — scroll one row
    PgUp/PgDn     — scroll one page
    Home/End      — jump to top/bottom
    Q / Ctrl+C    — exit

Auto-trading flags (paper mode by default — fake money):
    --trade        enable auto-trading (paper account)
    --live         use real money instead of paper (requires --trade)

Environment variables needed for auto-trading:
    ALPACA_API_KEY
    ALPACA_SECRET_KEY
"""

import argparse
import logging
import signal
import sys
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Load .env file so ALPACA_API_KEY / ALPACA_SECRET_KEY persist across sessions
try:
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")
except Exception as e:
    print(f"Warning: could not load .env file: {e}", file=sys.stderr)

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich import box

from config import ALL_SYMBOLS, DISPLAY_REFRESH_RATE
from short_term_data import ShortTermDataManager
from short_term_display import build_dashboard, compute_visible_rows
from terminal_input import poll_key, ScrollState
from auto_trader import AutoTrader

console = Console(force_terminal=True, color_system="truecolor")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


_SPLASH = """
  ███████╗██╗  ██╗ ██████╗ ██████╗ ████████╗    ████████╗███████╗██████╗ ███╗   ███╗
  ██╔════╝██║  ██║██╔═══██╗██╔══██╗╚══██╔══╝    ╚══██╔══╝██╔════╝██╔══██╗████╗ ████║
  ███████╗███████║██║   ██║██████╔╝   ██║          ██║   █████╗  ██████╔╝██╔████╔██║
  ╚════██║██╔══██║██║   ██║██╔══██╗   ██║          ██║   ██╔══╝  ██╔══██╗██║╚██╔╝██║
  ███████║██║  ██║╚██████╔╝██║  ██║   ██║          ██║   ███████╗██║  ██║██║ ╚═╝ ██║
  ╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝          ╚═╝   ╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝
                 1-5 Day Horizon  •  Patterns + Fresh Catalysts  •  50 Stocks
"""


def _print_splash() -> None:
    console.print(Text(_SPLASH, style="bright_magenta"))
    console.print(
        Panel(
            "[bold white]Signals: "
            "[bright_green]STRONG BUY[/bright_green]  "
            "[green]BUY[/green]  "
            "[yellow]HOLD[/yellow]  "
            "[red]SELL[/red]  "
            "[bright_red]STRONG SELL[/bright_red][/bold white]\n"
            "[bright_black]Weights: 60% short-term technicals (EMA/Stochastic/ROC/candles) "
            "+ 40% fresh catalysts (rating changes + recent news)\n"
            "HOLD column suggests a holding window, not investment advice.\n"
            "↑↓ scroll  •  PgUp/PgDn page  •  Home/End jump  •  Q or Ctrl+C to exit[/bright_black]",
            box=box.ROUNDED,
            border_style="bright_magenta",
        )
    )


def main(args: argparse.Namespace) -> None:
    _setup_logging(args.verbose)
    _print_splash()

    # ── Auto-trader setup ─────────────────────────────────────────────────────
    trader = None
    if args.trade:
        if args.live:
            console.print(
                Panel(
                    "[bold red]⚠  LIVE TRADING MODE — real money will be used.[/bold red]\n"
                    "[white]Make sure ALPACA_API_KEY and ALPACA_SECRET_KEY are set "
                    "to your LIVE account credentials.[/white]\n"
                    "[bright_black]Press Ctrl+C within 5 seconds to abort...[/bright_black]",
                    box=box.ROUNDED, border_style="bright_red",
                )
            )
            import time as _t; _t.sleep(5)
        mode_label = "[bright_red]LIVE[/bright_red]" if args.live else "[bright_green]PAPER[/bright_green]"
        console.print(f"  Auto-trading enabled — {mode_label} mode")
        trader = AutoTrader(paper=not args.live)

    dm = ShortTermDataManager()

    console.print(
        f"\n[bold yellow]  Loading 3 months of daily data for {len(ALL_SYMBOLS)} symbols…[/]"
        " [bright_black](this takes ~10-20 seconds)[/]"
    )

    if not dm.initialize():
        console.print(
            "[bold red]  Failed to load market data. "
            "Check your internet connection and try again.[/bold red]"
        )
        sys.exit(1)

    console.print("[bold green]  Market data loaded! Fetching catalysts & news…[/bold green]")
    time.sleep(0.5)

    dm.start_background_updates()

    def _shutdown(sig, frame):
        dm.stop()
        console.print("\n[bold yellow]Shutting down. Goodbye![/bold yellow]")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _shutdown)

    last_update = "—"
    tick_interval = 1.0 / DISPLAY_REFRESH_RATE
    scroll = ScrollState()

    with Live(
        renderable=build_dashboard(dm.get_snapshots(), dm.market_status(), last_update),
        refresh_per_second=DISPLAY_REFRESH_RATE,
        screen=True,
        console=console,
    ) as live:
        while not dm.stop_event.is_set():
            snapshots = dm.get_snapshots()
            ts_list = [s.last_price_update for s in snapshots if s.last_price_update]
            if ts_list:
                last_update = max(ts_list).strftime("%H:%M:%S")

            console_height = console.size.height
            visible_rows = compute_visible_rows(console_height)

            key = poll_key()
            if key == "QUIT":
                break
            scroll.handle(key, len(snapshots), visible_rows)

            # Auto-trade check (rate-limited internally to TRADE_CHECK_INTERVAL)
            if trader is not None:
                trader.run(snapshots)

            live.update(build_dashboard(
                snapshots, dm.market_status(), last_update,
                scroll_offset=scroll.offset, console_height=console_height,
                auto_trader=trader,
            ))
            time.sleep(tick_interval)

    dm.stop()
    console.print("\n[bold yellow]Shutting down. Goodbye![/bold yellow]")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Short-Term (1-5 day) Trading Dashboard — pattern + catalyst signals",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    p.add_argument("--trade", action="store_true",
                   help="Enable auto-trading via Alpaca (paper mode by default)")
    p.add_argument("--live", action="store_true",
                   help="Use real Alpaca account instead of paper trading (requires --trade)")
    return p.parse_args()


if __name__ == "__main__":
    main(_parse_args())
