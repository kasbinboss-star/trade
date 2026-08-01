#!/usr/bin/env python3
"""
Backtest & validate the short-term TECHNICAL scoring layer.

Only the technical layer (short_term_analysis.calculate_short_term_score) can be
faithfully backtested: yfinance has no free point-in-time history for options
chains, analyst-rating-change timestamps, or old news headlines, so the
catalyst layer (catalyst_analysis.py, the other 40% of the live score) is out
of scope here on purpose — see catalyst_log.csv, which tracks it going forward
instead of backward.

Methodology, in one pass:
  1. Simulate point-in-time technical scores across history with NO lookahead,
     by feeding calculate_short_term_score() (the actual production function)
     a truncated trailing window ending at each day t.
  2. Split into an in-sample period and an untouched out-of-sample holdout.
  3. Compute each factor's Information Coefficient (rank correlation with
     forward returns) in-sample.
  4. Propose a re-weighting via one bounded, closed-form transform (not a
     search/optimization loop).
  5. Accept the new weights ONLY if they beat the current weights on BOTH
     tails (BUY bucket and SELL bucket) on the out-of-sample holdout.

Usage:
    python backtest.py                     # IC report only
    python backtest.py --propose-weights   # + weight proposal + OOS verdict
    python backtest.py --refresh-data
    python backtest.py --years 5 --oos-years 2 --tx-cost-bps 7.5
"""

import argparse
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf
from rich.console import Console
from rich.table import Table
from rich import box

from config import (
    ALL_SYMBOLS,
    SHORT_STRONG_BUY_THRESHOLD, SHORT_BUY_THRESHOLD,
    SHORT_SELL_THRESHOLD, SHORT_STRONG_SELL_THRESHOLD,
)
from short_term_analysis import calculate_short_term_score, TECH_WEIGHTS

console = Console()

CACHE_DIR = Path(__file__).resolve().parent / ".backtest_cache"
WARMUP_DAYS = 25          # below this, stoch/roc/volume-ratio sit on degenerate defaults
LOOKBACK_WINDOW = 260     # ~1 trading year — EMA20/Stoch14/ROC5/ATR14 all converge well within this
HORIZONS = (1, 3, 5)
DECISION_HORIZON = 3

FACTOR_COLS = ["ema_score", "stoch_score", "roc_score", "gap_score", "vol_score", "candle_score"]
FACTOR_TO_WEIGHT_KEY = {
    "ema_score": "ema", "stoch_score": "stoch", "roc_score": "roc",
    "gap_score": "gap", "vol_score": "volume", "candle_score": "candle",
}
BUCKET_ORDER = ["STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL"]
TRADE_BUCKETS = {"STRONG BUY", "BUY", "SELL", "STRONG SELL"}


# ─────────────────────────────────────────────────────────────────────────────
# Data loading & caching
# ─────────────────────────────────────────────────────────────────────────────

def _cache_path(symbol: str, years: int) -> Path:
    return CACHE_DIR / f"{symbol}_{years}y.csv"


def _cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    return (time.time() - path.stat().st_mtime) / 3600 < 24


def _extract(raw: pd.DataFrame, sym: str) -> Optional[pd.DataFrame]:
    try:
        cols = raw.columns
        if isinstance(cols, pd.MultiIndex):
            if sym in cols.get_level_values(1):
                df = raw.xs(sym, axis=1, level=1)
            elif sym in cols.get_level_values(0):
                df = raw[sym]
            else:
                return None
        else:
            df = raw
        return df.dropna(how="all")
    except Exception:
        return None


def load_history(symbols: List[str], years: int, refresh: bool) -> Dict[str, pd.DataFrame]:
    CACHE_DIR.mkdir(exist_ok=True)
    result: Dict[str, pd.DataFrame] = {}
    to_fetch = []
    for sym in symbols:
        p = _cache_path(sym, years)
        if not refresh and _cache_fresh(p):
            try:
                df = pd.read_csv(p, index_col=0, parse_dates=True)
                if len(df) > WARMUP_DAYS + max(HORIZONS):
                    result[sym] = df
                    continue
            except Exception:
                pass
        to_fetch.append(sym)

    if to_fetch:
        console.print(f"[yellow]Downloading {len(to_fetch)} symbols ({years}y daily)…[/yellow]")
        raw = yf.download(tickers=to_fetch, period=f"{years}y", interval="1d",
                           auto_adjust=True, progress=False, threads=True)
        for sym in to_fetch:
            df = _extract(raw, sym)
            if df is not None and len(df) > WARMUP_DAYS + max(HORIZONS):
                df.to_csv(_cache_path(sym, years))
                result[sym] = df
            else:
                console.print(f"[bright_black]  skipping {sym}: insufficient history[/bright_black]")

    return result


# ─────────────────────────────────────────────────────────────────────────────
# No-lookahead simulation — reuses the production scoring function directly
# ─────────────────────────────────────────────────────────────────────────────

def simulate_scores(df: pd.DataFrame) -> pd.DataFrame:
    """
    For every day t, score using ONLY a trailing window ending at t (bounded to
    LOOKBACK_WINDOW bars for speed — negligible effect since every indicator here
    converges well within a year of daily bars), then record forward returns.
    Because calculate_short_term_score() only ever reads backward from the last
    row of whatever it's given, this can't leak future data by construction.
    """
    rows = []
    n = len(df)
    last_t = n - max(HORIZONS) - 1
    for t in range(WARMUP_DAYS, last_t + 1):
        start = max(0, t + 1 - LOOKBACK_WINDOW)
        window = df.iloc[start:t + 1]
        tech = calculate_short_term_score(window)
        price = tech["price"]
        if price <= 0:
            continue

        row = {"date": df.index[t]}
        for f in FACTOR_COLS:
            row[f] = tech[f]

        for h in HORIZONS:
            fut_close = float(df["Close"].iloc[t + h])
            row[f"fwd_ret_{h}d"] = fut_close / price - 1

        # Realistic-lag variant: signal seen at close[t], acted on at next open
        next_open = float(df["Open"].iloc[t + 1])
        for h in HORIZONS:
            fut_close = float(df["Close"].iloc[t + h])
            row[f"fwd_ret_lag_{h}d"] = fut_close / next_open - 1 if next_open > 0 else np.nan

        rows.append(row)

    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Information Coefficient analysis
# ─────────────────────────────────────────────────────────────────────────────

def _spearman(a: pd.Series, b: pd.Series) -> float:
    """Spearman rank correlation = Pearson correlation of the ranks.
    Avoids pandas' method="spearman", which lazily requires scipy (not a
    project dependency) under the hood."""
    return a.rank().corr(b.rank())


def compute_ic(panel: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for factor in FACTOR_COLS:
        for h in HORIZONS:
            ret_col = f"fwd_ret_{h}d"
            sub = panel[[factor, ret_col]].dropna()
            pooled_ic = _spearman(sub[factor], sub[ret_col]) if len(sub) > 30 else np.nan

            daily_ics = []
            for _, grp in panel.groupby("date"):
                g = grp[[factor, ret_col]].dropna()
                if len(g) >= 8 and g[factor].nunique() > 1:
                    ic = _spearman(g[factor], g[ret_col])
                    if pd.notna(ic):
                        daily_ics.append(ic)
            daily_mean = float(np.mean(daily_ics)) if daily_ics else np.nan
            daily_std = float(np.std(daily_ics)) if daily_ics else np.nan

            rows.append({
                "factor": factor, "horizon": h,
                "pooled_ic": pooled_ic,
                "daily_ic_mean": daily_mean,
                "daily_ic_ir": (daily_mean / daily_std) if daily_std else np.nan,
                "n": len(sub),
            })
    return pd.DataFrame(rows)


def factor_diagnosis(ic_df: pd.DataFrame, factor: str) -> str:
    rows = ic_df[ic_df.factor == factor].set_index("horizon")
    if DECISION_HORIZON not in rows.index or pd.isna(rows.loc[DECISION_HORIZON, "pooled_ic"]):
        return "NO DATA"
    pooled_3d = rows.loc[DECISION_HORIZON, "pooled_ic"]
    if abs(pooled_3d) <= 0.03:
        return "WEAK (|IC|<=0.03)"
    pooled_signs = np.sign(rows["pooled_ic"].dropna())
    if pooled_signs.nunique() > 1:
        return "INCONSISTENT SIGN across horizons"
    daily_3d = rows.loc[DECISION_HORIZON, "daily_ic_mean"]
    if pd.isna(daily_3d) or np.sign(daily_3d) != np.sign(pooled_3d):
        return "INCONSISTENT SIGN (pooled vs daily)"
    return "PASS"


def passes_materiality(ic_df: pd.DataFrame, factor: str) -> bool:
    return factor_diagnosis(ic_df, factor) == "PASS"


# ─────────────────────────────────────────────────────────────────────────────
# Bounded reweighting (single closed-form pass, not a search)
# ─────────────────────────────────────────────────────────────────────────────

def propose_weights(ic_df: pd.DataFrame, current: Dict[str, float],
                     shrink: float = 0.3, max_delta: float = 0.15) -> Dict[str, float]:
    raw = {}
    for factor in FACTOR_COLS:
        key = FACTOR_TO_WEIGHT_KEY[factor]
        rows = ic_df[ic_df.factor == factor].set_index("horizon")
        pooled_3d = rows.loc[DECISION_HORIZON, "pooled_ic"] if DECISION_HORIZON in rows.index else np.nan
        if passes_materiality(ic_df, factor) and pd.notna(pooled_3d):
            raw[key] = abs(pooled_3d)
        else:
            raw[key] = current[key] * shrink

    total = sum(raw.values())
    normalized = {k: v / total for k, v in raw.items()}

    clipped = {}
    for k, v in normalized.items():
        delta = v - current[k]
        if delta > max_delta:
            clipped[k] = current[k] + max_delta
        elif delta < -max_delta:
            clipped[k] = current[k] - max_delta
        else:
            clipped[k] = v
    total2 = sum(clipped.values())
    return {k: v / total2 for k, v in clipped.items()}


def apply_weights(panel: pd.DataFrame, weights: Dict[str, float]) -> pd.Series:
    score = pd.Series(0.0, index=panel.index)
    for factor in FACTOR_COLS:
        key = FACTOR_TO_WEIGHT_KEY[factor]
        score = score + panel[factor] * weights[key]
    return score


def bucket_signal(score: pd.Series) -> pd.Series:
    def _b(x: float) -> str:
        if x >= SHORT_STRONG_BUY_THRESHOLD: return "STRONG BUY"
        if x >= SHORT_BUY_THRESHOLD:        return "BUY"
        if x >= SHORT_SELL_THRESHOLD:       return "HOLD"
        if x >= SHORT_STRONG_SELL_THRESHOLD: return "SELL"
        return "STRONG SELL"
    return score.apply(_b)


def evaluate_weights(panel: pd.DataFrame, weights: Dict[str, float], tx_cost_bps: float) -> pd.DataFrame:
    """Technical-only proxy signal (short_score alone, existing thresholds) —
    distinct from the live blended final_score, since catalyst can't be
    reconstructed historically."""
    score = apply_weights(panel, weights)
    buckets = bucket_signal(score)
    rows = []
    for h in HORIZONS:
        ret_col = f"fwd_ret_{h}d"
        for b in BUCKET_ORDER:
            sub = panel.loc[buckets == b, ret_col].dropna()
            if len(sub) == 0:
                continue
            cost = tx_cost_bps / 10000 if b in TRADE_BUCKETS else 0.0
            net = sub - cost
            rows.append({
                "horizon": h, "bucket": b, "n": len(sub),
                "mean_gross": sub.mean(), "mean_net": net.mean(),
                "median": sub.median(), "std": sub.std(),
                "win_rate": (sub > 0).mean(),
                "reward_risk": (sub.mean() / sub.std()) if sub.std() else np.nan,
            })
    return pd.DataFrame(rows)


def _weighted_bucket_mean(df: pd.DataFrame, buckets: List[str], horizon: int) -> Optional[float]:
    sub = df[(df.horizon == horizon) & (df.bucket.isin(buckets))]
    if sub.empty or sub["n"].sum() == 0:
        return None
    return float((sub["mean_net"] * sub["n"]).sum() / sub["n"].sum())


def check_acceptance(old_df: pd.DataFrame, new_df: pd.DataFrame,
                      horizon: int = DECISION_HORIZON, min_improve: float = 0.0005) -> bool:
    old_buy = _weighted_bucket_mean(old_df, ["BUY", "STRONG BUY"], horizon)
    new_buy = _weighted_bucket_mean(new_df, ["BUY", "STRONG BUY"], horizon)
    old_sell = _weighted_bucket_mean(old_df, ["SELL", "STRONG SELL"], horizon)
    new_sell = _weighted_bucket_mean(new_df, ["SELL", "STRONG SELL"], horizon)

    if None in (old_buy, new_buy, old_sell, new_sell):
        return False

    buy_improved = (new_buy - old_buy) > min_improve
    sell_improved = (new_sell - old_sell) < -min_improve  # more negative = better for a sell signal
    return buy_improved and sell_improved


# ─────────────────────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────────────────────

def render_ic_table(ic_df: pd.DataFrame) -> None:
    t = Table(title="Per-Factor Information Coefficient (in-sample)", box=box.ROUNDED)
    t.add_column("Factor")
    t.add_column("Horizon", justify="center")
    t.add_column("Pooled IC", justify="right")
    t.add_column("Daily IC mean", justify="right")
    t.add_column("Daily IC IR", justify="right")
    t.add_column("n", justify="right")
    t.add_column("Verdict")

    for factor in FACTOR_COLS:
        diag = factor_diagnosis(ic_df, factor)
        rows = ic_df[ic_df.factor == factor]
        for _, r in rows.iterrows():
            verdict = diag if r.horizon == DECISION_HORIZON else ""
            t.add_row(
                factor, str(r.horizon), f"{r.pooled_ic:+.3f}",
                f"{r.daily_ic_mean:+.3f}" if pd.notna(r.daily_ic_mean) else "—",
                f"{r.daily_ic_ir:+.2f}" if pd.notna(r.daily_ic_ir) else "—",
                str(int(r.n)), verdict,
            )
    console.print(t)


def render_momentum_vs_reversion(ic_df: pd.DataFrame) -> None:
    momentum = ic_df[ic_df.factor.isin(["ema_score", "roc_score"]) & (ic_df.horizon == DECISION_HORIZON)]["pooled_ic"].abs().mean()
    reversion = ic_df[ic_df.factor.isin(["stoch_score", "candle_score"]) & (ic_df.horizon == DECISION_HORIZON)]["pooled_ic"].abs().mean()
    console.print(
        f"\n[bold]Momentum family (EMA+ROC) avg |IC@{DECISION_HORIZON}d|: {momentum:.3f}"
        f"   vs.   Mean-reversion family (Stoch+Candle) avg |IC@{DECISION_HORIZON}d|: {reversion:.3f}[/bold]\n"
    )


def render_lag_check(panel: pd.DataFrame) -> None:
    score = apply_weights(panel, TECH_WEIGHTS)
    buckets = bucket_signal(score)
    t = Table(title=f"Idealized (EOD close entry) vs realistic (next-open entry) — {DECISION_HORIZON}d", box=box.SIMPLE)
    t.add_column("Bucket")
    t.add_column("Mean idealized", justify="right")
    t.add_column("Mean realistic-lag", justify="right")
    for b in BUCKET_ORDER:
        mask = buckets == b
        ideal = panel.loc[mask, f"fwd_ret_{DECISION_HORIZON}d"].dropna()
        lag = panel.loc[mask, f"fwd_ret_lag_{DECISION_HORIZON}d"].dropna()
        if len(ideal) == 0:
            continue
        t.add_row(b, f"{ideal.mean():+.3%}", f"{lag.mean():+.3%}" if len(lag) else "—")
    console.print(t)


def render_metrics_table(df: pd.DataFrame, title: str) -> None:
    t = Table(title=title, box=box.SIMPLE)
    t.add_column("Horizon", justify="center")
    t.add_column("Bucket")
    t.add_column("n", justify="right")
    t.add_column("Mean (gross)", justify="right")
    t.add_column("Mean (net)", justify="right")
    t.add_column("Win rate", justify="right")
    t.add_column("Reward/Std", justify="right")
    for _, r in df.iterrows():
        t.add_row(
            str(r.horizon), r.bucket, str(int(r.n)),
            f"{r.mean_gross:+.3%}", f"{r.mean_net:+.3%}", f"{r.win_rate:.1%}",
            f"{r.reward_risk:+.3f}" if pd.notna(r.reward_risk) else "—",
        )
    console.print(t)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest & validate the short-term technical scoring layer")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--oos-years", type=int, default=2)
    parser.add_argument("--tx-cost-bps", type=float, default=7.5)
    parser.add_argument("--refresh-data", action="store_true")
    parser.add_argument("--propose-weights", action="store_true")
    args = parser.parse_args()

    console.print(f"[bold cyan]Loading {args.years}y daily history for {len(ALL_SYMBOLS)} symbols…[/bold cyan]")
    history = load_history(ALL_SYMBOLS, args.years, args.refresh_data)
    console.print(f"[green]Loaded {len(history)}/{len(ALL_SYMBOLS)} symbols[/green]\n")

    console.print("[bold cyan]Simulating point-in-time technical scores (no lookahead)…[/bold cyan]")
    panels = []
    for sym, df in history.items():
        p = simulate_scores(df)
        if not p.empty:
            p["symbol"] = sym
            panels.append(p)
    if not panels:
        console.print("[bold red]No usable data — aborting.[/bold red]")
        return
    panel = pd.concat(panels, ignore_index=True)
    console.print(f"[green]Panel built: {len(panel):,} symbol-days across {panel['symbol'].nunique()} symbols[/green]\n")

    max_date = panel["date"].max()
    cutoff = max_date - pd.Timedelta(days=365 * args.oos_years)
    is_panel = panel[panel["date"] < cutoff].reset_index(drop=True)
    oos_panel = panel[panel["date"] >= cutoff].reset_index(drop=True)
    console.print(f"In-sample: {len(is_panel):,} rows (before {cutoff.date()})   "
                  f"Out-of-sample: {len(oos_panel):,} rows (from {cutoff.date()})\n")

    thin_oos = [
        sym for sym in panel["symbol"].unique()
        if is_panel[is_panel.symbol == sym].empty and not oos_panel[oos_panel.symbol == sym].empty
    ]
    if thin_oos:
        console.print(f"[yellow]Note: {', '.join(thin_oos)} have no in-sample history "
                       f"(too new) — excluded from weight tuning, OOS-only.[/yellow]\n")

    ic_df = compute_ic(is_panel)
    render_ic_table(ic_df)
    render_momentum_vs_reversion(ic_df)
    render_lag_check(is_panel)

    if "SPY" in panel["symbol"].values:
        spy_is = is_panel.loc[is_panel.symbol == "SPY", f"fwd_ret_{DECISION_HORIZON}d"].mean()
        spy_oos = oos_panel.loc[oos_panel.symbol == "SPY", f"fwd_ret_{DECISION_HORIZON}d"].mean()
        console.print(f"SPY buy-and-hold avg {DECISION_HORIZON}d return — "
                       f"IS: {spy_is:+.3%}   OOS: {spy_oos:+.3%}   "
                       f"[bright_black](apples-to-oranges: always-in-market vs. signal-conditional)[/bright_black]\n")

    if not args.propose_weights:
        return

    new_weights = propose_weights(ic_df, TECH_WEIGHTS)
    console.print(f"[bold]Current TECH_WEIGHTS:[/bold]  {TECH_WEIGHTS}")
    console.print(f"[bold]Proposed TECH_WEIGHTS:[/bold] {{{', '.join(f'{k!r}: {v:.3f}' for k, v in new_weights.items())}}}\n")

    old_oos = evaluate_weights(oos_panel, TECH_WEIGHTS, args.tx_cost_bps)
    new_oos = evaluate_weights(oos_panel, new_weights, args.tx_cost_bps)
    render_metrics_table(old_oos, "OUT-OF-SAMPLE — CURRENT weights")
    render_metrics_table(new_oos, "OUT-OF-SAMPLE — PROPOSED weights")

    accepted = check_acceptance(old_oos, new_oos)
    if accepted:
        console.print("\n[bold bright_green]OOS validation PASSED — proposed weights improve both the BUY and SELL tails "
                       "on data never used for tuning.[/bold bright_green]")
    else:
        console.print("\n[bold bright_red]OOS validation FAILED — keeping original TECH_WEIGHTS. "
                       "(Improvement did not hold, or was one-sided, on the untouched holdout.)[/bold bright_red]")


if __name__ == "__main__":
    main()
