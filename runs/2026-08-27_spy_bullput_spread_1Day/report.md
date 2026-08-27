# Backtest Report — SPY Bull Put Credit Spread (options-only)

## Performance vs Benchmarks

| | Total Return | Ann. Return | Max Drawdown | Sharpe | Final Equity |
|---|---:|---:|---:|---:|---:|
| **Strategy** | 87.08% | 10.87% | -2.99% | 3.52 | $187,080.03 |
| SPY buy-hold | 138.07% | 15.36% | -25.38% | 0.95 | $238,074.61 |

## Strategy configuration
- Underlying: SPY (daily bars, feed=iex, raw adjustment)
- Window: 2020-07-27 → 2026-08-27
- Regime filter: close > SMA(80)
- Short put: ~95% of spot (30-day DTE target)
- Protective long put: ~88% of spot
- Fill model: next_open; friction 100 bps/side
- Options pricing: Black-Scholes, r=0.04, q=0.013, IV=0.15
- Sizing: risk 10% of equity per trade, up to 1 position(s)

## Results
- Trades: 167 · Wins: 159 (95.21%) · Losses: 8
- Profit factor: 3.392943299272364
- Gross profit: $38,059.12 · Gross loss: $11,217.14
- First trade: 2020-11-17 → 2020-11-25 (SPY201216P00344000), P&L $246.17
- Last trade:  2026-08-17 → 2026-08-27 (SPY260914P00737000), P&L $105.22

## Most important caveats
- 2022-01-20: underlying 451.83 < short strike 452; forced close.
- 2022-04-25: underlying 426.05 < short strike 436; forced close.
- 2022-08-29: underlying 405.35 < short strike 408; forced close.
- 2022-12-19: underlying 383.35 < short strike 387; forced close.
- 2025-03-05: underlying 576.88 < short strike 581; forced close.
- 2026-03-23: underlying 648.48 < short strike 655; forced close.
- Position still open at end of window; marked at last close 769.60.

> **Important disclosure**: This backtest is a hypothetical historical simulation and does not represent actual trading performance. Backtested results do not guarantee future results. Results depend on market-data quality, data feed selection, corporate-action handling, fees, slippage, liquidity, taxes, execution assumptions, and implementation details. Option premiums are MODEL-ESTIMATED (Black-Scholes), not historical market fills. This material is for research and educational purposes only and is not investment advice. All investments involve risk and may lose value. Review Alpaca's disclosures at https://alpaca.markets/disclosures.