#!/usr/bin/env python3
"""
Live Trading Dashboard
======================
Tracks 50 stocks with real-time technical analysis, analyst ratings, and news
sentiment. Displays STRONG BUY / BUY / HOLD / SELL / STRONG SELL signals in a
live-updating Rich terminal UI.

Usage:
    python main.py
    python main.py --help

Keyboard controls (while running):
    W/S (or W/D)  — scroll one row up/down
    PgUp/PgDn     — scroll one page
    Home/End      — jump to top/bottom
    Q / Ctrl+C    — exit
"""

import argparse
import logging
import signal
import sys
import time

# Force UTF-8 output on Windows so box-drawing chars render correctly
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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
from market_data import DataManager
from display import build_dashboard, score_breakdown, compute_visible_rows
from terminal_input import poll_key, ScrollState, enable_raw_mode, disable_raw_mode

console = Console(force_terminal=True, color_system="truecolor")


# ─────────────────────────────────────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────────────────────────────────────

def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Splash / loading screen
# ─────────────────────────────────────────────────────────────────────────────

_SPLASH = """
  ████████╗██████╗  █████╗ ██████╗ ██╗███╗   ██╗ ██████╗     █████╗ ██╗
     ██╔══╝██╔══██╗██╔══██╗██╔══██╗██║████╗  ██║██╔════╝    ██╔══██╗██║
     ██║   ██████╔╝███████║██║  ██║██║██╔██╗ ██║██║  ███╗   ███████║██║
     ██║   ██╔══██╗██╔══██║██║  ██║██║██║╚██╗██║██║   ██║   ██╔══██║██║
     ██║   ██║  ██║██║  ██║██████╔╝██║██║ ╚████║╚██████╔╝   ██║  ██║██║
     ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚═╝╚═╝  ╚═══╝ ╚═════╝    ╚═╝  ╚═╝╚═╝
               Live  •  50 Stocks  •  Technical + Analyst + News
"""


def _print_splash() -> None:
    console.print(Text(_SPLASH, style="bright_cyan"))
    console.print(
        Panel(
            "[bold white]Signals: "
            "[bright_green]STRONG BUY[/bright_green]  "
            "[green]BUY[/green]  "
            "[yellow]HOLD[/yellow]  "
            "[red]SELL[/red]  "
            "[bright_red]STRONG SELL[/bright_red][/bold white]\n"
            "[bright_black]Weights: 65% Technical  •  20% Analyst  •  15% News Sentiment\n"
            "W/S scroll  •  PgUp/PgDn page  •  Home/End jump  •  Q or Ctrl+C to exit[/bright_black]",
            box=box.ROUNDED,
            border_style="bright_cyan",
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main(args: argparse.Namespace) -> None:
    _setup_logging(args.verbose)
    _print_splash()

    dm = DataManager()

    # ── Initial data load ────────────────────────────────────────────────────
    console.print(
        f"\n[bold yellow]  Loading 1 year of daily data for {len(ALL_SYMBOLS)} symbols…[/]"
        " [bright_black](this takes ~10–20 seconds)[/]"
    )

    if not dm.initialize():
        console.print(
            "[bold red]  Failed to load market data. "
            "Check your internet connection and try again.[/bold red]"
        )
        sys.exit(1)

    console.print("[bold green]  Market data loaded! Fetching analyst & news data…[/bold green]")
    time.sleep(0.5)

    # ── Background refresh threads ───────────────────────────────────────────
    dm.start_background_updates()

    # ── Graceful shutdown ────────────────────────────────────────────────────
    def _shutdown(sig, frame):
        dm.stop()
        disable_raw_mode()
        # Live will restore the terminal cursor on exit
        console.print("\n[bold yellow]Shutting down. Goodbye![/bold yellow]")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _shutdown)

    # ── Live display loop ────────────────────────────────────────────────────
    last_update = "—"
    tick_interval = 1.0 / DISPLAY_REFRESH_RATE
    scroll = ScrollState()

    enable_raw_mode()
    try:
        with Live(
            renderable=build_dashboard(dm.get_snapshots(), dm.market_status(), last_update),
            refresh_per_second=DISPLAY_REFRESH_RATE,
            screen=True,
            console=console,
        ) as live:
            while not dm.stop_event.is_set():
                snapshots = dm.get_snapshots()

                # Track most recent price update timestamp
                ts_list = [s.last_price_update for s in snapshots if s.last_price_update]
                if ts_list:
                    last_update = max(ts_list).strftime("%H:%M:%S")

                console_height = console.size.height
                visible_rows = compute_visible_rows(console_height)

                key = poll_key()
                if key == "QUIT":
                    break
                scroll.handle(key, len(snapshots), visible_rows)

                live.update(build_dashboard(
                    snapshots, dm.market_status(), last_update,
                    scroll_offset=scroll.offset, console_height=console_height,
                ))
                time.sleep(tick_interval)
    finally:
        disable_raw_mode()

    dm.stop()
    console.print("\n[bold yellow]Shutting down. Goodbye![/bold yellow]")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Live Trading Dashboard — real-time buy/sell signal analysis",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging (outputs to stderr)",
    )
    return p.parse_args()


if __name__ == "__main__":
    main(_parse_args())
