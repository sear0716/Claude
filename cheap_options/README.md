# cheap_options

Scans listed stock options and ranks the contracts that are priced cheaply, using five independent
tests taken from the academic and practitioner literature on option mispricing.

```bash
pip install -r cheap_options/requirements.txt
python -m cheap_options --demo                      # synthetic data, no network: see what the output looks like
python -m cheap_options AAPL MSFT NVDA SPY          # live scan (Yahoo Finance, ~15 min delayed)
python -m cheap_options --watchlist tickers.txt --top 40 --csv cheap.csv
python -m cheap_options AAPL --right C --min-dte 30 --max-dte 90 --min-oi 500
python -m cheap_options AAPL --sort smile           # strikes cheap relative to their neighbours
python -m cheap_options AAPL --provider schwab      # real-time, uses the schwab_mcp login in this repo
```

Run it from the repo root, during market hours (outside them most data sources return no bid/ask, and
the scanner says so instead of ranking stale prices).

## What "cheap" means here

An option's price is a bet on how much the stock will move. The price is usually quoted as
**implied volatility (IV)**: the annualised move the price assumes. An option is cheap when the
market is charging for less movement than is likely. No single measure settles that, so the scanner
uses five and combines them into a 0-100 score (50 = fairly priced).

| Measure | Weight | Question it answers | Basis |
|---|---|---|---|
| `vrp` | 30% | Is the expiry's at-the-money IV below the realised volatility we should expect until expiry? | Goyal & Saretto (2009): stocks whose IV is low relative to historical volatility have high subsequent straddle returns. |
| `hist` | 20% | Over every past window as long as this option's life, what did this option pay on average, versus its price today? | Model-free check; no pricing-model assumptions beyond put-call parity. |
| `level` | 15% | Is IV low compared with its own past year? (IV percentile) | Standard brokerage measure (IV rank / IV percentile); volatility mean-reverts. |
| `smile` | 15% | Is this strike's IV below what the neighbouring strikes of the same expiry imply? | Relative-value check across strikes; a dislocation larger than the bid-ask spread. |
| `term` | 10% | Is this expiry's at-the-money IV below the curve through the neighbouring expiries? | Vasquez (2017): the slope of the IV term structure predicts straddle returns. |
| `spread` | 10% | How much is lost to the bid-ask spread? (always a deduction) | Transaction costs erase most option anomalies for retail-size traders. |

Verdicts: `CHEAP` (score 65+), `cheap-ish` (55-65), `fair` (45-55), `rich` (<45).

### How each number is computed

- **Forward price** per expiry comes from put-call parity at the strike where call and put mids are
  closest (the method the Cboe VIX calculation uses). This builds dividends and borrow costs into the
  forward, so no dividend yield is needed.
- **Implied volatility** is solved from the bid-ask midpoint with the Black-76 model on that forward.
  Only out-of-the-money options are ranked (calls above the forward, puts below). By put-call parity an
  in-the-money option carries the same mispricing as the out-of-the-money option at its strike, and
  out-of-the-money quotes are tighter and almost free of early-exercise premium, which matters because
  US stock options are American-style and the model is European.
- **IV vs forecast** uses the expiry's at-the-money IV for every strike. Out-of-the-money calls always
  carry a lower IV than at-the-money options (skew), so comparing each strike's own IV with a single
  forecast would mark them cheap for no reason; strike-level pricing is judged by `hist` and `smile`.
- **Realised volatility**: 20-day, 60-day and 1-year close-to-close, plus the 20-day Yang-Zhang (2000)
  estimator, which uses open/high/low/close and is several times more efficient.
- **Volatility forecast** (`Fcst`): starts from the current level (RiskMetrics EWMA, lambda 0.94) and
  decays to the 1-year level with a 30-trading-day half-life, averaged over the option's remaining
  life. This is the shape of a GARCH(1,1) forecast: short-dated options are compared with recent
  volatility, long-dated ones with the long-run level.
- **Historical payoff** (`HistEV%`, `Win%`): takes every overlapping window of the option's length from
  the last 3 years of prices, rescales them so their average equals the forward (removing the stock's
  past trend, so this is a test of movement, not direction), and averages the option's payoff.
  `HistEV%` is that average against today's mid price; `Win%` is how often the payoff beat the premium.
- **IV percentile**: free data has no IV history, so on first use the scanner ranks today's 30-day IV
  against the past year of 21-day realised volatility and labels it `realised-vol proxy`. Because IV
  normally sits above realised volatility, the proxy leans towards calling options expensive. Every run
  stores the day's 30-day IV in `~/.cheap_options/iv_history.sqlite`; after 30 daily runs for a symbol
  the scanner switches to its true IV history. Scheduling a daily run makes this measure exact.
- **Smile**: a weighted quadratic in standardised moneyness, refitted once without outliers, then
  shifted at each strike by the median gap of the two strikes on either side. Real smiles are not
  exact quadratics, and without that shift a whole wing that the curve fits badly would look cheap;
  with it, only a strike that is out of line with its own neighbours scores. The gap is judged against the option's own
  bid-ask width in volatility terms; a gap inside the spread scores near zero.

### Output

Three tables: one row per underlying (IV vs forecast and history, term-structure slope, skew), one per
expiry (IV, forecast, implied move from the at-the-money straddle vs the average historical move), and
the ranked contracts. `(E)` marks expiries that span an earnings date: IV is normally raised ahead of
earnings, and history-based comparisons understate the coming move, so read those rows with care.
`--csv` and `--json` write the same data with every component score.

## Filters (defaults)

7-120 days to expiry, open interest of at least 100, spread at most 25% of mid, absolute delta between
0.10 and 0.60. The delta floor is deliberate: far out-of-the-money options look cheap in dollars but
are the most overpriced part of the market (Boyer & Vorkink 2014; Bakshi, Kapadia & Madan 2003).
All are adjustable; see `python -m cheap_options --help`.

## Limits to keep in mind

- **Cheap is relative.** Option buyers pay a premium on average: implied volatility exceeds realised
  volatility most of the time (Bakshi & Kapadia 2003; Carr & Wu 2009). A high score means less
  expensive than usual, not a profitable trade. This is a screen, not investment advice.
- **Low IV can be right.** After a takeover offer, or once a known event has passed, low IV reflects
  real information. Check the news before treating a low reading as an opportunity.
- **History is not the future.** The forecast and the historical-payoff test both assume the stock
  will move roughly as it has. They cannot see a scheduled event that has no precedent in the data.
- **Yahoo data is delayed** and occasionally stale for thin contracts; the liquidity filters remove
  most of it. Use `--provider schwab` for real-time quotes.
- The `--demo` data is synthetic and is never written to the IV history.

## Data sources

| Provider | Needs | Notes |
|---|---|---|
| `yahoo` (default) | `yfinance` | Free, no account, delayed. Also supplies the next earnings date. |
| `schwab` | the `schwab_mcp` setup in this repo | Real-time chain and 5 years of daily prices; read-only calls. |
| `--from-snapshot` | files written by `--save-snapshot DIR` | Re-run the analysis offline on saved data. |

## References

- Bakshi, G. & Kapadia, N. (2003). Delta-hedged gains and the negative market volatility risk premium. *Review of Financial Studies*.
- Bakshi, G., Kapadia, N. & Madan, D. (2003). Stock return characteristics, skew laws, and the differential pricing of individual equity options. *Review of Financial Studies*.
- Black, F. (1976). The pricing of commodity contracts. *Journal of Financial Economics*.
- Boyer, B. & Vorkink, K. (2014). Stock options as lotteries. *Journal of Finance*.
- Carr, P. & Wu, L. (2009). Variance risk premiums. *Review of Financial Studies*.
- Cboe. *VIX Index methodology* (forward level from put-call parity; model-free implied variance).
- Goyal, A. & Saretto, A. (2009). Cross-section of option returns and volatility. *Journal of Financial Economics*.
- J.P. Morgan/Reuters (1996). *RiskMetrics Technical Document* (EWMA volatility).
- Vasquez, A. (2017). Equity volatility term structures and the cross section of option returns. *Journal of Financial and Quantitative Analysis*.
- Yang, D. & Zhang, Q. (2000). Drift-independent volatility estimation based on high, low, open, and close prices. *Journal of Business*.

## Tests

```bash
python -m pytest cheap_options
```
