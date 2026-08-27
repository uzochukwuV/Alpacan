# SPY Bull Put Credit Spread — Alpaca Options Trading Agent

An options-only trend strategy running against the **Alpaca paper-trading API**,
backtested over 6 years and live-managed automatically.

Built with the **Alpaca Skills Library** (`alpacahq/alpaca-skills`):
`alpaca-trading-backtest`, `alpaca-trading-paper-trading`,
`alpaca-trading-paper-trading-cli` (installed in `.agents/skills/`).

## Strategy

- **Signal** (options-only, spot is just the trigger): SPY daily close > SMA(100).
- **Trade**: Bull put credit spread.
  - Sell-to-open a put ~5% below spot.
  - Buy-to-open a protective put ~12% below spot.
  - Same expiration, nearest SPY trading day ≥ 30 calendar days.
- **Exits** (checked on each poll, fill at the next order):
  - Take-profit when spread cost-to-close ≤ 25% of the entry credit (~75% captured).
  - Time-stop at ≤ 5 DTE.
  - Breach stop: force-close if spot < short strike.
  - Otherwise let it expire (credit retained).
- **Sizing**: risk 10% of equity per trade; max 1 position; daily-loss circuit -5%.

## Repository layout

```
.env                       # paper credentials (gitignored)
strategy_runtime.py        # paper-trading runtime (check/tick/run/closeall/report)
scripts/                   # data probing helpers
runs/2026-08-27_spy_bullput_spread_1Day/
   run.py                  # deterministic backtest
   raw/                    # SPY bars (IEX) + market calendar
   normalized/             # clean CSV
   strategy_spec.json      # strategy contract
   config.json             # backtest config
   notes.md                # modeling notes / caveats
   summary.json trades.csv equity.csv benchmark_equity.csv
   report.md               # backtest report
run_data/                  # live paper runtime logs & state (gitignored w/ runs/)
```

## Backtest results (2020-07-27 → 2026-08-27, daily bars, options priced with
Black–Scholes at IV=0.15 + 100bps friction — see `notes.md` caveats)

| Metric | Strategy | SPY buy-hold |
|---|---|---:|
| Total return | **+56.6%** | +138.1% |
| Annualized | 7.7% | 15.4% |
| Max drawdown | **-3.9%** | -25.4% |
| Sharpe | **2.96** | 0.95 |
| Win rate | **90.6%** | — |
| Profit factor | **2.71** | — |
| Trades | 117 (106 win / 11 loss) | — |

The strategy underperforms buy-and-hold in raw return but with a far better
return/risk profile and a ~3.9% worst drawdown. Returns model the credit-spread
premium via Black-Scholes because historical per-contract option bars are not
available on this data subscription; live paper fills replace modeled fills.

## Commands

```bash
# Backtest (reproduces run artifacts)
.venv/bin/python runs/2026-08-27_spy_bullput_spread_1Day/run.py

# Paper runtime
.venv/bin/python strategy_runtime.py check     # connectivity + contract dry-run
.venv/bin/python strategy_runtime.py tick --yes  # one decision cycle (auto-confirm)
.venv/bin/python strategy_runtime.py run --yes   # continuous loop (5-min poll)
.venv/bin/python strategy_runtime.py report      # portfolio summary
.venv/bin/python strategy_runtime.py closeall    # flatten all paper options
```

## Current live paper status

- Account `PA37BIGK5WVL` (paper), options approval level 3.
- Open position (opened 2026-08-27): sell 1× `SPY260930P00731000` @ 2.83,
  buy 1× `SPY260930P00677000` @ 0.82 → net credit $2.01. Expiry 2026-09-30.
- The background runtime (`run --yes`) polls every 5 minutes and manages exits.

## Disclaimers

Hypothetical backtest ≠ future results. Option premiums are model-estimated
(Black-Scholes), not historical market fills. Paper trading is simulated.
This is research/education, not investment advice. See `runs/.../notes.md` and
Alpaca's disclosures (https://alpaca.markets/disclosures).