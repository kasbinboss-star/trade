WATCHLIST = {
    "Mega-Cap Tech":  ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"],
    "Semiconductors": ["AMD", "INTC", "QCOM", "AVGO", "MU", "TSM", "TXN", "ADI",
                       "AMAT", "LRCX", "KLAC", "NXPI", "ARM", "MRVL"],
    "Software":       ["CRM", "ORCL", "ADBE", "NOW", "SNOW", "INTU", "PANW",
                       "CRWD", "DDOG", "ZS", "NET", "MDB", "TEAM", "WDAY", "SHOP"],
    "Finance":        ["JPM", "BAC", "GS", "V", "MA", "BRK-B", "MS", "WFC", "C",
                       "AXP", "SCHW", "BLK", "SPGI", "CME", "PYPL", "COF"],
    "Healthcare":     ["JNJ", "PFE", "UNH", "ABBV", "LLY", "BMY", "MRK", "TMO",
                       "ABT", "AMGN", "GILD", "CVS", "ISRG", "VRTX", "REGN"],
    "Consumer":       ["WMT", "COST", "HD", "MCD", "NKE", "DIS", "PG", "KO",
                       "PEP", "SBUX", "TGT", "LOW", "TJX", "CMG", "YUM", "EL"],
    "Energy":         ["XOM", "CVX", "COP", "SLB", "EOG", "PSX", "MPC", "VLO", "OXY"],
    "Industrials":    ["CAT", "DE", "BA", "GE", "HON", "UPS", "UNP", "LMT",
                       "RTX", "MMM", "FDX", "ETN"],
    "Communication":  ["NFLX", "CMCSA", "T", "VZ", "TMUS", "WBD", "TTWO"],
    "Materials/Util": ["LIN", "APD", "SHW", "FCX", "NEM", "NEE", "DUK", "SO"],
    "REITs":          ["AMT", "PLD", "EQIX", "SPG", "O"],
    "High Growth":    ["PLTR", "COIN", "XYZ", "RBLX", "UBER", "SPOT", "ABNB",
                       "DASH", "SNAP", "PINS", "ROKU", "LYFT", "HOOD", "SOFI"],
    "ETFs":           ["SPY", "QQQ", "IWM", "DIA", "SMH"],
}

# Company names, used to disambiguate news searches. Bare tickers like "V",
# "MA", "O" or "ALL" match huge amounts of unrelated news, so the news layer
# queries on the company name instead where one is known.
SYMBOL_NAME = {
    "AAPL": "Apple", "MSFT": "Microsoft", "GOOGL": "Alphabet Google",
    "AMZN": "Amazon", "META": "Meta Platforms", "NVDA": "Nvidia", "TSLA": "Tesla",
    "AMD": "AMD", "INTC": "Intel", "QCOM": "Qualcomm", "AVGO": "Broadcom",
    "MU": "Micron", "TSM": "TSMC", "TXN": "Texas Instruments",
    "ADI": "Analog Devices", "AMAT": "Applied Materials", "LRCX": "Lam Research",
    "KLAC": "KLA Corporation", "NXPI": "NXP Semiconductors", "ARM": "Arm Holdings",
    "MRVL": "Marvell Technology",
    "CRM": "Salesforce", "ORCL": "Oracle", "ADBE": "Adobe", "NOW": "ServiceNow",
    "SNOW": "Snowflake", "INTU": "Intuit", "PANW": "Palo Alto Networks",
    "CRWD": "CrowdStrike", "DDOG": "Datadog", "ZS": "Zscaler",
    "NET": "Cloudflare", "MDB": "MongoDB", "TEAM": "Atlassian",
    "WDAY": "Workday", "SHOP": "Shopify",
    "JPM": "JPMorgan Chase", "BAC": "Bank of America", "GS": "Goldman Sachs",
    "V": "Visa", "MA": "Mastercard", "BRK-B": "Berkshire Hathaway",
    "MS": "Morgan Stanley", "WFC": "Wells Fargo", "C": "Citigroup",
    "AXP": "American Express", "SCHW": "Charles Schwab", "BLK": "BlackRock",
    "SPGI": "S&P Global", "CME": "CME Group", "PYPL": "PayPal",
    "COF": "Capital One",
    "JNJ": "Johnson & Johnson", "PFE": "Pfizer", "UNH": "UnitedHealth",
    "ABBV": "AbbVie", "LLY": "Eli Lilly", "BMY": "Bristol Myers Squibb",
    "MRK": "Merck", "TMO": "Thermo Fisher", "ABT": "Abbott Laboratories",
    "AMGN": "Amgen", "GILD": "Gilead Sciences", "CVS": "CVS Health",
    "ISRG": "Intuitive Surgical", "VRTX": "Vertex Pharmaceuticals",
    "REGN": "Regeneron",
    "WMT": "Walmart", "COST": "Costco", "HD": "Home Depot", "MCD": "McDonald's",
    "NKE": "Nike", "DIS": "Disney", "PG": "Procter & Gamble", "KO": "Coca-Cola",
    "PEP": "PepsiCo", "SBUX": "Starbucks", "TGT": "Target", "LOW": "Lowe's",
    "TJX": "TJX Companies", "CMG": "Chipotle", "YUM": "Yum Brands",
    "EL": "Estee Lauder",
    "XOM": "Exxon Mobil", "CVX": "Chevron", "COP": "ConocoPhillips",
    "SLB": "SLB Schlumberger", "EOG": "EOG Resources", "PSX": "Phillips 66",
    "MPC": "Marathon Petroleum", "VLO": "Valero Energy", "OXY": "Occidental Petroleum",
    "CAT": "Caterpillar", "DE": "John Deere", "BA": "Boeing",
    "GE": "GE Aerospace", "HON": "Honeywell", "UPS": "United Parcel Service",
    "UNP": "Union Pacific", "LMT": "Lockheed Martin", "RTX": "RTX Raytheon",
    "MMM": "3M Company", "FDX": "FedEx", "ETN": "Eaton Corporation",
    "NFLX": "Netflix", "CMCSA": "Comcast", "T": "AT&T", "VZ": "Verizon",
    "TMUS": "T-Mobile", "WBD": "Warner Bros Discovery",
    "TTWO": "Take-Two Interactive",
    "LIN": "Linde", "APD": "Air Products", "SHW": "Sherwin-Williams",
    "FCX": "Freeport-McMoRan", "NEM": "Newmont", "NEE": "NextEra Energy",
    "DUK": "Duke Energy", "SO": "Southern Company",
    "AMT": "American Tower", "PLD": "Prologis", "EQIX": "Equinix",
    "SPG": "Simon Property Group", "O": "Realty Income",
    "PLTR": "Palantir", "COIN": "Coinbase", "XYZ": "Block Inc",
    "RBLX": "Roblox", "UBER": "Uber", "SPOT": "Spotify", "ABNB": "Airbnb",
    "DASH": "DoorDash", "SNAP": "Snap Inc", "PINS": "Pinterest",
    "ROKU": "Roku", "LYFT": "Lyft", "HOOD": "Robinhood", "SOFI": "SoFi",
    "SPY": "S&P 500 ETF", "QQQ": "Nasdaq 100 ETF", "IWM": "Russell 2000 ETF",
    "DIA": "Dow Jones ETF", "SMH": "Semiconductor ETF",
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
    "Industrials":    "Indus",
    "Communication":  "Comm",
    "Materials/Util": "Matls",
    "REITs":          "REIT",
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

# Tiered analysis. Technicals are computed for every symbol (one batched
# download, so cost is flat), but catalyst work costs ~1s and ~5 Yahoo API
# calls per symbol -- running it across the whole universe would trigger the
# rate limiting that already caused mass "possibly delisted" failures. So it
# runs only on the names that can actually change a decision.
CATALYST_TOP_N    = 20   # strongest technical setups (buy candidates)
CATALYST_BOTTOM_N = 5    # weakest, so SELL/exit signals stay informed
# Selecting purely by technical rank is circular: the catalyst layer would only
# ever see names the technicals already liked, so it could never surface a
# stock with a real catalyst but middling technicals -- it would just
# rubber-stamp. A rotating slice gives the rest of the universe coverage too
# (10 per cycle over ~143 names ≈ full sweep every ~25 minutes).
CATALYST_ROTATE_N = 10

# Final score = short_tech * SHORT_TECH_WEIGHT + catalyst * SHORT_CATALYST_WEIGHT
# Catalyst (fresh news + rating changes) gets far more weight than the
# long-term-mode's static analyst rating, since it actually drives short moves.
SHORT_TECH_WEIGHT     = 0.60
SHORT_CATALYST_WEIGHT = 0.40

# Reuses the same -2..+2-ish scale as long-term mode, so the same thresholds apply
SHORT_STRONG_BUY_THRESHOLD  =  1.0
# Raised from 0.3, where a BUY fired on ~33% of the universe and the bucket
# returned LESS than simply holding SPY out-of-sample. The backtest validated
# "top ~6% by score" (+0.407% net vs SPY's +0.208% over 3 days), which is a
# 0.50 cut on the PURE TECHNICAL score it measured.
#
# Live, symbols that also have catalyst data are scored 0.6*tech + 0.4*catalyst,
# and catalyst_score has much lower variance than short_score (0.185 vs 0.388),
# so blending compresses the composite -- a 0.50 cut on the blended score fires
# on only ~1.4%, far stricter than validated. 0.40 reproduces the validated
# ~5-6% selectivity live. This is a scale alignment, not a second fit.
#
# Deliberately NOT mirrored on the sell side: only the buy side was validated.
SHORT_BUY_THRESHOLD         =  0.40
SHORT_SELL_THRESHOLD        = -0.3
SHORT_STRONG_SELL_THRESHOLD = -1.0
