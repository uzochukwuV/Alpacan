"""Read-only FastAPI dashboard for the SPY bull put spread paper strategy."""

import os
import json
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

load_dotenv(os.path.abspath(".env"))

BASE_DIR = Path(__file__).resolve().parent.parent
RUN_DIR = BASE_DIR / "run_data"

PARAMS = {
    "strategy": "SPY bull put credit spread (paper)",
    "version": "1.1.0",
    "sma_window": 80,
    "short_otm_pct": 0.95,
    "long_otm_pct": 0.88,
    "target_dte": 30,
    "max_dte_close": 5,
    "take_profit_ratio": 0.50,
    "risk_per_trade_pct": 0.10,
    "max_positions": 1,
    "paper": True,
    "poll_seconds": 300,
}

app = FastAPI(title="SPY Bull Put Spread Dashboard", version="1.1.0")


def _num(v):
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def get_clients():
    from alpaca.trading.client import TradingClient
    from alpaca.data.historical.stock import StockHistoricalDataClient
    from alpaca.data.historical.option import OptionHistoricalDataClient
    key = os.environ.get("APCA_API_KEY_ID")
    secret = os.environ.get("APCA_API_SECRET_KEY")
    if not key or not secret:
        raise HTTPException(status_code=500, detail="Alpaca credentials are not set")
    tclient = TradingClient(key, secret, paper=True)
    sclient = StockHistoricalDataClient(key, secret)
    oclient = OptionHistoricalDataClient(key, secret)
    return tclient, sclient, oclient


def _stock_spot(sclient):
    from alpaca.data.requests import StockLatestQuoteRequest
    try:
        q = sclient.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=["SPY"]))
        r = q["SPY"]
        return {"bid": _num(r.bid_price), "ask": _num(r.ask_price)}
    except Exception:
        return None


def _option_positions(tclient):
    from alpaca.trading.models import AssetClass
    return [p for p in tclient.get_all_positions() if p.asset_class == AssetClass.US_OPTION]


@app.get("/api/health")
def api_health():
    return {"ok": True, "service": "alpacan-dashboard", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/account")
def api_account():
    tclient, sclient, oclient = get_clients()
    acct = tclient.get_account()
    clock = tclient.get_clock()
    return {
        "account_number": acct.account_number,
        "status": str(acct.status),
        "equity": _num(acct.equity),
        "buying_power": _num(acct.buying_power),
        "cash": _num(acct.cash),
        "options_level": acct.options_approved_level,
        "clock": {"is_open": bool(clock.is_open), "next_open": str(clock.next_open) if clock.next_open else None},
        "spy_spot": _stock_spot(sclient),
        "strategy": PARAMS,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/position")
def api_position():
    tclient, sclient, oclient = get_clients()
    positions = _option_positions(tclient)
    if not positions:
        return {"open": False, "legs": [], "total_unrealized_pnl": None}
    legs = []
    for p in positions:
        legs.append({
            "symbol": p.symbol,
            "qty": _num(p.qty),
            "avg_entry_price": _num(p.avg_entry_price),
            "current_price": _num(p.current_price),
            "unrealized_pl": _num(p.unrealized_pl),
            "market_value": _num(p.market_value),
        })
    total = None
    sums = 0.0
    count = 0
    for p in positions:
        v = _num(p.unrealized_pl)
        if v is not None:
            sums += v
            count += 1
    if count > 0:
        total = sums
    return {"open": True, "legs": legs, "total_unrealized_pnl": total}


@app.get("/api/state")
def api_state():
    state = {}
    p = RUN_DIR / "state.json"
    if p.exists():
        try:
            state = json.loads(p.read_text())
        except Exception:
            state = {}
    return {"params": PARAMS, "state": state, "as_of": datetime.now(timezone.utc).isoformat()}


@app.get("/api/executions")
def api_executions(limit: int = 50):
    rows = []
    p = RUN_DIR / "order_log.csv"
    if p.exists():
        try:
            lines = p.read_text().strip().splitlines()
            for ln in reversed(lines):
                parts = ln.split(",")
                if len(parts) >= 3:
                    rows.append({"ts": parts[0], "event": parts[1], "detail": ",".join(parts[2:])})
        except Exception:
            pass
    tclient, sclient, oclient = get_clients()
    try:
        orders = tclient.get_orders()
        for o in orders[:20]:
            ts = str(getattr(o, "submitted_at", ""))
            legs = [l.symbol for l in getattr(o, "legs", [])]
            legs_desc = ";".join(legs)
            rows.append({
                "ts": ts,
                "event": "ORDER",
                "detail": "id=%s status=%s qty=%s avg=%s legs=%s" % (o.id, o.status, o.filled_qty, o.filled_avg_price, legs_desc),
            })
    except Exception:
        pass
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return {"executions": rows[:limit], "as_of": datetime.now(timezone.utc).isoformat()}


@app.get("/api/logs")
def api_logs():
    out = {}
    for name, fn in (("runtime", "runtime.log"), ("keeper", "runtime_keeper.log")):
        p = RUN_DIR / fn
        if p.exists():
            try:
                lines = p.read_text().strip().splitlines()
                out[name] = lines[-40:]
            except Exception:
                out[name] = []
        else:
            out[name] = []
    return {"logs": out}


@app.get("/", response_class=HTMLResponse)
def dashboard():
    p = Path(__file__).resolve().parent / "templates" / "index.html"
    return HTMLResponse(p.read_text())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("webapp.main:app", host="0.0.0.0", port=12000, reload=True)
