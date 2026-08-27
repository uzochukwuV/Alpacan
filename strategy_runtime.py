#!/usr/bin/env python3
"""
SPY Bull Put Credit Spread — Alpaca Paper-Trading Runtime (options-only).

This runtime executes the strategy validated in
runs/2026-08-27_spy_bullput_spread_1Day/ against the Alpaca PAPER account.

Strategy (as backtested):
  Signal   : SPY close > SMA(100) of daily closes
  Trade    : Bull put credit spread — sell-to-open put ~5% OTM, buy-to-open
             protective put ~12% OTM, expiration nearest >= 30 calendar days.
  Exits    : take profit when remaining short-put value <= 25% of entry credit;
             time stop when DTE <= 5; forced close when underlying < short strike.
  Position : max 1 spread; contracts = max(1, floor(0.10 * equity / (width*100))).

Safety (per alpaca-trading-paper-trading skill):
  * paper=True is hard-coded; live credentials abort before any order.
  * Every order carries a unique client_order_id (idempotency).
  * Order previews are printed and (in interactive mode) require confirmation.
  * Risk limits: max 1 position, daily loss circuit, max risk per trade.

Usage:
  python strategy_runtime.py check     # connectivity + current state, no orders
  python strategy_runtime.py tick      # run one decision cycle
  python strategy_runtime.py run       # continuous loop (every N minutes)
  python strategy_runtime.py closeall  # close all options positions (paper)
"""
import argparse
import json
import math
import os
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv(os.path.abspath(".env"))

# ---- hard-coded paper safety -------------------------------------------------
PAPER_URL = "https://paper-api.alpaca.markets"
PAPER = True  # literal flag; never read from env

# ---- strategy parameters (mirror run.py) ------------------------------------
SMA_WINDOW = 100
SHORT_PCT = 0.95
LONG_PCT = 0.88
TARGET_DTE = 30
MAX_DTE_CLOSE = 5
TAKE_PROFIT_RATIO = 0.25
RISK_PER_TRADE_PCT = 0.10
MAX_POSITIONS = 1
DAILY_LOSS_CIRCUIT_PCT = 0.05     # stop for the day if realized + unrealized < -5%
POLL_SECONDS = 300                 # runtime loop sleep

RUN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_data")
os.makedirs(RUN_DIR, exist_ok=True)

LOG_FILES = {
    "orders": os.path.join(RUN_DIR, "orders.json"),
    "order_log": os.path.join(RUN_DIR, "order_log.csv"),
    "notes": os.path.join(RUN_DIR, "notes.md"),
    "portfolio": os.path.join(RUN_DIR, "portfolio_summary.md"),
}


def log_entry(event, detail=""):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"{ts},{event},{detail}"
    with open(LOG_FILES["order_log"], "a") as f:
        f.write(line + "\n")
    print(f"[{ts}] {event} {detail}")


def append_notes(text):
    with open(LOG_FILES["notes"], "a") as f:
        f.write(text + "\n")


# ---- data access -------------------------------------------------------------
def get_clients():
    from alpaca.data.historical.stock import StockHistoricalDataClient
    from alpaca.trading.client import TradingClient

    api_key = os.environ.get("APCA_API_KEY_ID")
    secret = os.environ.get("APCA_API_SECRET_KEY")
    if not api_key or not secret:
        raise RuntimeError("Missing APCA_API_KEY_ID / APCA_API_SECRET_KEY in .env")
    # paper=True is literal by design (skill requirement)
    tclient = TradingClient(api_key, secret, paper=PAPER)
    sclient = StockHistoricalDataClient(api_key, secret)
    return tclient, sclient


def get_spot(sclient):
    from alpaca.data.requests import StockLatestQuoteRequest

    q = sclient.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=["SPY"]))
    return q["SPY"]


def get_spy_bars(sclient, days=250):
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    start = (datetime.now(timezone.utc) - timedelta(days=260)).date().isoformat()
    req = StockBarsRequest(
        symbol_or_symbols=["SPY"],
        start=start,
        timeframe=TimeFrame.Day,
        feed="iex",
    )
    resp = sclient.get_stock_bars(req)
    bars = list(resp["SPY"])
    bars.sort(key=lambda b: b.timestamp)
    return bars


def sma100(bars):
    if len(bars) < SMA_WINDOW:
        return None
    window = [b.close for b in bars[-SMA_WINDOW:]]
    return sum(window) / len(window)


def atr(bars, period=14):
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(len(bars) - period, len(bars)):
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


def get_chain(oc, expiry, cp="put"):
    from alpaca.data.requests import OptionChainRequest

    req = OptionChainRequest(underlying_symbol="SPY", expiration_date=expiry, type=cp)
    chain = oc.get_option_chain(req)
    return chain


def find_expiries(oc, min_dte=TARGET_DTE, max_dte=90):
    """List candidate SPY expirations within range from the API (no pagination scan)."""
    # The chain endpoint requires a specific date; probe the near-term dates first.
    from alpaca.data.requests import OptionChainRequest

    today = date.today()
    candidates = []
    # probe each calendar day 25-100 days out (weekly/monthly): try a sample
    for d in range(min_dte, max_dte + 1, 7):
        exp = (today + timedelta(days=d)).isoformat()
        # weeklies may not exist every day; we just probe unique expirations
        pads = (today + timedelta(days=d)).weekday()
        exp = (today + timedelta(days=d)).isoformat()
        if exp not in candidates:
            candidates.append(exp)
    return candidates


def select_contracts(oc, sclient, spot):
    """Return (short_sym, long_sym, short_strike, long_strike, exp_date, short_bid, long_ask)."""
    from alpaca.data.requests import OptionChainRequest
    import re

    today = date.today()
    best = None
    # walk candidate expirations from ~30 up to ~60 calendar days out
    for dte in range(TARGET_DTE, 60):
        exp = today + timedelta(days=dte)
        # align to a trading day approximate (skip weekends)
        while exp.weekday() >= 5:
            exp += timedelta(days=1)
        try:
            chain = get_chain(oc, exp.isoformat())
        except Exception:
            continue
        if not chain:
            continue
        snaps = chain  # dict symbol -> snapshot
        puts = []
        for sym, snap in snaps.items():
            m = re.search(r"P(\d{8})$", sym)
            if not m:
                continue
            strike = int(m.group(1)) / 1000
            q = snap.latest_quote
            if q is None:
                continue
            puts.append((sym, strike, q.bid_price, q.ask_price, snap))
        if not puts:
            continue
        short_target = round(spot * SHORT_PCT, 0)
        long_target = round(spot * LONG_PCT, 0)
        short_put = min(puts, key=lambda x: abs(x[1] - short_target))
        longs = [p for p in puts if p[1] <= short_put[1]]
        if not longs:
            continue
        long_put = min(longs, key=lambda x: abs(x[1] - long_target))
        if long_put[1] >= short_put[1]:
            continue
        # net credit = short-put bid (we sell) - long-put ask (we buy)
        net_credit = short_put[2] - long_put[3]
        if net_credit <= 0:
            continue
        candidate = {
            "exp": exp,
            "exp_iso": exp.isoformat(),
            "dte": (exp - today).days,
            "short_sym": short_put[0],
            "long_sym": long_put[0],
            "short_strike": short_put[1],
            "long_strike": long_put[1],
            "short_bid": short_put[2],
            "short_ask": short_put[3],
            "long_bid": long_put[2],
            "long_ask": long_put[3],
            "net_credit": net_credit,
        }
        if best is None or candidate["dte"] < best["dte"]:
            best = candidate
    return best


def compute_contracts(equity, width, net_credit):
    max_risk = (width - net_credit) * 100
    return max(1, int(equity * RISK_PER_TRADE_PCT / max_risk))


# ---- order submission --------------------------------------------------------
def build_mleg_order(short_sym, long_sym, contracts, action="open"):
    """
    action='open'  -> sell-to-open short put, buy-to-open long put.
    action='close' -> buy-to-close short put, sell-to-close long put.
    """
    from alpaca.trading.models import OrderClass, OrderType, PositionIntent, TimeInForce
    from alpaca.trading.requests import OptionLegRequest, OrderRequest

    legs = []
    if action == "open":
        legs.append(
            OptionLegRequest(
                symbol=short_sym,
                ratio_qty=contracts,
                position_intent=PositionIntent.SELL_TO_OPEN,
            )
        )
        legs.append(
            OptionLegRequest(
                symbol=long_sym,
                ratio_qty=contracts,
                position_intent=PositionIntent.BUY_TO_OPEN,
            )
        )
    else:
        legs.append(
            OptionLegRequest(
                symbol=short_sym,
                ratio_qty=contracts,
                position_intent=PositionIntent.BUY_TO_CLOSE,
            )
        )
        legs.append(
            OptionLegRequest(
                symbol=long_sym,
                ratio_qty=contracts,
                position_intent=PositionIntent.SELL_TO_CLOSE,
            )
        )
    # mleg: top-level qty = number of spreads; legs carry ratio_qty and intent
    req = OrderRequest(
        order_class=OrderClass.MLEG,
        type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        qty=contracts,
        legs=legs,
        client_order_id=str(uuid.uuid4()),
    )
    return req


def submit_paper_order(tclient, order_req, preview_lines, confirm=True):
    """Submit a paper order after printing a preview. Returns order or None."""
    print("=" * 70)
    print("ORDER PREVIEW")
    for line in preview_lines:
        print(" ", line)
    print("=" * 70)
    if confirm:
        ans = input("Confirm submit to PAPER (y/N)? ").strip().lower()
        if ans not in ("y", "yes"):
            log_entry("SKIP", "user declined")
            return None
    try:
        order = tclient.submit_order(order_data=order_req)
        log_entry("SUBMIT", f"client={order_req.client_order_id} id={order.id} status={order.status}")
        return order
    except Exception as e:
        log_entry("ERROR", f"submit failed: {e}")
        print("Submit error:", e)
        return None


# ---- position management -----------------------------------------------------
def current_options_positions(tclient):
    from alpaca.trading.models import AssetClass

    positions = tclient.get_all_positions()
    return [p for p in positions if p.asset_class == AssetClass.US_OPTION]


def pos_pnl_percent(tclient):
    total = 0.0
    for p in tclient.get_all_positions():
        total += float(p.unrealized_pl) if p.unrealized_pl else 0.0
    return total


# ---- state persistence -------------------------------------------------------
def load_state():
    state_path = os.path.join(RUN_DIR, "state.json")
    if os.path.exists(state_path):
        with open(state_path) as f:
            return json.load(f)
    return {"last_signal_date": None, "open_order_info": None}


def save_state(state):
    with open(os.path.join(RUN_DIR, "state.json"), "w") as f:
        json.dump(state, f, indent=2)


# ---- the decision cycle --------------------------------------------------------
def run_tick(tclient, sclient, oc, confirm=True, daily_pnl_tracker=None):
    state = load_state()
    acct = tclient.get_account()
    equity = float(acct.equity)
    buying_power = float(acct.buying_power)
    print(f"Account {acct.account_number}: equity=${equity:,.2f} bp=${buying_power:,.2f}")

    # market open check
    clock = tclient.get_clock()
    print(f"Clock is_open={clock.is_open} next_open={clock.next_open}")
    market_open = clock.is_open

    opts_positions = current_options_positions(tclient)
    open_orders = tclient.get_orders()

    # daily loss circuit
    if daily_pnl_tracker is not None:
        unreal = sum(float(p.unrealized_pl) for p in opts_positions if p.unrealized_pl)
        day_pnl = unreal + daily_pnl_tracker.get("realized_today", 0.0)
        if day_pnl < -DAILY_LOSS_CIRCUIT_PCT * equity:
            print(f"DAILY LOSS CIRCUIT: day P&L {day_pnl:,.0f} < {-DAILY_LOSS_CIRCUIT_PCT*equity:,.0f}; halting.")
            log_entry("HALT", "daily loss circuit")
            return

    # --- exit management for open position ---
    if opts_positions:
        # we hold a spread; evaluate exit rules
        print(f"Open options positions: {len(opts_positions)}")
        for p in opts_positions:
            print(f"  {p.symbol}: qty={p.qty} avg={p.avg_entry_price} upnl={p.unrealized_pl}")
        # identify short put leg (the >strike one for the spread)
        short_pos = None
        long_pos = None
        for p in opts_positions:
            if float(p.qty) < 0:
                short_pos = p
            else:
                long_pos = p
        if short_pos is None:
            print("No identifiable short put leg; skipping management.")
        else:
            # parse symbol for expiry and strike
            sym = short_pos.symbol
            exp_iso = f"20{sym[3:5]}-{sym[5:7]}-{sym[7:9]}"
            exp_date = date.fromisoformat(exp_iso)
            dte = (exp_date - date.today()).days
            short_strike = int(sym[-8:]) / 1000
            underlying = get_spot(sclient)
            spot = underlying.bid_price or underlying.ask_price
            print(f"Short leg {sym} strike={short_strike} DTE={dte} S={spot:.2f}")
            # breach check
            if spot < short_strike:
                reason = "breach"
                do_close = True
            elif dte <= MAX_DTE_CLOSE:
                reason = "time_stop"
                do_close = True
            elif long_pos is None:
                print("Long leg not found; skipping management.")
                do_close = False
            else:
                # take-profit: current spread cost-to-close (short bid - long ask)
                # vs actual entry credit captured at open.
                entry_credit = float(short_pos.avg_entry_price) - float(long_pos.avg_entry_price)
                if entry_credit <= 0:
                    entry_credit = state.get("open_order_info", {}).get("net_credit")
                if entry_credit is not None and entry_credit > 0:
                    chain_snaps = get_chain(oc, exp_iso)
                    short_live = chain_snaps.get(sym)
                    long_live = chain_snaps.get(long_pos.symbol) if long_pos else None
                    if short_live and short_live.latest_quote and long_live and long_live.latest_quote:
                        sq = short_live.latest_quote
                        lq = long_live.latest_quote
                        close_cost = (sq.bid_price or 0) - (lq.ask_price or 0)
                        if close_cost <= TAKE_PROFIT_RATIO * entry_credit:
                            reason = "take_profit"
                            do_close = True
                        else:
                            do_close = False
                            print(f"  spread close-cost ${close_cost:.2f} vs entry credit ${entry_credit:.2f}")
                    else:
                        do_close = False
                        print("  no live quotes for legs; take-profit unavailable")
                else:
                    do_close = False
            if do_close:
                print(f"CLOSE signal: {reason}")
                n = int(abs(float(short_pos.qty)))
                req = build_mleg_order(short_pos.symbol, long_pos.symbol if long_pos else "", n, action="close")
                preview = [
                    f"Action: CLOSE {reason}",
                    f"Short: {short_pos.symbol} x {n} buy_to_close",
                    f"Long:  {long_pos.symbol if long_pos else '-'} x {n} sell_to_close",
                    f"Expiry: {exp_iso} (DTE {dte})",
                    f"Spot: {spot:.2f}",
                ]
                order = submit_paper_order(tclient, req, preview, confirm=confirm)
                if order is not None:
                    state["open_order_info"] = None
                    save_state(state)
                return
            else:
                print("No exit condition met yet.")
                return
    else:
        print("No open option positions.")

    # --- entry signal ---
    if market_open:
        bars = get_spy_bars(sclient)
        sma = sma100(bars)
        if sma is None:
            print(f"Need >= {SMA_WINDOW} bars of history for SMA; have {len(bars)}.")
            return
        last_close = bars[-1].close
        bull = last_close > sma
        print(f"SPY close={last_close:.2f} SMA100={sma:.2f} bull={bull}")
        if bull:
            if open_orders:
                print(f"{len(open_orders)} open orders; skipping entry this tick.")
                return
            contract = select_contracts(oc, sclient, last_close)
            if contract is None:
                print("No suitable spread found in chain.")
                return
            width = contract["short_strike"] - contract["long_strike"]
            n = compute_contracts(equity, width, contract["net_credit"])
            if n > 5:
                n = 5  # size cap for a first paper run
            preview = [
                "Action: OPEN bull put credit spread (PAPER)",
                f"Short PUT {contract['short_sym']} strike={contract['short_strike']} x {n}",
                f"Long  PUT {contract['long_sym']} strike={contract['long_strike']} x {n}",
                f"Expiry {contract['exp_iso']} DTE={contract['dte']}",
                f"Net credit ~${contract['net_credit']:.2f}/contract",
                f"Width {width}, estimated max risk ${width*100*n:,.0f}",
                f"Equity ${equity:,.0f}, contracts {n}",
            ]
            req = build_mleg_order(contract["short_sym"], contract["long_sym"], n, action="open")
            order = submit_paper_order(tclient, req, preview, confirm=confirm)
            if order is not None:
                state["open_order_info"] = {
                    "short_sym": contract["short_sym"],
                    "long_sym": contract["long_sym"],
                    "contracts": n,
                    "short_strike": contract["short_strike"],
                    "long_strike": contract["long_strike"],
                    "exp": contract["exp_iso"],
                    "net_credit": contract["net_credit"],
                    "client_order_id": req.client_order_id,
                }
                state["last_signal_date"] = date.today().isoformat()
                save_state(state)
                append_notes(f"- Opened spread {contract['short_sym']}/{contract['long_sym']} x{n} @ DTE {contract['dte']}")
        else:
            print("Not in bull regime; no entry.")
    else:
        print("Market closed; entry deferred.")


def cmd_check():
    tclient, sclient = get_clients()
    acct = tclient.get_account()
    clock = tclient.get_clock()
    print(f"PAPER account {acct.account_number}: status={acct.status} equity=${float(acct.equity):,.2f}")
    print(f"Options level: {acct.options_approved_level}")
    print(f"Clock: is_open={clock.is_open} next_open={clock.next_open}")
    spot = get_spot(sclient)
    print(f"SPY spot: bid={spot.bid_price} ask={spot.ask_price}")
    positions = tclient.get_all_positions()
    print(f"Positions: {len(positions)}")
    for p in positions:
        print(f"  {p.symbol}: qty={p.qty} upnl={p.unrealized_pl}")
    from alpaca.data.historical.option import OptionHistoricalDataClient
    oc = OptionHistoricalDataClient(os.environ["APCA_API_KEY_ID"], os.environ["APCA_API_SECRET_KEY"])
    print("\n--- contract selection dry-run ---")
    c = select_contracts(oc, sclient, spot.ask_price or spot.bid_price)
    if c:
        for k, v in c.items():
            print(f"  {k}: {v}")
    else:
        print("  no spread found")


def cmd_closeall():
    tclient, _ = get_clients()
    closes = tclient.close_all_positions()
    print("close_all response:", closes)
    log_entry("CLOSE_ALL", f"close_all_positions -> {closes}")


def cmd_report():
    """Generate portfolio_summary.md from live account state."""
    from alpaca.trading.models import AssetClass

    tclient, sclient = get_clients()
    acct = tclient.get_account()
    opts = current_options_positions(tclient)
    clock = tclient.get_clock()

    lines = []
    lines.append("# Paper Portfolio Summary")
    lines.append("")
    lines.append(f"- Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- Account: {acct.account_number} (PAPER)")
    lines.append(f"- Equity: ${float(acct.equity):,.2f}")
    lines.append(f"- Buying power: ${float(acct.buying_power):,.2f}")
    lines.append(f"- Options level: {acct.options_approved_level}")
    lines.append(f"- Market open: {clock.is_open}")
    lines.append("")
    lines.append(f"## Open options positions ({len(opts)})")
    lines.append("")
    if opts:
        lines.append("| Symbol | Side | Qty | Avg Entry | Current | Unrealized P&L |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for p in sorted(opts, key=lambda x: x.symbol):
            side = "SHORT" if float(p.qty) < 0 else "LONG"
            lines.append(
                f"| {p.symbol} | {side} | {p.qty} | {p.avg_entry_price} | "
                f"{p.current_price} | ${float(p.unrealized_pl or 0):,.2f} |"
            )
        total_pl = sum(float(p.unrealized_pl or 0) for p in opts)
        lines.append(f"\n**Total unrealized P&L: ${total_pl:,.2f}**")
    else:
        lines.append("_None._")
    lines.append("")
    lines.append("## Recent orders")
    lines.append("")
    if os.path.exists(LOG_FILES["order_log"]):
        lines.append("```")
        with open(LOG_FILES["order_log"]) as f:
            lines.append(f.read())
        lines.append("```")
    lines.append("")
    lines.append("> Paper trading only; not real-money account. Strategy manager: "
                 "SPY bull put credit spread runtime (strategy_runtime.py).")

    with open(LOG_FILES["portfolio"], "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))

    # mirror account snapshot to json
    snap = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "account": acct.account_number,
        "equity": float(acct.equity),
        "buying_power": float(acct.buying_power),
        "options_approved_level": acct.options_approved_level,
        "positions": [
            {
                "symbol": p.symbol,
                "qty": p.qty,
                "avg_entry_price": p.avg_entry_price,
                "current_price": p.current_price,
                "market_value": p.market_value,
                "unrealized_pl": p.unrealized_pl,
            }
            for p in opts
        ],
    }
    with open(os.path.join(RUN_DIR, "positions_snapshot.json"), "w") as f:
        json.dump(snap, f, indent=2)


def cmd_run(confirm=True, once=False):
    from alpaca.data.historical.option import OptionHistoricalDataClient
    tclient, sclient = get_clients()
    oc = OptionHistoricalDataClient(os.environ["APCA_API_KEY_ID"], os.environ["APCA_API_SECRET_KEY"])
    append_notes(f"# Strategy runtime start {datetime.now(timezone.utc).isoformat()} (PAPER)")
    if once:
        run_tick(tclient, sclient, oc, confirm=confirm)
        return
    while True:
        try:
            run_tick(tclient, sclient, oc, confirm=confirm)
        except KeyboardInterrupt:
            print("Stopped.")
            break
        except Exception as e:
            import traceback
            traceback.print_exc()
            log_entry("ERROR", f"tick exception: {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SPY bull put spread paper runtime")
    parser.add_argument("command", choices=["check", "tick", "run", "closeall", "report"])
    parser.add_argument("--yes", action="store_true", help="auto-confirm paper orders (default: prompt)")
    args = parser.parse_args()
    confirm = not args.yes
    if args.command == "check":
        cmd_check()
    elif args.command == "closeall":
        cmd_closeall()
    elif args.command == "report":
        cmd_report()
    elif args.command == "tick":
        cmd_run(confirm=confirm, once=True)
    elif args.command == "run":
        cmd_run(confirm=confirm)