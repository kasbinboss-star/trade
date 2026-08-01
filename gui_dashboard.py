#!/usr/bin/env python3
"""
GUI dashboard (Tkinter) for the long-term trading system.

A native, scrollable alternative to the terminal Rich UI in main.py.
Scrolling here is an ordinary Treeview scrollbar + mouse wheel + arrow/
Page Up/Page Down keys, all handled by Tk itself -- no raw-terminal
escape-sequence parsing involved, so it isn't sensitive to how the
terminal/launcher handles stdin.

Usage:
    python gui_dashboard.py
"""

from config import ALL_SYMBOLS, DISPLAY_REFRESH_RATE, SECTOR_ABBR
from market_data import DataManager
from signals import reason_text
from technical_analysis import bb_label, ma_arrow

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
    ("sym", "SYM", 70, "w"), ("sect", "SECT", 70, "center"), ("price", "PRICE", 90, "e"),
    ("chg", "CHG %", 80, "e"), ("rsi", "RSI", 55, "center"), ("macd", "MACD", 55, "center"),
    ("bb", "BB", 55, "center"), ("ma", "MA", 55, "center"), ("analyst", "ANALYST", 100, "w"),
    ("target", "TGT", 70, "e"), ("upside", "UPSD", 70, "e"), ("reason", "REASON", 190, "w"),
    ("score", "SCORE", 70, "e"), ("signal", "SIGNAL", 110, "w"),
]


def _row_values(snap):
    price_str = f"${snap.price:,.2f}" if snap.price >= 1 else f"${snap.price:.4f}"
    target_str = f"${snap.analyst_target:.0f}" if snap.analyst_target else "—"
    upside_str = f"{snap.upside_pct:+.1f}%" if snap.upside_pct else "—"
    bb_pos = bb_label(snap.price, snap.bb_upper, snap.bb_lower)
    ma_pos = ma_arrow(snap.price, snap.sma_50, snap.sma_200)
    return (
        snap.symbol,
        SECTOR_ABBR.get(snap.sector, snap.sector[:5]),
        price_str,
        f"{snap.change_pct:+.2f}%",
        f"{snap.rsi:.0f}",
        f"{snap.macd_hist:+.3f}",
        bb_pos,
        ma_pos,
        snap.analyst_rating,
        target_str,
        upside_str,
        reason_text(snap).plain,
        f"{snap.final_score:+.2f}",
        snap.signal,
    )


class DashboardWindow:
    def __init__(self, dm: DataManager):
        self.dm = dm
        self.root = tk.Tk()
        self.root.title("Live Trading Dashboard")
        self.root.geometry("1500x820")

        header = ttk.Frame(self.root, padding=6)
        header.pack(fill="x")
        self.status_var = tk.StringVar(value="Loading…")
        ttk.Label(header, textvariable=self.status_var, font=("Menlo", 12, "bold")).pack(side="left")

        table_frame = ttk.Frame(self.root)
        table_frame.pack(fill="both", expand=True)

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
                values=(s.symbol, "", "…", "…", "…", "…", "…", "…", "…", "…", "…", "Loading…", "…", "…"),
                tags=("LOADING",),
            )

        self.tree.yview_moveto(yview_top)

        status = self.dm.market_status()
        n = len(loaded)
        self.status_var.set(f"Market: {status}   |   Loaded {n}/{len(snapshots)} stocks   "
                             f"|   Scroll: mouse wheel, arrow keys, or Page Up/Down")

        self.root.after(REFRESH_MS, self._refresh)

    def _on_close(self):
        self.dm.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    dm = DataManager()
    print(f"Loading 1 year of daily data for {len(ALL_SYMBOLS)} symbols…")
    if not dm.initialize():
        print("Failed to load market data. Check your internet connection and try again.")
        return
    dm.start_background_updates()

    win = DashboardWindow(dm)
    win.run()


if __name__ == "__main__":
    main()
