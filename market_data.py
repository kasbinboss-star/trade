"""
DataManager: owns all stock state, drives background fetching threads,
and exposes thread-safe snapshots to the display layer.
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
    PRICE_UPDATE_INTERVAL, NEWS_UPDATE_INTERVAL,
    TECHNICAL_WEIGHT, ANALYST_WEIGHT, NEWS_WEIGHT, EARNINGS_WEIGHT,
    STRONG_BUY_THRESHOLD, BUY_THRESHOLD,
    SELL_THRESHOLD, STRONG_SELL_THRESHOLD,
)
from technical_analysis import calculate_tech_score
from news_sentiment import fetch_fundamental

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
# Stock snapshot dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StockSnapshot:
    symbol: str
    sector: str = ""

    # Price
    price:      float = 0.0
    prev_close: float = 0.0
    change:     float = 0.0
    change_pct: float = 0.0

    # Volume
    volume:       int   = 0
    avg_volume:   int   = 0
    volume_ratio: float = 1.0

    # Technical indicators
    rsi:          float = 50.0
    macd_hist:    float = 0.0
    bb_upper:     float = 0.0
    bb_middle:    float = 0.0
    bb_lower:     float = 0.0
    sma_50:       float = 0.0
    sma_200:      float = 0.0

    # Individual scores
    rsi_score:  float = 0.0
    macd_score: float = 0.0
    bb_score:   float = 0.0
    ma_score:   float = 0.0
    vol_score:  float = 0.0

    # Aggregate scores
    tech_score:              float = 0.0
    analyst_score:           float = 0.0
    news_score:              float = 0.0
    earnings_surprise_score: float = 0.0
    final_score:             float = 0.0

    # Signal
    signal:     str = "HOLD"
    confidence: str = "LOW"   # LOW / MED / HIGH

    # Analyst
    analyst_rating: str   = "Hold"
    analyst_target: float = 0.0
    num_analysts:   int   = 0
    upside_pct:     float = 0.0
    market_cap:     float = 0.0
    pe_ratio:       float = 0.0

    # News
    headlines: List[str] = field(default_factory=list)

    # Metadata
    last_price_update: Optional[datetime] = None
    last_news_update:  Optional[datetime] = None
    is_loading: bool = True
    error:      Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Signal calculation
# ─────────────────────────────────────────────────────────────────────────────

def _compute_signal(snap: StockSnapshot) -> None:
    final = (
        snap.tech_score              * TECHNICAL_WEIGHT +
        snap.analyst_score           * ANALYST_WEIGHT   +
        snap.news_score              * NEWS_WEIGHT       +
        snap.earnings_surprise_score * EARNINGS_WEIGHT
    )
    snap.final_score = round(final, 3)

    if   final >= STRONG_BUY_THRESHOLD:   snap.signal = "STRONG BUY"
    elif final >= BUY_THRESHOLD:          snap.signal = "BUY"
    elif final >= SELL_THRESHOLD:         snap.signal = "HOLD"
    elif final >= STRONG_SELL_THRESHOLD:  snap.signal = "SELL"
    else:                                 snap.signal = "STRONG SELL"

    abs_f = abs(final)
    snap.confidence = "HIGH" if abs_f > 1.3 else ("MED" if abs_f > 0.6 else "LOW")


# ─────────────────────────────────────────────────────────────────────────────
# DataManager
# ─────────────────────────────────────────────────────────────────────────────

class DataManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._snapshots: Dict[str, StockSnapshot] = {
            sym: StockSnapshot(symbol=sym, sector=SYMBOL_SECTOR.get(sym, ""))
            for sym in ALL_SYMBOLS
        }
        self._hist: Dict[str, pd.DataFrame] = {}
        self.stop_event = threading.Event()

    # ── Initialisation ────────────────────────────────────────────────────────

    def initialize(self) -> bool:
        """Download 1 year of daily OHLCV data and compute initial signals."""
        try:
            logger.info("Downloading 1-year daily history for %d symbols…", len(ALL_SYMBOLS))
            raw = _download_with_retry(ALL_SYMBOLS, period="1y")
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

            # Fetch analyst + news in parallel
            self._refresh_fundamentals()
            return True

        except Exception as e:
            logger.error("Initialize failed: %s", e)
            return False

    def _extract(self, raw: pd.DataFrame, sym: str) -> Optional[pd.DataFrame]:
        """Safely pull per-symbol slice from a multi- or single-ticker download."""
        try:
            cols = raw.columns
            if isinstance(cols, pd.MultiIndex):
                # yfinance v0.2 default: columns are (price_type, ticker)
                if sym in cols.get_level_values(1):
                    df = raw.xs(sym, axis=1, level=1)
                elif sym in cols.get_level_values(0):
                    df = raw[sym]
                else:
                    return None
            else:
                # Single ticker download — use as-is
                df = raw
            return df.dropna(how="all")
        except Exception as e:
            logger.debug("Extract error %s: %s", sym, e)
            return None

    def _apply_technicals(self, sym: str, df: pd.DataFrame) -> None:
        """Compute technical indicators from OHLCV DataFrame and update snapshot."""
        try:
            close  = df["Close"].dropna()
            volume = df["Volume"].dropna()

            if len(close) < 5:
                return

            tech = calculate_tech_score(close, volume)

            with self._lock:
                snap = self._snapshots[sym]
                snap.price        = tech["price"]
                snap.prev_close   = float(close.iloc[-2]) if len(close) >= 2 else tech["price"]
                snap.change       = tech["price"] - snap.prev_close
                snap.change_pct   = tech["change_pct"]
                snap.volume       = tech["volume"]
                snap.avg_volume   = tech["avg_volume"]
                snap.volume_ratio = tech["volume_ratio"]
                snap.rsi          = tech["rsi"]
                snap.macd_hist    = tech["macd_hist"]
                snap.bb_upper     = tech["bb_upper"]
                snap.bb_middle    = tech["bb_middle"]
                snap.bb_lower     = tech["bb_lower"]
                snap.sma_50       = tech["sma_50"]
                snap.sma_200      = tech["sma_200"]
                snap.rsi_score    = tech["rsi_score"]
                snap.macd_score   = tech["macd_score"]
                snap.bb_score     = tech["bb_score"]
                snap.ma_score     = tech["ma_score"]
                snap.vol_score    = tech["vol_score"]
                snap.tech_score   = tech["tech_score"]
                snap.last_price_update = datetime.now()
                snap.is_loading   = False
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
                               prepost=True, auto_adjust=False,
                               progress=False, threads=True)
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
        """Last COMPLETED regular-session close -- the correct baseline for a
        change %. If today's (partial) bar is present, step back one."""
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
                snap.change     = price - ref
                snap.change_pct = (price - ref) / ref * 100
                snap.last_price_update = datetime.now()

                # Only the live regular session has a genuinely partial last
                # bar. Pre/after-hours it is a COMPLETED bar, and patching it
                # would corrupt the history the indicators score off.
                hist = self._hist.get(sym)
                if session_open and hist is not None and self._last_bar_is_today(hist):
                    hist.iloc[-1, hist.columns.get_loc("Close")] = price

                _compute_signal(snap)

    # ── Fundamental refresh ───────────────────────────────────────────────────

    def _refresh_fundamentals(self) -> None:
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(fetch_fundamental, sym): sym for sym in ALL_SYMBOLS}
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    data = fut.result()
                    self._apply_fundamental(sym, data)
                except Exception as e:
                    logger.warning("Fundamental apply failed %s: %s", sym, e)

    def _apply_fundamental(self, sym: str, data: dict) -> None:
        with self._lock:
            snap = self._snapshots[sym]
            snap.analyst_score  = data["analyst_score"]
            snap.analyst_rating = data["analyst_rating"]
            snap.analyst_target = data["analyst_target"]
            snap.num_analysts   = data["num_analysts"]
            snap.upside_pct     = data["upside_pct"]
            snap.market_cap     = data["market_cap"]
            snap.pe_ratio       = data["pe_ratio"]
            snap.news_score              = data["news_score"]
            snap.earnings_surprise_score = data.get("earnings_surprise_score", 0.0)
            snap.headlines               = data["headlines"]
            snap.last_news_update        = data["updated_at"]
            _compute_signal(snap)

    # ── Background threads ────────────────────────────────────────────────────

    def start_background_updates(self) -> None:
        def price_loop():
            while not self.stop_event.is_set():
                self.stop_event.wait(PRICE_UPDATE_INTERVAL)
                if not self.stop_event.is_set():
                    try:
                        self.update_prices()
                    except Exception as e:
                        logger.error("Price loop error: %s", e)

        def news_loop():
            while not self.stop_event.is_set():
                self.stop_event.wait(NEWS_UPDATE_INTERVAL)
                if not self.stop_event.is_set():
                    try:
                        self._refresh_fundamentals()
                    except Exception as e:
                        logger.error("News loop error: %s", e)

        threading.Thread(target=price_loop, daemon=True, name="price-updater").start()
        threading.Thread(target=news_loop,  daemon=True, name="news-updater").start()

    def stop(self) -> None:
        self.stop_event.set()

    # ── Public accessors ──────────────────────────────────────────────────────

    def get_snapshots(self) -> List[StockSnapshot]:
        with self._lock:
            return list(self._snapshots.values())

    def get_snapshot(self, sym: str) -> Optional[StockSnapshot]:
        with self._lock:
            return self._snapshots.get(sym)

    def market_status(self) -> str:
        """Returns OPEN / PRE-MARKET / AFTER-HOURS / CLOSED."""
        try:
            import pytz
            et = pytz.timezone("America/New_York")
            now = datetime.now(et)
            wd  = now.weekday()
            if wd >= 5:
                return "CLOSED"
            h, m = now.hour, now.minute
            mins = h * 60 + m
            if 570 <= mins < 960:    # 9:30–16:00
                return "OPEN"
            if 240 <= mins < 570:    # 4:00–9:30
                return "PRE-MARKET"
            if 960 <= mins < 1200:   # 16:00–20:00
                return "AFTER-HOURS"
            return "CLOSED"
        except Exception:
            return "UNKNOWN"
