# Options scout: find cheap stock options

Scans listed US stock and ETF options and ranks the ones whose **implied volatility (IV) is cheap**.
It is read-only research: it never places orders.

## What "cheap" means here

An option's price is mostly a bet on volatility. Given the stock price, strike, time and rates,
the price and the IV are the same thing in different units (Black-Scholes-Merton maps one to the other).
So "is this option cheap?" becomes "is its IV low?", and the question is *low compared with what*.
The scanner checks four things:

| Test | Formula | Cheap when |
|---|---|---|
| **IV vs forecast realized vol** (main test) | vol gap = 1 − IV(ask) / RV̂(T) | IV at the **ask** is below the vol the stock is expected to realize over the option's life (gap > 0). This is the `cheap` flag. |
| **Model vs market price** | edge = BSM(σ = RV̂(T)) / ask − 1 | Black-Scholes using forecast vol is worth more than the ask. |
| **Smile residual** | IV(mid) − fitted smile(log K/F) | The strike's IV sits below a vega-weighted quadratic smile fitted to the out-of-the-money options of the same expiry, i.e. cheap against its neighbours. |
| **IV rank / percentile** (per stock) | rank = (IV₃₀ − min) / (max − min) over 1 year | Today's 30-day ATM IV is near the bottom of its 1-year range. |

Using the ask, not the mid, keeps it honest: a buyer pays the ask.

**Realized-vol forecast RV̂(T).** From daily OHLC the scanner computes close-to-close vol (10/20/60/252 days),
Parkinson (high-low) and Yang-Zhang (gaps + intraday range) over 20 days, and RiskMetrics EWMA (λ = 0.94).
The current level is the average of Yang-Zhang 20d, EWMA and close-to-close 20d; the long-run level is the 1-year vol.
Variance then mean-reverts like a GARCH(1,1) with a half-life h (default 30 trading days):

    avg variance over T = σ²_long + (σ²_now − σ²_long) · (1 − e^(−kT)) / (kT),   k = ln 2 / h

so a 1-week option is compared with today's vol and a 6-month option mostly with the 1-year average.

**Score** (points, higher is cheaper): 45 × vol gap (capped at ±25%) + 20 × smile residual (capped at ±3 vol pts)
+ 20 × (50 − IV rank)/50 − 15 × spread/max spread. Contracts must also pass liquidity filters:
two-sided quote, bid ≥ $0.05, spread ≤ 15% of mid, open interest ≥ 100, |delta| between 0.10 and 0.80.

## Data sources

| Source | Quotes | IV history (for IV rank) | Notes |
|---|---|---|---|
| `--source ibkr` (default) | TWS / IB Gateway via `ib_async`, read-only | IB's daily 30-day IV index, 1 year | Needs an OPRA options data subscription, or `IB_MARKET_DATA_TYPE=3` for delayed data. Uses client id 19 (`OPTIONS_IB_CLIENT_ID`) so it can run next to `trading_agent`. Same `IB_HOST` / `IB_PORT` settings as `trading_agent`. |
| `--source yahoo` (also the automatic fallback when IBKR is unreachable) | Yahoo Finance via `yfinance`, ~15 min delayed | Built up locally: each run logs the stock's 30-day ATM IV to `~/.cache/options_scout/iv_history/`, so IV rank appears after ~20 trading days of runs | Yahoo's bid/ask are often 0 when the market is closed: run during market hours. |
| `--source demo` | Synthetic chain with a few planted cheap contracts | Synthetic | Offline try-out only. Not market data. |

The scanner computes every IV itself from bid/mid/ask (it does not trust the vendor's IV), so results
are comparable across sources. The vendor IV is kept in the CSV as `source_iv` for reference.
The risk-free rate is the latest 3-month T-bill from FRED (override with `--rate`). Dividend yield comes from
Yahoo's trailing 12-month dividends; with IBKR pass `--div-yield` for dividend payers (default 0).

## Run it

```bash
pip install -r options_scout/requirements.txt

python -m options_scout --source demo                      # try it offline
python -m options_scout AAPL,MSFT,NVDA,SPY                 # IBKR (TWS paper on 7497), falls back to Yahoo
python -m options_scout AAPL,TSLA --source yahoo --max-dte 60
python -m options_scout SPY --min-dte 20 --max-dte 60 --delta 0.25,0.60 --min-oi 1000 --top 10
python -m options_scout QQQ --all                          # rank all liquid contracts, cheap or not
```

Output goes to the terminal and to `reports/options/`: `cheap_options.md`, `cheap_options.json`, and
`options_scan.csv` with every quoted contract and all metrics (IV at bid/mid/ask, forecast RV, delta, vega, etc).

## Things that make IV look cheap when it is not

- **Earnings and other events.** If the past year contains big earnings gaps, 1-year realized vol is inflated, and
  options expiring *before* the next report will look cheap. Options spanning an earnings date carry the event
  premium and look rich. Check the earnings calendar before acting on a signal.
- **American exercise.** BSM ignores early exercise. Calls on non-dividend payers are exact; deep in-the-money
  puts and calls on high-dividend stocks are slightly underpriced by the model, so their IVs read a bit high.
  The delta filter keeps most of these out.
- **Stale or one-sided quotes**, especially from delayed feeds. The spread filter and the ask-based test help.
- **Regime changes.** Realized vol is backward-looking; if a known catalyst is ahead, the market is right to price it.

Cheap IV means the option costs less than the stock's recent behaviour implies. It does not mean the trade
will make money: a long option still needs the stock to move more than the market expects, and time decay works
against it every day.

## Tests

```bash
python -m pytest options_scout -q
```

Pricing is checked against textbook values and put-call parity, the realized-vol estimators against a simulated
GBM with known volatility, and the scanner against synthetic chains where the answer is known. The IBKR and
Yahoo sources are tested with fakes (no network).
