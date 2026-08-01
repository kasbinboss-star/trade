#!/usr/bin/env python3
"""
GUI dashboard (Tkinter) for the short-term (1-5 day) trading system, with
live Alpaca paper auto-trading and a performance panel.

Native, scrollable table (real Treeview scrollbar + mouse wheel + arrow/
Page Up/Page Down keys) so scrolling doesn't depend on raw-terminal
escape-sequence parsing at all.

Auto-trading is ALWAYS in paper mode here (fake money) -- this window has
no live-trading option. Requires ALPACA_API_KEY / ALPACA_SECRET_KEY set in
.env; without them, trading is skipped and the dashboard runs read-only.

Usage:
    python gui_dashboard_short_term.py
"""

from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

import os

from config import ALL_SYMBOLS, DISPLAY_REFRESH_RATE
from short_term_data import ShortTermDataManager
from short_term_signals import catalyst_text
from short_term_analysis import ema_arrow
from auto_trader import AutoTrader

import tkinter as tk
from tkinter import ttk

REFRESH_MS = max(200, int(1000 / DISPLAY_REFRESH_RATE))

SIGNAL_COLORS = {
    "STRONG BUY":  "#0a7d2c",
    "BUY":         "#2e8b3d",
    "HOLD":        "#8a7f00",
    "SELL":        "#a33b2b",
    "STRONG SELL": "#8b0000",
}
SIGNAL_FG = {
    "STRONG BUY": "white", "BUY": "white", "HOLD": "black",
    "SELL": "white", "STRONG SELL": "white",
}

COLUMNS = [
    ("sym", "SYM", 70, "w"), ("price", "PRICE", 90, "e"), ("chg", "CHG %", 80, "e"),
    ("pm", "PM %", 70, "e"), ("stoch", "STOCH", 70, "center"), ("roc", "ROC 3D", 80, "e"),
    ("ema", "EMA", 55, "center"), ("candle", "CANDLE", 100, "w"), ("reason", "REASON", 200, "w"),
    ("score", "SCORE", 70, "e"), ("hold", "HOLD", 140, "w"), ("signal", "SIGNAL", 110, "w"),
]

POS_COLUMNS = [
    ("sym", "SYM", 60, "w"), ("qty", "QTY", 50, "e"), ("entry", "ENTRY", 75, "e"),
    ("current", "NOW", 75, "e"), ("pnl", "P&L $", 75, "e"), ("pnlpct", "P&L %", 65, "e"),
]


def _stoch_label(k: float) -> str:
    if k < 20:  return f"{k:.0f} OS"
    if k > 80:  return f"{k:.0f} OB"
    return f"{k:.0f}"


def _row_values(snap):
    price_str = f"${snap.price:,.2f}" if snap.price >= 1 else f"${snap.price:.4f}"
    pm_str = f"{snap.premarket_pct:+.1f}%" if snap.premarket_pct else "—"
    reason = catalyst_text(
        snap.rating_label, snap.options_label, snap.squeeze_label,
        snap.catalyst_score, snap.earnings_soon, snap,
    ).plain
    return (
        snap.symbol,
        price_str,
        f"{snap.change_pct:+.2f}%",
        pm_str,
        _stoch_label(snap.stoch_k),
        f"{snap.roc3:+.1f}%",
        ema_arrow(snap.price, snap.ema5, snap.ema10, snap.ema20),
        snap.candle_label,
        reason,
        f"{snap.final_score:+.2f}",
        snap.hold_time,
        snap.signal,
    )


class DashboardWindow:
    def __init__(self, dm: ShortTermDataManager, trader: AutoTrader | None):
        self.dm = dm
        self.trader = trader
        self.root = tk.Tk()
        self.root.title("Short-Term Trading Dashboard" + (" — PAPER TRADING ACTIVE" if trader else ""))
        self.root.geometry("1850x850")

        header = ttk.Frame(self.root, padding=6)
        header.pack(fill="x")
        self.status_var = tk.StringVar(value="Loading…")
        ttk.Label(header, textvariable=self.status_var, font=("Menlo", 12, "bold")).pack(side="left")
        if trader:
            ttk.Label(header, text="  ●  PAPER AUTO-TRADING ACTIVE  ●  ",
                      foreground="white", background="#0a7d2c",
                      font=("Menlo", 11, "bold")).pack(side="right")

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True)

        # ── Main signal table (left) ────────────────────────────────────────
        table_frame = ttk.Frame(body)
        table_frame.pack(side="left", fill="both", expand=True)

        col_ids = [c[0] for c in COLUMNS]
        self.tree = ttk.Treeview(table_frame, columns=col_ids, show="headings", selectmode="browse")
        for col_id, label, width, anchor in COLUMNS:
            self.tree.heading(col_id, text=label)
            self.tree.column(col_id, width=width, anchor=anchor)

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        for sig, color in SIGNAL_COLORS.items():
            self.tree.tag_configure(sig, background=color, foreground=SIGNAL_FG[sig])
        self.tree.tag_configure("LOADING", foreground="#888888")
        self.tree.focus_set()

        # ── Performance panel (right) ───────────────────────────────────────
        if trader:
            perf = ttk.Frame(body, padding=(10, 4), width=420)
            perf.pack(side="right", fill="y")
            perf.pack_propagate(False)

            ttk.Label(perf, text="ALPACA PAPER ACCOUNT", font=("Menlo", 13, "bold")).pack(anchor="w", pady=(0, 6))

            self.equity_var = tk.StringVar(value="Equity: —")
            self.daypnl_var = tk.StringVar(value="Day P&L: —")
            self.bp_var = tk.StringVar(value="Buying power: —")
            self.openpos_var = tk.StringVar(value="Open positions: —")

            ttk.Label(perf, textvariable=self.equity_var, font=("Menlo", 14, "bold")).pack(anchor="w", pady=1)
            self.daypnl_label = ttk.Label(perf, textvariable=self.daypnl_var, font=("Menlo", 13, "bold"))
            self.daypnl_label.pack(anchor="w", pady=1)
            ttk.Label(perf, textvariable=self.bp_var, font=("Menlo", 11)).pack(anchor="w", pady=1)
            ttk.Label(perf, textvariable=self.openpos_var, font=("Menlo", 11)).pack(anchor="w", pady=1)

            ttk.Separator(perf, orient="horizontal").pack(fill="x", pady=8)
            ttk.Label(perf, text="OPEN POSITIONS", font=("Menlo", 12, "bold")).pack(anchor="w")

            pos_ids = [c[0] for c in POS_COLUMNS]
            self.pos_tree = ttk.Treeview(perf, columns=pos_ids, show="headings", height=8, selectmode="none")
            for col_id, label, width, anchor in POS_COLUMNS:
                self.pos_tree.heading(col_id, text=label)
                self.pos_tree.column(col_id, width=width, anchor=anchor)
            self.pos_tree.tag_configure("gain", foreground="#1a9c3d")
            self.pos_tree.tag_configure("loss", foreground="#c0392b")
            self.pos_tree.pack(fill="x", pady=(4, 8))

            ttk.Separator(perf, orient="horizontal").pack(fill="x", pady=4)
            ttk.Label(perf, text="TRADE LOG", font=("Menlo", 12, "bold")).pack(anchor="w")
            self.log_text = tk.Text(perf, height=14, font=("Menlo", 10), wrap="word", state="disabled")
            self.log_text.pack(fill="both", expand=True, pady=(4, 0))

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(200, self._refresh)

    def _refresh(self):
        snapshots = self.dm.get_snapshots()
        loaded = sorted((s for s in snapshots if not s.is_loading),
                         key=lambda s: s.final_score, reverse=True)
        loading = [s for s in snapshots if s.is_loading]

        yview_top = self.tree.yview()[0]

        self.tree.delete(*self.tree.get_children())
        for s in loaded:
            self.tree.insert("", "end", iid=s.symbol, values=_row_values(s), tags=(s.signal,))
        for s in loading:
            self.tree.insert(
                "", "end", iid=s.symbol,
                values=(s.symbol, "…", "…", "…", "…", "…", "…", "…", "Loading…", "…", "…", "…"),
                tags=("LOADING",),
            )

        self.tree.yview_moveto(yview_top)

        status = self.dm.market_status()
        earnings_warn = sum(1 for s in loaded if s.earnings_soon)
        n = len(loaded)
        self.status_var.set(
            f"Market: {status}   |   Loaded {n}/{len(snapshots)} stocks   |   "
            f"⚠ {earnings_warn} reporting earnings soon   |   "
            f"Scroll: mouse wheel, arrow keys, or Page Up/Down"
        )

        if self.trader is not None:
            self.trader.run(snapshots)
            self._refresh_performance()

        self.root.after(REFRESH_MS, self._refresh)

    def _refresh_performance(self):
        t = self.trader
        self.equity_var.set(f"Equity: ${t.equity:,.2f}")
        self.daypnl_var.set(f"Day P&L: {t.day_pnl:+,.2f} ({t.day_pnl_pct:+.2f}%)")
        self.daypnl_label.configure(foreground="#1a9c3d" if t.day_pnl >= 0 else "#c0392b")
        self.bp_var.set(f"Buying power: ${t.buying_power:,.2f}")
        self.openpos_var.set(f"Open positions: {len(t.positions)}")

        self.pos_tree.delete(*self.pos_tree.get_children())
        for sym, pos in t.positions.items():
            pnl = pos["pnl_pct"]
            entry = pos["entry"]
            current = pos.get("current", entry)
            pnl_dollars = (current - entry) * pos["qty"]
            tag = "gain" if pnl >= 0 else "loss"
            self.pos_tree.insert("", "end", values=(
                sym, int(pos["qty"]), f"${entry:.2f}", f"${current:.2f}",
                f"{pnl_dollars:+.2f}", f"{pnl:+.1f}%",
            ), tags=(tag,))

        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        for line in t.log[:20]:
            self.log_text.insert("end", line + "\n")
        self.log_text.configure(state="disabled")

    def _on_close(self):
        self.dm.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    dm = ShortTermDataManager()
    print(f"Loading 3 months of daily data for {len(ALL_SYMBOLS)} symbols…")
    if not dm.initialize():
        print("Failed to load market data. Check your internet connection and try again.")
        return
    dm.start_background_updates()

    trader = None
    if os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_SECRET_KEY"):
        print("Alpaca keys found — enabling PAPER auto-trading.")
        trader = AutoTrader(paper=True)
    else:
        print("No Alpaca keys found in .env — running dashboard-only (no auto-trading).")

    win = DashboardWindow(dm, trader)
    win.run()


if __name__ == "__main__":
    main()
