import os, re
from dotenv import load_dotenv
load_dotenv(os.path.abspath('.env'))
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionChainRequest, OptionBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime
key = os.environ['APCA_API_KEY_ID']; secret = os.environ['APCA_API_SECRET_KEY']
oc = OptionHistoricalDataClient(key, secret)
req = OptionChainRequest(underlying_symbol='SPY', expiration_date='2026-10-16', type='call')
chain = oc.get_option_chain(req)
snaps = chain['snapshots'] if isinstance(chain, dict) and 'snapshots' in chain else chain
def strike_of(sym):
    m = re.search(r'C(\d{8})$', sym)
    return int(m.group(1))/1000 if m else 0
calls = [(sym, snap) for sym, snap in (snaps.items() if isinstance(snaps, dict) else [])]
calls_sorted = sorted(calls, key=lambda x: strike_of(x[0]))
for sym, snap in calls_sorted:
    st = strike_of(sym)
    if 740 <= st <= 800:
        lq = snap.latest_quote
        iv = snap.implied_volatility if snap.implied_volatility is not None else 0
        d = snap.greeks.delta if snap.greeks else None
        print(f"{sym} strike={st} iv={iv:.3f} delta={d} bid={lq.bid_price if lq else None} ask={lq.ask_price if lq else None}")