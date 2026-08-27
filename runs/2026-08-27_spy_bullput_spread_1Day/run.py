#!/usr/bin/env python3
"""
SPY bull put credit spread — deterministic historical backtest.

Signal: SPY close > SMA(100)  (bull regime)
Trade : sell-to-open put ~5% OTM, buy-to-open put ~12% OTM, ~30 DTE
        (bull put credit spread). Options-only.
Fill  : next_open — signal at bar T close, fill at bar T+1 open.
Price : option premiums modeled with Black-Scholes (documented assumption;
        historical per-contract option bars unavailable on this subscription).

Run this script from the run folder. Requires normalized/bars_SPY.csv.
"""
import csv
import json
import math
import os
from datetime import date, datetime, timedelta

# ---------------------------------------------------------------- options pricing
def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def bs_put_price(S, K, T, r, q, sigma):
    """Black-Scholes European put. T in years."""
    if T <= 0:
        return max(K - S, 0.0)
    if sigma == 0:
        return max(K - S, 0.0)
    sqT = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / sqT
    d2 = d1 - sqT
    return K * math.exp(-r * T) * norm_cdf(-d2) - S * math.exp(-q * T) * norm_cdf(-d1)

RATE = 0.04
DIVY = 0.013
IV = 0.15
FRICTION_BPS = 100.0          # per side
FRICTION = FRICTION_BPS / 10000.0

# ---------------------------------------------------------------- config
INITIAL_CASH = 100000.0
SMA_WINDOW = 100
SHORT_PCT = 0.95              # short put strike at 95% of spot
LONG_PCT = 0.88               # long put strike at 88% of spot
TARGET_DTE = 30               # calendar days to expiration
MAX_DTE_CLOSE = 5             # time stop
TAKE_PROFIT_RATIO = 0.25      # remaining short premium <= 25% of credit -> close
RISK_PER_TRADE_PCT = 0.10
MAX_POSITIONS = 1

def round_strike(x):
    return round(round(x))

# ---------------------------------------------------------------- data loading
def load_bars(path):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            ts = r["timestamp"].split("T")[0]
            rows.append({
                "date": datetime.strptime(ts, "%Y-%m-%d").date(),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),
            })
    rows.sort(key=lambda r: r["date"])
    return rows

def load_calendar(path):
    days = set()
    with open(path) as f:
        d = json.load(f)
    for day in d.get("calendar", []):
        days.add(day["date"])
    return days

def sma(rows, window):
    out = [None] * len(rows)
    s = 0.0
    for i, r in enumerate(rows):
        s += r["close"]
        if i >= window:
            s -= rows[i - window]["close"]
        if i >= window - 1:
            out[i] = s / window
    return out

# ---------------------------------------------------------------- strategy state
class Position:
    def __init__(self, open_date, exp_date, short_strike, long_strike, short_sym, long_sym,
                 credit_short, debit_long, contracts, spot_entry):
        self.open_date = open_date
        self.exp_date = exp_date
        self.short_strike = short_strike
        self.long_strike = long_strike
        self.short_sym = short_sym
        self.long_sym = long_sym
        self.credit_short = credit_short
        self.debit_long = debit_long
        self.contracts = contracts
        self.spot_entry = spot_entry
        self.net_credit = credit_short - debit_long
        self.realized = 0.0          # realized P&L on close (positive = profit)
        self.close_reason = None

    def max_risk(self):
        return (self.short_strike - self.long_strike - self.net_credit) * 100 * self.contracts

def expiration_with_dte(anchor_date, calendar, target_dte):
    """Nearest calendar date >= anchor + target_dte that is a trading day."""
    t = anchor_date + timedelta(days=target_dte)
    while t.isoformat() not in calendar:
        t += timedelta(days=1)
    return t

def occ_symbol(root, exp_date, cp, strike):
    return f"{root}{exp_date.strftime('%y%m%d')}{cp}{round(strike * 1000):08d}"

def main():
    base = os.path.dirname(os.path.abspath(__file__))
    bars = load_bars(os.path.join(base, "normalized", "bars_SPY.csv"))
    calendar = load_calendar(os.path.join(base, "raw", "calendar.json"))
    n = len(bars)
    sma_vals = sma(bars, SMA_WINDOW)

    equity = INITIAL_CASH
    cash = INITIAL_CASH
    position = None
    trades = []                    # flat trade list
    round_trips = []
    equity_curve = []
    warnings = []

    idx = 0
    while idx < n:
        bar = bars[idx]
        d = bar["date"]
        # ------------------------------------------------------------------
        # 0) MANAGEMENT for an open position uses *yesterday-closed* values
        #    decided at idx-1; fill occurs at this bar's open.
        # ------------------------------------------------------------------
        if position is not None and position.close_reason is None:
            # take profit / breach / time check computed on PREVIOUS bar close
            prev = bars[idx - 1]
            dte = (position.exp_date - d).days
            t_prev_close = (position.exp_date - prev["date"]).days / 365.0
            short_mark_prev = bs_put_price(prev["close"], position.short_strike,
                                           t_prev_close, RATE, DIVY, IV)
            credit_frac_remaining = short_mark_prev / max(position.credit_short, 1e-9)
            do_close = False
            reason = None
            if prev["close"] < position.short_strike:
                do_close = True
                reason = "breach"
                warnings.append(f"{d}: underlying {prev['close']:.2f} < short strike "
                                f"{position.short_strike}; forced close.")
            elif credit_frac_remaining <= TAKE_PROFIT_RATIO:
                do_close = True
                reason = "take_profit"
            elif dte <= MAX_DTE_CLOSE:
                do_close = True
                reason = "time_stop"

            if do_close:
                # fill at TODAY's open
                t_today = max((position.exp_date - d).days, 0) / 365.0
                short_mark = bs_put_price(bar["open"], position.short_strike,
                                          t_today, RATE, DIVY, IV)
                long_mark = bs_put_price(bar["open"], position.long_strike,
                                         t_today, RATE, DIVY, IV)
                # buy-to-close short (pay ask = model*(1+friction)), sell-to-close long
                close_short_cost = short_mark * (1 + FRICTION)
                close_long_credit = long_mark * (1 - FRICTION)
                net_cost = (close_short_cost - close_long_credit) * 100 * position.contracts
                position.realized = position.net_credit * 100 * position.contracts - net_cost
                cash += position.realized
                position.close_reason = reason
                trade = {
                    "open_date": position.open_date.isoformat(),
                    "close_date": d.isoformat(),
                    "exp_date": position.exp_date.isoformat(),
                    "short_symbol": position.short_sym,
                    "long_symbol": position.long_sym,
                    "spot_entry": round(position.spot_entry, 2),
                    "spot_exit": round(bar["open"], 2),
                    "contracts": position.contracts,
                    "net_credit_per_contract": round(position.net_credit, 2),
                    "max_risk_per_contract": round(position.short_strike - position.long_strike - position.net_credit, 2),
                    "realized_pnl": round(position.realized, 2),
                    "close_reason": reason,
                    "dte_at_close": (position.exp_date - d).days,
                }
                trades.append(trade)
                round_trips.append(dict(trade))
                position = None

        # ------------------------------------------------------------------
        # 1) OPEN new position (decision on completed bar idx, fill at idx+1 open)
        # ------------------------------------------------------------------
        if position is None and idx + 1 < n and sma_vals[idx] is not None:
            close = bar["close"]
            if close > sma_vals[idx]:
                # expire candidate: from next trading day ~30 calendar days out
                exp_date = expiration_with_dte(d, calendar, TARGET_DTE)
                short_strike = round_strike(close * SHORT_PCT)
                long_strike = round_strike(close * LONG_PCT)
                if long_strike < short_strike:
                    # price next open
                    t = (exp_date - bars[idx + 1]["date"]).days / 365.0
                    if t > 0:
                        S_open = bars[idx + 1]["open"]
                        short_sym = occ_symbol("SPY", exp_date, "P", short_strike)
                        long_sym = occ_symbol("SPY", exp_date, "P", long_strike)
                        short_mid = bs_put_price(S_open, short_strike, t, RATE, DIVY, IV)
                        long_mid = bs_put_price(S_open, long_strike, t, RATE, DIVY, IV)
                        credit_short = short_mid * (1 - FRICTION)   # we SELL (receive bid)
                        debit_long = long_mid * (1 + FRICTION)      # we BUY (pay ask)
                        if credit_short > debit_long:
                            width = short_strike - long_strike
                            max_risk = (width - (credit_short - debit_long)) * 100
                            contracts = max(1, int(equity * RISK_PER_TRADE_PCT / max_risk))
                            position = Position(
                                open_date=bars[idx + 1]["date"],
                                exp_date=exp_date,
                                short_strike=short_strike,
                                long_strike=long_strike,
                                short_sym=short_sym,
                                long_sym=long_sym,
                                credit_short=credit_short,
                                debit_long=debit_long,
                                contracts=contracts,
                                spot_entry=S_open,
                            )
                            cash += position.net_credit * 100 * contracts

        # mark-to-market equity at close
        mtm = cash
        if position is not None:
            t_mark = max((position.exp_date - d).days, 0) / 365.0
            short_mark = bs_put_price(bar["close"], position.short_strike, t_mark, RATE, DIVY, IV)
            long_mark = bs_put_price(bar["close"], position.long_strike, t_mark, RATE, DIVY, IV)
            # spread value = long premium - short premium (we are short the spread)
            spread_cost_to_close = (short_mark - long_mark) * 100 * position.contracts
            mtm = cash - spread_cost_to_close
        equity = max(mtm, 0.0)

        equity_curve.append({"date": d.isoformat(), "equity": round(equity, 2)})
        idx += 1

    # close any open position at final bar close (marked to model)
    if position is not None:
        warnings.append(f"Position still open at end of window; marked at last close {bars[-1]['close']:.2f}.")
        t = max((position.exp_date - bars[-1]["date"]).days, 0) / 365.0
        short_mark = bs_put_price(bars[-1]["close"], position.short_strike, t, RATE, DIVY, IV)
        long_mark = bs_put_price(bars[-1]["close"], position.long_strike, t, RATE, DIVY, IV)
        net_cost = (short_mark * (1 + FRICTION) - long_mark * (1 - FRICTION)) * 100 * position.contracts
        position.realized = position.net_credit * 100 * position.contracts - net_cost
        trade = {
            "open_date": position.open_date.isoformat(),
            "close_date": bars[-1]["date"].isoformat(),
            "exp_date": position.exp_date.isoformat(),
            "short_symbol": position.short_sym,
            "long_symbol": position.long_sym,
            "spot_entry": round(position.spot_entry, 2),
            "spot_exit": round(bars[-1]["close"], 2),
            "contracts": position.contracts,
            "net_credit_per_contract": round(position.net_credit, 2),
            "max_risk_per_contract": round(position.short_strike - position.long_strike - position.net_credit, 2),
            "realized_pnl": round(position.realized, 2),
            "close_reason": "end_of_window",
            "dte_at_close": (position.exp_date - bars[-1]["date"]).days,
        }
        trades.append(trade)
        round_trips.append(dict(trade))

    # ---------------------------------------------------------------- benchmark
    bench_shares = INITIAL_CASH / bars[0]["close"]
    bench_eq = [INITIAL_CASH / bars[0]["close"] * b["close"] for b in bars]

    # ---------------------------------------------------------------- metrics
    def metrics(eq_curve, init):
        arr = eq_curve
        total = arr[-1] / init - 1
        days = len(arr)
        ann = (1 + total) ** (252.0 / max(days, 1)) - 1
        peak = -1e18
        max_dd = 0.0
        for v in arr:
            peak = max(peak, v)
            dd = v / peak - 1
            max_dd = min(max_dd, dd)
        rets = [(arr[i] / arr[i - 1] - 1) for i in range(1, len(arr))]
        mean = sum(rets) / len(rets) if rets else 0.0
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1) if len(rets) > 1 else 0.0
        std = math.sqrt(var) if len(rets) > 1 else 0.0
        sharpe = (mean / std) * math.sqrt(252) if std > 0 else 0.0
        return {"total_return": total, "ann_return": ann, "max_drawdown": max_dd,
                "sharpe": sharpe, "final_equity": arr[-1]}

    strat = metrics([e["equity"] for e in equity_curve], INITIAL_CASH)
    bench_metrics = metrics(bench_eq, INITIAL_CASH)

    wins = [t for t in trades if t["realized_pnl"] > 0]
    losses = [t for t in trades if t["realized_pnl"] < 0]
    gross_win = sum(t["realized_pnl"] for t in wins)
    gross_loss = abs(sum(t["realized_pnl"] for t in losses))
    win_rate = len(wins) / len(trades) if trades else 0.0
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

    first_trade = trades[0] if trades else None
    last_trade = trades[-1] if trades else None

    # ---------------------------------------------------------------- artifacts
    with open(os.path.join(base, "trades.csv"), "w", newline="") as f:
        cols = list(trades[0].keys()) if trades else ["none"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for t in trades:
            w.writerow(t)

    with open(os.path.join(base, "round_trips.csv"), "w", newline="") as f:
        cols = list(round_trips[0].keys()) if round_trips else ["none"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for t in round_trips:
            w.writerow(t)

    with open(os.path.join(base, "equity.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "equity"])
        for row in equity_curve:
            w.writerow([row["date"], row["equity"]])

    with open(os.path.join(base, "benchmark_equity.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "equity"])
        for b, v in zip(bars, bench_eq):
            w.writerow([b["date"].isoformat(), round(v, 2)])

    summary = {
        "strategy": "spy_bull_put_credit_spread",
        "initial_cash": INITIAL_CASH,
        "window": {"start": bars[0]["date"].isoformat(), "end": bars[-1]["date"].isoformat()},
        "metrics": {k: round(v, 4) if isinstance(v, float) else v for k, v in strat.items()},
        "benchmark_metrics": {k: round(v, 4) if isinstance(v, float) else v for k, v in bench_metrics.items()},
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 4),
        "profit_factor": (round(profit_factor, 4) if math.isfinite(profit_factor) else None),
        "gross_profit": round(gross_win, 2),
        "gross_loss": round(gross_loss, 2),
        "first_trade": first_trade,
        "last_trade": last_trade,
        "warnings": warnings,
    }
    with open(os.path.join(base, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)

    # fingerprint
    import hashlib
    h = hashlib.sha256(open(os.path.join(base, "raw", "bars_SPY.json"), "rb").read()).hexdigest()
    fp = {"feed": "iex", "adjustment": "raw", "raw_files": ["raw/bars_SPY.json", "raw/calendar.json"],
          "bars_sha256": h, "bars_count": n,
          "window": {"start": bars[0]["date"].isoformat(), "end": bars[-1]["date"].isoformat()}}
    with open(os.path.join(base, "data_fingerprint.json"), "w") as f:
        json.dump(fp, f, indent=2)

    with open(os.path.join(base, "warnings.json"), "w") as f:
        json.dump(warnings, f, indent=2)

    # ---------------------------------------------------------------- report
    def pct(x):
        return f"{x*100:.2f}%"

    lines = []
    lines.append("# Backtest Report — SPY Bull Put Credit Spread (options-only)\n")
    lines.append("## Performance vs Benchmarks\n")
    lines.append("| | Total Return | Ann. Return | Max Drawdown | Sharpe | Final Equity |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    lines.append(f"| **Strategy** | {pct(strat['total_return'])} | {pct(strat['ann_return'])} | {pct(strat['max_drawdown'])} | {strat['sharpe']:.2f} | ${strat['final_equity']:,.2f} |")
    lines.append(f"| SPY buy-hold | {pct(bench_metrics['total_return'])} | {pct(bench_metrics['ann_return'])} | {pct(bench_metrics['max_drawdown'])} | {bench_metrics['sharpe']:.2f} | ${bench_metrics['final_equity']:,.2f} |\n")
    lines.append("## Strategy configuration")
    lines.append("- Underlying: SPY (daily bars, feed=iex, raw adjustment)")
    lines.append(f"- Window: {bars[0]['date']} → {bars[-1]['date']}")
    lines.append(f"- Regime filter: close > SMA({SMA_WINDOW})")
    lines.append(f"- Short put: ~{SHORT_PCT*100:.0f}% of spot ({TARGET_DTE}-day DTE target)")
    lines.append(f"- Protective long put: ~{LONG_PCT*100:.0f}% of spot")
    lines.append(f"- Fill model: next_open; friction {FRICTION_BPS:.0f} bps/side")
    lines.append(f"- Options pricing: Black-Scholes, r={RATE}, q={DIVY}, IV={IV}")
    lines.append(f"- Sizing: risk {RISK_PER_TRADE_PCT*100:.0f}% of equity per trade, up to {MAX_POSITIONS} position(s)\n")
    lines.append("## Results")
    if trades:
        lines.append(f"- Trades: {len(trades)} · Wins: {len(wins)} ({pct(win_rate)}) · Losses: {len(losses)}")
        lines.append(f"- Profit factor: {profit_factor if math.isfinite(profit_factor) else 'n/a (no losses)'}")
        lines.append(f"- Gross profit: ${gross_win:,.2f} · Gross loss: ${gross_loss:,.2f}")
        ft = first_trade
        lt = last_trade
        lines.append(f"- First trade: {ft['open_date']} → {ft['close_date']} ({ft['short_symbol']}), P&L ${ft['realized_pnl']:,.2f}")
        lines.append(f"- Last trade:  {lt['open_date']} → {lt['close_date']} ({lt['short_symbol']}), P&L ${lt['realized_pnl']:,.2f}")
    else:
        lines.append("- No trades occurred.")
    lines.append("")
    lines.append("## Most important caveats")
    for w in warnings:
        lines.append(f"- {w}")
    lines.append("")
    lines.append("> **Important disclosure**: This backtest is a hypothetical historical simulation and does not represent actual trading performance. Backtested results do not guarantee future results. Results depend on market-data quality, data feed selection, corporate-action handling, fees, slippage, liquidity, taxes, execution assumptions, and implementation details. Option premiums are MODEL-ESTIMATED (Black-Scholes), not historical market fills. This material is for research and educational purposes only and is not investment advice. All investments involve risk and may lose value. Review Alpaca's disclosures at https://alpaca.markets/disclosures.")

    with open(os.path.join(base, "report.md"), "w") as f:
        f.write("\n".join(lines))
    print("Summary:", json.dumps(summary["metrics"], indent=2))
    print("Benchmark:", json.dumps(bench_metrics, indent=2))
    print(f"Trades: {len(trades)}, Win rate: {pct(win_rate)}, Profit factor: {profit_factor}")

if __name__ == "__main__":
    main()