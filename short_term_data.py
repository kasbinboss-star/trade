"""
ShortTermDataManager: owns short-horizon stock state and drives background
fetching threads tuned for fast-moving, few-day trades.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

from config import (
    ALL_SYMBOLS, SYMBOL_SECTOR,
    SHORT_PRICE_UPDATE_INTERVAL, SHORT_CATALYST_UPDATE_INTERVAL,
    SHORT_TECH_WEIGHT, SHORT_CATALYST_WEIGHT,
    SHORT_STRONG_BUY_THRESHOLD, SHORT_BUY_THRESHOLD,
    SHORT_SELL_THRESHOLD, SHORT_STRONG_SELL_THRESHOLD,
    CATALYST_TOP_N, CATALYST_BOTTOM_N, CATALYST_ROTATE_N,
)
from short_term_analysis import calculate_short_term_score
from catalyst_analysis import fetch_catalyst, log_catalyst_snapshot

logger = logging.getLogger(__name__)


def _download_with_retry(tickers, period, interval="1d", auto_adjust=True,
                          max_attempts=3, base_delay=5.0) -> pd.DataFrame:
    """yf.download() occasionally returns an empty frame for the WHOLE batch
    when Yahoo Finance rate-limits/blocks the request -- yfinance then
    misreports every symbol as "possibly delisted", which is transient, not
    a real mass-delisting. Retry with backoff before giving up."""
    raw = pd.DataFrame()
    for attempt in range(1, max_attempts + 1):
        raw = yf.download(tickers=tickers, period=period, interval=interval,
                           auto_adjust=auto_adjust, progress=False, threads=True)
        if not raw.empty:
            return raw
        if attempt < max_attempts:
            delay = base_delay * attempt
            logger.warning(
                "yf.download returned empty for all %d symbols (attempt %d/%d) "
                "-- likely rate-limited by Yahoo, retrying in %.0fs",
                len(tickers), attempt, max_attempts, delay,
            )
            time.sleep(delay)
    return raw


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ShortTermSnapshot:
    symbol: str
    sector: str = ""

    price:      float = 0.0
    prev_close: float = 0.0
    change_pct: float = 0.0

    ema5:  float = 0.0
    ema10: float = 0.0
    ema20: float = 0.0
    stoch_k: float = 50.0
    stoch_d: float = 50.0
    roc3: float = 0.0
    roc5: float = 0.0
    atr_pct: float = 0.0
    gap_pct: float = 0.0
    volume_ratio: float = 1.0
    candle_label: str = "—"

    ema_score:    float = 0.0
    stoch_score:  float = 0.0
    roc_score:    float = 0.0
    gap_score:    float = 0.0
    vol_score:    float = 0.0
    candle_score: float = 0.0
    short_score:  float = 0.0

    catalyst_score: float = 0.0
    catalyst_label: Optional[str] = None
    rating_label:   Optional[str] = None
    options_score:  float = 0.0
    options_label:  Optional[str] = None
    squeeze_score:  float = 0.0
    squeeze_label:  Optional[str] = None
    news_score:     float = 0.0
    earnings_soon:  bool  = False
    premarket_pct:   float = 0.0
    premarket_price: float = 0.0
    short_float_pct: float = 0.0
    headlines: List[str] = field(default_factory=list)

    final_score: float = 0.0
    signal:      str   = "HOLD"
    hold_time:   str   = "1-2 Days"
    confidence:  str   = "LOW"

    last_price_update:    Optional[datetime] = None
    last_catalyst_update: Optional[datetime] = None
    is_loading: bool = True
    error: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Signal + hold-time logic
# ─────────────────────────────────────────────────────────────────────────────

def _compute_signal(snap: ShortTermSnapshot) -> None:
    # Blend in the catalyst layer only when it was actually fetched. Under
    # tiered analysis most symbols have no catalyst data, and scoring them as
    # 0.60*tech + 0.40*0.0 would rank a strong technical setup below a mediocre
    # one that merely happened to be selected for catalyst analysis.
    if snap.last_catalyst_update is not None:
        final = snap.short_score * SHORT_TECH_WEIGHT + snap.catalyst_score * SHORT_CATALYST_WEIGHT
    else:
        final = snap.short_score
    snap.final_score = round(final, 3)

    if   final >= SHORT_STRONG_BUY_THRESHOLD:   snap.signal = "STRONG BUY"
    elif final >= SHORT_BUY_THRESHOLD:          snap.signal = "BUY"
    elif final >= SHORT_SELL_THRESHOLD:         snap.signal = "HOLD"
    elif final >= SHORT_STRONG_SELL_THRESHOLD:  snap.signal = "SELL"
    else:                                       snap.signal = "STRONG SELL"

    abs_f = abs(final)
    snap.confidence = "HIGH" if abs_f > 1.3 else ("MED" if abs_f > 0.6 else "LOW")
    snap.hold_time = _suggest_hold_time(snap)


def _suggest_hold_time(snap: ShortTermSnapshot) -> str:
    if snap.earnings_soon:
        return "Exit before earnings"

    has_fresh_catalyst = abs(snap.catalyst_score) > 0.5
    if has_fresh_catalyst and snap.atr_pct > 4.0:
        return "Intraday-1 Day"
    if abs(snap.short_score) > 1.2 and snap.atr_pct < 2.5:
        return "3-5 Days"
    if abs(snap.short_score) > 0.5:
        return "2-3 Days"
    return "1-2 Days"


# ─────────────────────────────────────────────────────────────────────────────
# DataManager
# ─────────────────────────────────────────────────────────────────────────────

class ShortTermDataManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._snapshots: Dict[str, ShortTermSnapshot] = {
            sym: ShortTermSnapshot(symbol=sym, sector=SYMBOL_SECTOR.get(sym, ""))
            for sym in ALL_SYMBOLS
        }
        self._hist: Dict[str, pd.DataFrame] = {}
        self._priority_symbols: set = set()   # e.g. currently-held positions
        self._hist_refreshed_for = None       # date of last intraday hist refresh
        self._rotate_idx = 0                  # catalyst rotation cursor
        self.stop_event = threading.Event()

    def set_priority_symbols(self, symbols) -> None:
        """Always get full catalyst analysis regardless of rank -- typically
        open positions, so exit decisions keep complete data."""
        with self._lock:
            self._priority_symbols = set(symbols or ())

    # ── Initialisation ────────────────────────────────────────────────────────

    def initialize(self) -> bool:
        """Download ~3 months of daily OHLCV (enough for EMA20/Stochastic/ATR)."""
        try:
            logger.info("Downloading 3-month daily history for %d symbols…", len(ALL_SYMBOLS))
            raw = _download_with_retry(ALL_SYMBOLS, period="3mo")
            if raw.empty:
                logger.error("yfinance returned empty data after retries")
                return False

            for sym in ALL_SYMBOLS:
                try:
                    df = self._extract(raw, sym)
                    if df is not None and len(df) >= 10:
                        self._hist[sym] = df
                        self._apply_technicals(sym, df)
                except Exception as e:
                    logger.warning("History parse failed for %s: %s", sym, e)

            self._refresh_catalysts()
            return True

        except Exception as e:
            logger.error("Initialize failed: %s", e)
            return False

    def _extract(self, raw: pd.DataFrame, sym: str) -> Optional[pd.DataFrame]:
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
        except Exception as e:
            logger.debug("Extract error %s: %s", sym, e)
            return None

    def _apply_technicals(self, sym: str, df: pd.DataFrame) -> None:
        try:
            if len(df) < 5:
                return
            tech = calculate_short_term_score(df)

            with self._lock:
                snap = self._snapshots[sym]
                snap.price        = tech["price"]
                snap.prev_close   = float(df["Close"].iloc[-2]) if len(df) >= 2 else tech["price"]
                snap.change_pct   = tech["change_pct"]
                snap.ema5         = tech["ema5"]
                snap.ema10        = tech["ema10"]
                snap.ema20        = tech["ema20"]
                snap.stoch_k      = tech["stoch_k"]
                snap.stoch_d      = tech["stoch_d"]
                snap.roc3         = tech["roc3"]
                snap.roc5         = tech["roc5"]
                snap.atr_pct      = tech["atr_pct"]
                snap.gap_pct      = tech["gap_pct"]
                snap.volume_ratio = tech["volume_ratio"]
                snap.candle_label = tech["candle_label"]
                snap.ema_score    = tech["ema_score"]
                snap.stoch_score  = tech["stoch_score"]
                snap.roc_score    = tech["roc_score"]
                snap.gap_score    = tech["gap_score"]
                snap.vol_score    = tech["vol_score"]
                snap.candle_score = tech["candle_score"]
                snap.short_score  = tech["short_score"]
                snap.last_price_update = datetime.now()
                snap.is_loading = False
                _compute_signal(snap)

        except Exception as e:
            logger.warning("Technicals failed for %s: %s", sym, e)
            with self._lock:
                self._snapshots[sym].error = str(e)

    # ── Live price refresh ────────────────────────────────────────────────────

    def _fetch_live_prices(self) -> Dict[str, float]:
        """Current price per symbol from ONE batched intraday request.

        prepost=True so this reflects real pre/after-hours trading. The old
        per-symbol fast_info loop returned the last *regular session* close
        during extended hours, and a previous_close that matched no actual
        bar -- so pre-market moves came out fabricated.
        """
        prices: Dict[str, float] = {}
        try:
            raw = yf.download(tickers=ALL_SYMBOLS, period="1d", interval="1m",
                               prepost=True, auto_adjust=False, progress=False, threads=True)
        except Exception as e:
            logger.warning("Live price download failed: %s", e)
            return prices

        if raw is None or raw.empty:
            return prices

        try:
            close_block = raw["Close"]
        except Exception:
            return prices

        if isinstance(close_block, pd.Series):          # single-ticker shape
            series = close_block.dropna()
            if not series.empty and len(ALL_SYMBOLS) == 1:
                prices[ALL_SYMBOLS[0]] = float(series.iloc[-1])
            return prices

        for sym in close_block.columns:
            series = close_block[sym].dropna()
            if series.empty:
                continue
            val = float(series.iloc[-1])
            if val > 0:
                prices[str(sym)] = val
        return prices

    def _last_bar_is_today(self, hist: pd.DataFrame) -> bool:
        """True when the final daily bar belongs to the current ET session."""
        try:
            import pytz
            today_et = datetime.now(pytz.timezone("America/New_York")).date()
            return hist.index[-1].date() == today_et
        except Exception:
            return False

    def _reference_close(self, sym: str) -> float:
        """Last COMPLETED regular-session close -- the correct baseline for
        a change %. If today's (partial) bar is present, step back one."""
        hist = self._hist.get(sym)
        if hist is None or hist.empty or "Close" not in hist.columns:
            return 0.0
        idx = -2 if (self._last_bar_is_today(hist) and len(hist) >= 2) else -1
        try:
            return float(hist["Close"].iloc[idx])
        except Exception:
            return 0.0

    def update_prices(self) -> None:
        prices = self._fetch_live_prices()
        if not prices:
            return

        session_open = self.market_status() == "OPEN"
        self._refresh_daily_history_if_stale(session_open)

        with self._lock:
            for sym, price in prices.items():
                snap = self._snapshots.get(sym)
                if snap is None:
                    continue
                ref = self._reference_close(sym)
                if ref <= 0:
                    continue

                snap.price      = price
                snap.prev_close = ref
                snap.change_pct = (price - ref) / ref * 100
                snap.last_price_update = datetime.now()

                # Only the live regular session has a genuinely partial last
                # bar. Pre/after-hours it is a COMPLETED bar, and patching it
                # would corrupt the history the indicators score off.
                hist = self._hist.get(sym)
                if session_open and hist is not None and self._last_bar_is_today(hist):
                    hist.iloc[-1, hist.columns.get_loc("Close")] = price

                _compute_signal(snap)

    def _refresh_daily_history_if_stale(self, session_open: bool) -> None:
        """Re-pull daily bars once, the first time we trade a session whose bar
        we don't have yet -- e.g. the app was started pre-market, so history
        ends at yesterday, and the market has since opened."""
        if not session_open or self._hist_refreshed_for == datetime.now().date():
            return
        sample = next(iter(self._hist.values()), None)
        if sample is not None and self._last_bar_is_today(sample):
            return

        logger.info("Market opened since startup -- refreshing daily history")
        self._hist_refreshed_for = datetime.now().date()
        try:
            raw = _download_with_retry(ALL_SYMBOLS, period="3mo")
            if raw.empty:
                return
            for sym in ALL_SYMBOLS:
                df = self._extract(raw, sym)
                if df is not None and len(df) >= 10:
                    self._hist[sym] = df
                    self._apply_technicals(sym, df)
        except Exception as e:
            logger.warning("Daily history refresh failed: %s", e)

    # ── Catalyst refresh ──────────────────────────────────────────────────────

    def _rotating_slice(self, exclude: set, count: int) -> List[str]:
        """Next `count` symbols from a cursor walking the universe, skipping
        anything already selected -- so every name eventually gets catalyst
        coverage, not just the technical leaders."""
        if count <= 0:
            return []
        picked, n = [], len(ALL_SYMBOLS)
        for _ in range(n):
            sym = ALL_SYMBOLS[self._rotate_idx % n]
            self._rotate_idx = (self._rotate_idx + 1) % n
            if sym not in exclude:
                picked.append(sym)
                if len(picked) >= count:
                    break
        return picked

    def _select_catalyst_symbols(self) -> List[str]:
        """Tier 2 selection. Catalyst work costs ~1s and ~5 Yahoo API calls per
        symbol, so it runs only on names that can change a decision: the
        strongest setups, the weakest (so SELL signals stay informed), anything
        currently held, plus a rotating slice so selection isn't purely
        circular on technical rank."""
        with self._lock:
            ranked = sorted(
                (s for s in self._snapshots.values() if not s.is_loading),
                key=lambda s: s.short_score, reverse=True,
            )
            priority = set(self._priority_symbols)

        if not ranked:
            return list(ALL_SYMBOLS[:CATALYST_TOP_N])

        top    = [s.symbol for s in ranked[:CATALYST_TOP_N]]
        bottom = [s.symbol for s in ranked[-CATALYST_BOTTOM_N:]]
        chosen = set(top) | set(bottom) | priority
        rotating = self._rotating_slice(chosen, CATALYST_ROTATE_N)

        # dict.fromkeys keeps order while de-duplicating
        selected = list(dict.fromkeys(top + bottom + sorted(priority) + rotating))
        return [s for s in selected if s in self._snapshots]

    def _refresh_catalysts(self) -> None:
        symbols = self._select_catalyst_symbols()
        logger.info("Catalyst refresh: %d of %d symbols", len(symbols), len(ALL_SYMBOLS))
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(fetch_catalyst, sym): sym for sym in symbols}
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    data = fut.result()
                    self._apply_catalyst(sym, data)
                except Exception as e:
                    logger.warning("Catalyst apply failed %s: %s", sym, e)

    def _apply_catalyst(self, sym: str, data: dict) -> None:
        with self._lock:
            snap = self._snapshots[sym]
            snap.catalyst_score  = data["catalyst_score"]
            snap.catalyst_label  = data["catalyst_label"]
            snap.rating_label    = data.get("rating_label")
            snap.options_score   = data.get("options_score", 0.0)
            snap.options_label   = data.get("options_label")
            snap.squeeze_score   = data.get("squeeze_score", 0.0)
            snap.squeeze_label   = data.get("squeeze_label")
            snap.news_score      = data["news_score"]
            snap.headlines       = data["headlines"]
            snap.earnings_soon   = data["earnings_soon"]
            snap.premarket_pct   = data.get("premarket_pct", 0.0)
            snap.premarket_price = data.get("premarket_price", 0.0)
            snap.short_float_pct = data.get("short_float_pct", 0.0)
            snap.last_catalyst_update = data["updated_at"]
            _compute_signal(snap)
            log_catalyst_snapshot(sym, snap.price, data)

    # ── Background threads ────────────────────────────────────────────────────

    def start_background_updates(self) -> None:
        def price_loop():
            while not self.stop_event.is_set():
                self.stop_event.wait(SHORT_PRICE_UPDATE_INTERVAL)
                if not self.stop_event.is_set():
                    try:
                        self.update_prices()
                    except Exception as e:
                        logger.error("Price loop error: %s", e)

        def catalyst_loop():
            while not self.stop_event.is_set():
                self.stop_event.wait(SHORT_CATALYST_UPDATE_INTERVAL)
                if not self.stop_event.is_set():
                    try:
                        self._refresh_catalysts()
                    except Exception as e:
                        logger.error("Catalyst loop error: %s", e)

        threading.Thread(target=price_loop,    daemon=True, name="short-price-updater").start()
        threading.Thread(target=catalyst_loop, daemon=True, name="short-catalyst-updater").start()

    def stop(self) -> None:
        self.stop_event.set()

    # ── Public accessors ──────────────────────────────────────────────────────

    def get_snapshots(self) -> List[ShortTermSnapshot]:
        with self._lock:
            return list(self._snapshots.values())

    def get_snapshot(self, sym: str) -> Optional[ShortTermSnapshot]:
        with self._lock:
            return self._snapshots.get(sym)

    def market_status(self) -> str:
        try:
            import pytz
            et = pytz.timezone("America/New_York")
            now = datetime.now(et)
            if now.weekday() >= 5:
                return "CLOSED"
            mins = now.hour * 60 + now.minute
            if 570 <= mins < 960:  return "OPEN"
            if 240 <= mins < 570:  return "PRE-MARKET"
            if 960 <= mins < 1200: return "AFTER-HOURS"
            return "CLOSED"
        except Exception:
            return "UNKNOWN"
