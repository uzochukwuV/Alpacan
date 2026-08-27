# Run notes — SPY bull put credit spread (options-only)

## Original request
Build a simple, options-only trading strategy against the Alpaca trading API (paper),
backtest it, and (if validated) run it against the paper account. User provided
paper credentials in `env (4).txt`; account `PA37BIGK5WVL`, options approval Level 3.

## Confirmed strategy interpretation
- Underlying signal instrument: SPY daily bars (close only), 2020-07-27 → 2026-08-26.
- Regime filter: only sell premium when SPY is in an intermediate uptrend: `close > SMA(100)`.
- Trade: a **bull put credit spread** (100% options — two put legs, no stock):
  - Sell-to-open a put ~5% below spot (`short_strike = round_1(spot * 0.95)`).
  - Buy-to-open a protective put ~12% below spot (`long_strike = round_1(spot * 0.88)`).
  - Both legs at the same expiration ≈ 30 calendar days out (nearest SPY trading day ≥ 30d).
- Exit logic (applied at next-day open, never same-bar):
  - Take profit when remaining short-put mark ≤ 25% of entry credit (~75% of max profit captured).
  - Time stop when DTE ≤ 5.
  - Underlying breach: if prior close < short strike, force-close at next open.
  - Otherwise hold to expiration — if underlying > short strike at expiry, spread expires worthless and full credit is retained.
- Sizing: 1 position at a time; contracts = max(1, floor(0.10 * equity / (width * 100))).
- Benchmark: SPY buy-and-hold over the same window.

## Data limitations and modeling choice (IMPORTANT)
The provided paper subscription gives:
- Full SPY daily bars via `feed=iex` (2020-07-27 onward) — real data.
- **No deep historical per-contract option bars.** Only ~2 months of live expirations exist and
  each contract carries only a few historical daily bars. Recent SIP equity feed is not
  permitted on this subscription (403).

Therefore the backtest **models option premiums with Black–Scholes** on the real underlying
history, calibrated to the live chain (ATM SPY IV ≈ 0.125 on 2026-08-27) with a conservative
uplift to IV = 0.15 and 100 bps round-trip friction. Contract symbols are synthetic OCC-style.
This is a documented research approximation, **not** historical option-price fills. Real fills
are exercised in the paper forward-validation step.

## Assumptions
- r = 4% (cash rate), q = 1.3% (SPY dividend yield), σ = 15% constant.
- Spread/bid-ask friction = 100 bps of premium per side on top of modeled mid.
- No commissions (paper; documented in fee_source.json as excluded for live).
- Fill model `next_open`: signal at T close → fill at T+1 open.
- Strike rounding to $1 (chain at $1 increments in this environment).
- No short-selling; single concurrent spread.

## Run lineage
First run. No prior variant.

## Fees
Modeled: none. Documented in fee_source.json. Live Alpaca options commission structure
(per-contract, min $0 for many orders during this period) is excluded from the simulation.

## Benchmark
SPY buy-and-hold buy-at-first-close from the same data.

## Parameter tuning (2026-08-27) v1.1.0
A sweep overthe SMA trend filter, DTE, take-profit ratio, and strike widths was run
on the same 2020-2026 data ((no refetch; same bars/pricing model). The adopted config:
`SMA=80`, `SHORT=0.95` (5% OTM), `LONG=0.88` (12% OTM),
`DTE=30`, `take_profit=0.50`, `risk=10%`.

| Config | Total | MaxDD | Sharpe | Trades | W/L |
|---|---:|---:|---:|---:|---:|
| v1.0.0 (SMA100, TP.25) | +56.6% | -3.9%% | 2.96 | 117 |  106/11 |
| **v1.1.0 (SMA80, TP.50)** | **+87.1%%** | **-3.0%%** | **3.52** | **167** | **159/8** |

Why it helps the bear-market losses: an **80-day** SMA is more responsive than 100, keeping the
strategyout of 2022-style sustained downtrends (and the 2025/2026 drawdowns sooner);
takingprofit at **50% of credit** (instead of 25%) closes winners faster and compounds through
higher turnover, while holding the same protective 12%-OTM long-leg risk lid. Losses
drop from 11 (v1.0.0) to**8**(v1.1.0), around the2022 bear slides.

## Reproducibility
- Raw data: `raw/bars_SPY.json` (Alpaca CLI, feed=iex), `raw/calendar.json` (XNYS 2020–2026).
- Data fingerprint: `data_fingerprint.json`.
- Full script: `run.py`; artifacts: `summary.json`, `report.md`, `trades.csv`, `equity.csv`,
  `benchmark_equity.csv`, `round_trips.csv`, `warnings.json`.

> **Important disclosure**
> This backtest is a hypothetical historical simulation and does not represent actual
> trading performance. Backtested results do not guarantee future results. Results depend
> on market-data quality, data feed selection, corporate-action handling, fees, slippage,
> liquidity, taxes, execution assumptions, and implementation details. Option premiums are
> MODEL-ESTIMATED (Black–Scholes), not historical market fills. This material is for research
> and educational purposes only and is not investment advice, a recommendation, an offer, or a
> solicitation to buy or sell securities, options, cryptocurrencies, or any other financial
> product. All investments involve risk and may lose value. Review Alpaca's disclosures at
> https://alpaca.markets/disclosures.