# Backtest Report — SPY Bull Put Credit Spread (options-only)

## Performance vs Benchmarks

| | Total Return | Ann. Return | Max Drawdown | Sharpe | Final Equity |
|---|---:|---:|---:|---:|---:|
| **Strategy** | 56.60% | 7.67% | -3.85% | 2.96 | $156,601.37 |
| SPY buy-hold | 138.07% | 15.36% | -25.38% | 0.95 | $238,074.61 |

## Strategy configuration
- Underlying: SPY (daily bars, feed=iex, raw adjustment)
- Window: 2020-07-27 → 2026-08-27
- Regime filter: close > SMA(100)
- Short put: ~95% of spot (30-day DTE target)
- Protective long put: ~88% of spot
- Fill model: next_open; friction 100 bps/side
- Options pricing: Black-Scholes, r=0.04, q=0.013, IV=0.15
- Sizing: risk 10% of equity per trade, up to 1 position(s)

## Results
- Trades: 117 · Wins: 106 (90.60%) · Losses: 11
- Profit factor: 2.714334770708319
- Gross profit: $29,641.54 · Gross loss: $10,920.37
- First trade: 2020-12-16 → 2020-12-29 (SPY210114P00351000), P&L $183.66
- Last trade:  2026-08-27 → 2026-08-27 (SPY260925P00728000), P&L $13.67

## Most important caveats
- 2022-01-20: underlying 451.83 < short strike 453; forced close.
- 2022-02-23: underlying 429.58 < short strike 434; forced close.
- 2022-08-31: underlying 398.17 < short strike 399; forced close.
- 2022-09-19: underlying 385.58 < short strike 386; forced close.
- 2022-12-20: underlying 380.00 < short strike 382; forced close.
- 2025-03-05: underlying 576.88 < short strike 582; forced close.
- Position still open at end of window; marked at last close 769.60.

> **Important disclosure**: This backtest is a hypothetical historical simulation and does not represent actual trading performance. Backtested results do not guarantee future results. Results depend on market-data quality, data feed selection, corporate-action handling, fees, slippage, liquidity, taxes, execution assumptions, and implementation details. Option premiums are MODEL-ESTIMATED (Black-Scholes), not historical market fills. This material is for research and educational purposes only and is not investment advice. All investments involve risk and may lose value. Review Alpaca's disclosures at https://alpaca.markets/disclosures.