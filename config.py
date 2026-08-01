WATCHLIST = {
    "Mega-Cap Tech": ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"],
    "Semiconductors": ["AMD", "INTC", "QCOM", "AVGO", "MU", "TSM"],
    "Software":       ["CRM", "ORCL", "ADBE", "NOW", "SNOW"],
    "Finance":        ["JPM", "BAC", "GS", "V", "MA", "BRK-B", "MS"],
    "Healthcare":     ["JNJ", "PFE", "UNH", "ABBV", "LLY", "BMY"],
    "Consumer":       ["WMT", "COST", "HD", "MCD", "NKE", "DIS"],
    "Energy":         ["XOM", "CVX", "COP"],
    "ETFs":           ["SPY", "QQQ", "IWM"],
    "High Growth":    ["PLTR", "COIN", "XYZ", "NFLX", "RBLX", "UBER", "SPOT"],
}

ALL_SYMBOLS = [sym for syms in WATCHLIST.values() for sym in syms]

SYMBOL_SECTOR = {
    sym: sector
    for sector, syms in WATCHLIST.items()
    for sym in syms
}

# Abbreviated sector labels for display
SECTOR_ABBR = {
    "Mega-Cap Tech": "Tech",
    "Semiconductors": "Semi",
    "Software":       "SaaS",
    "Finance":        "Fin",
    "Healthcare":     "Health",
    "Consumer":       "Cons",
    "Energy":         "Enrgy",
    "ETFs":           "ETF",
    "High Growth":    "HiGro",
}

# Update intervals (seconds)
PRICE_UPDATE_INTERVAL  = 60
NEWS_UPDATE_INTERVAL   = 300
DISPLAY_REFRESH_RATE   = 2   # refreshes per second

# Final signal thresholds
STRONG_BUY_THRESHOLD   =  1.0
BUY_THRESHOLD          =  0.3
SELL_THRESHOLD         = -0.3
STRONG_SELL_THRESHOLD  = -1.0

# Weight of each signal source in the final score
# Earnings surprise is now a 4th component (reduces tech/news slightly)
TECHNICAL_WEIGHT  = 0.60
ANALYST_WEIGHT    = 0.22
NEWS_WEIGHT       = 0.12
EARNINGS_WEIGHT   = 0.06

# Technical indicator weights (must sum to 1.0)
TECH_WEIGHTS = {
    "rsi":       0.25,
    "macd":      0.30,
    "bollinger": 0.20,
    "ma":        0.20,
    "volume":    0.05,
}

# ─────────────────────────────────────────────
# Auto-trading (Alpaca)
# ─────────────────────────────────────────────
# Set ALPACA_API_KEY and ALPACA_SECRET_KEY as environment variables.
# Paper trading uses paper-api.alpaca.markets (fake money, safe to test).
# Live trading uses api.alpaca.markets (real money — requires explicit --live flag).

# Entry conditions — a stock must pass ALL THREE to trigger a buy:
#   1. Signal label must be BUY or STRONG BUY
#   2. final_score must be >= TRADE_MIN_SCORE (filters out weak BUY signals near 0.3)
#   3. At least 2 of 3 technical confirmations must be true (see auto_trader.py)
# Exit: close when signal drops to SELL or STRONG SELL.
TRADE_ENTRY_SIGNAL    = "BUY"           # BUY or STRONG BUY both qualify
TRADE_MIN_SCORE       = 0.55            # score must be above this (BUY starts at 0.3 — this cuts weak ones)
TRADE_EXIT_SIGNAL     = "SELL"          # signal at which to close

# Position sizing: fraction of account equity per trade, hard-capped in USD.
# STRONG BUY gets 1.5× the base size (higher conviction = slightly larger bet).
POSITION_SIZE_PCT     = 0.04            # 4% of account equity per position
POSITION_SIZE_STRONG  = 0.06            # 6% for STRONG BUY
MAX_POSITION_USD      = 5000            # hard cap regardless of equity

# Risk controls
MAX_OPEN_POSITIONS    = 8               # maximum simultaneous holdings
STOP_LOSS_PCT         = 0.04            # 4% below entry → automatic stop-loss
TAKE_PROFIT_PCT       = 0.10            # 10% above entry → automatic take-profit
MIN_ACCOUNT_EQUITY    = 1000            # refuse to trade if equity falls below this
TRADE_COOLDOWN_HOURS  = 4              # hours before re-entering a recently exited symbol
TRADE_CHECK_INTERVAL  = 60             # seconds between auto-trade checks

# ─────────────────────────────────────────────
# Short-term mode (1-5 day holding horizon)
# ─────────────────────────────────────────────

SHORT_PRICE_UPDATE_INTERVAL    = 45
SHORT_CATALYST_UPDATE_INTERVAL = 120

# Final score = short_tech * SHORT_TECH_WEIGHT + catalyst * SHORT_CATALYST_WEIGHT
# Catalyst (fresh news + rating changes) gets far more weight than the
# long-term-mode's static analyst rating, since it actually drives short moves.
SHORT_TECH_WEIGHT     = 0.60
SHORT_CATALYST_WEIGHT = 0.40

# Reuses the same -2..+2-ish scale as long-term mode, so the same thresholds apply
SHORT_STRONG_BUY_THRESHOLD  =  1.0
SHORT_BUY_THRESHOLD         =  0.3
SHORT_SELL_THRESHOLD        = -0.3
SHORT_STRONG_SELL_THRESHOLD = -1.0
