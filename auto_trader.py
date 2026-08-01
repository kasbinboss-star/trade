"""
AutoTrader — Alpaca-backed execution engine.

Wired to the short-term signal output. Each time run() is called it:
  1. Checks existing positions — exits any whose signal has flipped to SELL/STRONG SELL.
  2. Scans for new STRONG BUY signals — opens bracket orders (market buy +
     automatic stop-loss + take-profit legs) for symbols not already held.

All risk guardrails live in config.py. Paper mode (fake money) is the default;
pass paper=False only when you have tested in paper and are ready for real trades.

Bracket orders mean you never need to manually watch for stop/profit hits —
Alpaca closes the position automatically when either leg triggers.

Environment variables required:
  ALPACA_API_KEY     — your Alpaca key ID
  ALPACA_SECRET_KEY  — your Alpaca secret key
"""

import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from config import (
    TRADE_ENTRY_SIGNAL, TRADE_MIN_SCORE, TRADE_EXIT_SIGNAL,
    POSITION_SIZE_PCT, POSITION_SIZE_STRONG, MAX_POSITION_USD,
    MAX_OPEN_POSITIONS, STOP_LOSS_PCT, TAKE_PROFIT_PCT,
    MIN_ACCOUNT_EQUITY, TRADE_COOLDOWN_HOURS,
)

logger = logging.getLogger(__name__)

_PAPER_URL = "https://paper-api.alpaca.markets"
_LIVE_URL  = "https://api.alpaca.markets"

# Signal priority ordering (higher index = stronger sell pressure)
_SIGNAL_RANK = {
    "STRONG BUY": 5,
    "BUY":        4,
    "HOLD":       3,
    "SELL":       2,
    "STRONG SELL": 1,
}
_EXIT_RANK = _SIGNAL_RANK.get(TRADE_EXIT_SIGNAL, 2)   # rank at or below triggers exit


class TradeRecord:
    """Lightweight in-memory record of a position we opened."""
    __slots__ = ("symbol", "qty", "entry_price", "stop_price",
                 "target_price", "opened_at", "order_id")

    def __init__(self, symbol, qty, entry_price, stop_price, target_price, order_id):
        self.symbol      = symbol
        self.qty         = qty
        self.entry_price = entry_price
        self.stop_price  = stop_price
        self.target_price = target_price
        self.opened_at   = datetime.now()
        self.order_id    = order_id


class AutoTrader:
    """
    Thread-safe execution engine. Call run(snapshots) from the main loop;
    it is rate-limited internally (won't re-run within TRADE_CHECK_INTERVAL).
    """

    def __init__(self, paper: bool = True):
        self.paper    = paper
        self._lock    = threading.Lock()
        self._api     = None             # lazy-init on first run
        self._trades: Dict[str, TradeRecord] = {}    # symbol → open trade
        self._cooldowns: Dict[str, datetime]  = {}   # symbol → earliest re-entry time
        self._last_run: Optional[datetime]    = None
        self._account_equity: float           = 0.0
        self._buying_power:   float           = 0.0
        self.log: List[str]                   = []   # human-readable trade log for display

        # Public read-only stats updated after each run
        self.positions: Dict[str, dict] = {}   # symbol → {qty, entry, stop, target, pnl_pct}
        self.equity:    float           = 0.0
        self.buying_power: float        = 0.0
        self.last_equity: float         = 0.0   # equity as of previous session's close
        self.day_pnl:     float         = 0.0   # equity - last_equity
        self.day_pnl_pct: float         = 0.0
        self.trade_count: int           = 0

    # ─────────────────────────────────────────────────────────────────────────
    # Connection
    # ─────────────────────────────────────────────────────────────────────────

    def _connect(self) -> bool:
        """Lazy-init Alpaca client. Returns True if connected."""
        if self._api is not None:
            return True
        try:
            from alpaca.trading.client import TradingClient
            key    = os.environ.get("ALPACA_API_KEY", "")
            secret = os.environ.get("ALPACA_SECRET_KEY", "")
            if not key or not secret:
                logger.error(
                    "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set as "
                    "environment variables before auto-trading can start."
                )
                return False
            self._api = TradingClient(key, secret, paper=self.paper)
            logger.info("Connected to Alpaca (%s)", "PAPER" if self.paper else "LIVE")
            return True
        except Exception as e:
            logger.error("Alpaca connection failed: %s", e)
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # Account info
    # ─────────────────────────────────────────────────────────────────────────

    def _refresh_account(self) -> bool:
        try:
            acct = self._api.get_account()
            self._account_equity = float(acct.equity)
            self._buying_power   = float(acct.buying_power)
            self.equity          = self._account_equity
            self.buying_power    = self._buying_power
            self.last_equity     = float(acct.last_equity)
            self.day_pnl         = self.equity - self.last_equity
            self.day_pnl_pct     = (self.day_pnl / self.last_equity * 100) if self.last_equity else 0.0
            return True
        except Exception as e:
            logger.error("Account refresh failed: %s", e)
            return False

    def _market_is_open(self) -> bool:
        try:
            clock = self._api.get_clock()
            return bool(clock.is_open)
        except Exception as e:
            logger.error("Clock check failed: %s", e)
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # Position sizing
    # ─────────────────────────────────────────────────────────────────────────

    def _calc_qty(self, price: float, signal: str) -> int:
        pct   = POSITION_SIZE_STRONG if signal == "STRONG BUY" else POSITION_SIZE_PCT
        usd   = min(self._account_equity * pct, MAX_POSITION_USD, self._buying_power)
        qty   = int(usd // price)
        return max(qty, 0)

    # ─────────────────────────────────────────────────────────────────────────
    # Sync open positions from Alpaca (fills/stops may have closed them)
    # ─────────────────────────────────────────────────────────────────────────

    def _sync_positions(self) -> None:
        try:
            open_syms = {p.symbol for p in self._api.get_all_positions()}
            with self._lock:
                closed = [s for s in self._trades if s not in open_syms]
                for sym in closed:
                    rec = self._trades.pop(sym)
                    self._cooldowns[sym] = datetime.now() + timedelta(hours=TRADE_COOLDOWN_HOURS)
                    self._log(f"Position closed (stop/target hit): {sym} held since {rec.opened_at:%H:%M:%S}")
        except Exception as e:
            logger.error("Position sync failed: %s", e)

    def _build_positions_display(self, snapshots) -> None:
        price_map = {s.symbol: s.price for s in snapshots if not s.is_loading and s.price > 0}
        out = {}
        try:
            for p in self._api.get_all_positions():
                sym  = p.symbol
                qty  = float(p.qty)
                cost = float(p.avg_entry_price)
                cur  = price_map.get(sym, float(p.current_price or cost))
                pnl  = (cur - cost) / cost * 100 if cost else 0.0
                rec  = self._trades.get(sym)
                out[sym] = {
                    "qty":    qty,
                    "entry":  cost,
                    "stop":   rec.stop_price   if rec else cost * (1 - STOP_LOSS_PCT),
                    "target": rec.target_price if rec else cost * (1 + TAKE_PROFIT_PCT),
                    "pnl_pct": pnl,
                    "current": cur,
                }
        except Exception as e:
            logger.error("Positions display build failed: %s", e)
        self.positions = out

    # ─────────────────────────────────────────────────────────────────────────
    # Exit logic
    # ─────────────────────────────────────────────────────────────────────────

    def _check_exits(self, snapshots) -> None:
        snap_map = {s.symbol: s for s in snapshots if not s.is_loading}
        with self._lock:
            held = list(self._trades.keys())

        for sym in held:
            snap = snap_map.get(sym)
            if snap is None:
                continue
            if _SIGNAL_RANK.get(snap.signal, 3) <= _EXIT_RANK:
                self._close_position(sym, snap.price, snap.signal)

    def _close_position(self, symbol: str, price: float, reason: str) -> None:
        try:
            self._api.close_position(symbol)
            with self._lock:
                rec = self._trades.pop(symbol, None)
            self._cooldowns[symbol] = datetime.now() + timedelta(hours=TRADE_COOLDOWN_HOURS)
            entry = rec.entry_price if rec else 0.0
            pnl   = (price - entry) / entry * 100 if entry else 0.0
            msg   = f"SOLD {symbol} @ ${price:.2f}  P&L {pnl:+.1f}%  [{reason}]"
            self._log(msg)
            logger.info(msg)
            self.trade_count += 1
        except Exception as e:
            logger.error("Close position failed for %s: %s", symbol, e)

    # ─────────────────────────────────────────────────────────────────────────
    # Entry logic
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _tech_confirmed(snap) -> bool:
        """
        At least 2 of 3 quick technical checks must be true before entering.
        Guards against buying a BUY-rated stock that's technically overextended.
        """
        checks = 0
        # 1. EMA stack bullish (short > mid > long)
        try:
            if snap.ema5 > snap.ema10 > snap.ema20:
                checks += 1
        except AttributeError:
            checks += 1   # long-term snapshots don't have EMAs — skip this check

        # 2. Stochastic not overbought (below 80 = room to run)
        try:
            if snap.stoch_k < 80:
                checks += 1
        except AttributeError:
            checks += 1

        # 3. Positive 3-day momentum
        try:
            if snap.roc3 > 0:
                checks += 1
        except AttributeError:
            checks += 1

        return checks >= 2

    def _check_entries(self, snapshots) -> None:
        with self._lock:
            held = set(self._trades.keys())

        if len(held) >= MAX_OPEN_POSITIONS:
            return

        valid_signals = {"BUY", "STRONG BUY"} if TRADE_ENTRY_SIGNAL == "BUY" else {"STRONG BUY"}

        candidates = [
            s for s in snapshots
            if not s.is_loading
            and s.signal in valid_signals
            and s.final_score >= TRADE_MIN_SCORE   # filter out weak BUY signals near 0.3
            and s.symbol not in held
            and s.price > 0
            and not s.earnings_soon                # never buy into earnings
            and datetime.now() >= self._cooldowns.get(s.symbol, datetime.min)
            and self._tech_confirmed(s)            # at least 2/3 technical checks pass
        ]

        # Sort by final_score descending — best signal first
        candidates.sort(key=lambda s: s.final_score, reverse=True)

        for snap in candidates:
            if len(self._trades) >= MAX_OPEN_POSITIONS:
                break
            self._open_position(snap)

    def _open_position(self, snap) -> None:
        from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

        price  = snap.price
        qty    = self._calc_qty(price, snap.signal)
        if qty <= 0:
            logger.debug("Skipping %s: qty=0 at price %.2f", snap.symbol, price)
            return

        stop_price   = round(price * (1 - STOP_LOSS_PCT),   2)
        target_price = round(price * (1 + TAKE_PROFIT_PCT), 2)

        try:
            order = self._api.submit_order(MarketOrderRequest(
                symbol         = snap.symbol,
                qty            = qty,
                side           = OrderSide.BUY,
                time_in_force  = TimeInForce.DAY,
                order_class    = OrderClass.BRACKET,
                take_profit    = TakeProfitRequest(limit_price=target_price),
                stop_loss      = StopLossRequest(stop_price=stop_price),
            ))

            rec = TradeRecord(snap.symbol, qty, price, stop_price, target_price, str(order.id))
            with self._lock:
                self._trades[snap.symbol] = rec
            self._buying_power -= price * qty

            msg = (f"BOUGHT {qty}× {snap.symbol} @ ${price:.2f}  "
                   f"stop ${stop_price:.2f}  target ${target_price:.2f}  "
                   f"[score {snap.final_score:+.3f}]")
            self._log(msg)
            logger.info(msg)
            self.trade_count += 1

        except Exception as e:
            logger.error("Order failed for %s: %s", snap.symbol, e)

    # ─────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ─────────────────────────────────────────────────────────────────────────

    def run(self, snapshots, force: bool = False) -> None:
        """
        Called from the main display loop. Rate-limited to TRADE_CHECK_INTERVAL.
        Set force=True to bypass the interval (e.g., on startup).
        """
        from config import TRADE_CHECK_INTERVAL
        now = datetime.now()
        if not force and self._last_run and (now - self._last_run).seconds < TRADE_CHECK_INTERVAL:
            return
        self._last_run = now

        if not self._connect():
            return

        # Always refresh account info and positions so the display is accurate
        # even outside market hours — just don't place orders when closed.
        self._refresh_account()
        self._sync_positions()
        self._build_positions_display(snapshots)

        if not self._market_is_open():
            return

        if self._account_equity < MIN_ACCOUNT_EQUITY:
            logger.warning("Account equity $%.0f below minimum $%.0f — not trading",
                           self._account_equity, MIN_ACCOUNT_EQUITY)
            return

        self._check_exits(snapshots)
        self._check_entries(snapshots)

    def _log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        self.log.insert(0, entry)
        self.log = self.log[:50]   # keep last 50 entries
