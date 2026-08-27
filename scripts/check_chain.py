import os, re
from dotenv import load_dotenv
load_dotenv(os.path.abspath('.env'))
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import OptionChainRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
key = os.environ['APCA_API_KEY_ID']; secret = os.environ['APCA_API_SECRET_KEY']
oc = OptionHistoricalDataClient(key, secret)
sc = StockHistoricalDataClient(key, secret)
spot = sc.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=['SPY']))['SPY']
spot_px = spot.ask_price
print(f"SPY ask: {spot_px}")
req = OptionChainRequest(underlying_symbol='SPY', expiration_date='2026-10-16', type='put')
chain = oc.get_option_chain(req)
snaps = chain
def strike_of(sym):
    m = re.search(r'P(\d{8})$', sym)
    return int(m.group(1))/1000 if m else 0
puts = sorted([(strike_of(s), s, snap) for s, snap in snaps.items()], key=lambda x: x[0])
print('total puts in Oct16 chain:', len(puts))
targets = [round(spot_px*0.95), round(spot_px*0.88)]
for tgt in targets:
    best = min(puts, key=lambda x: abs(x[0]-tgt))
    sym, snap = best[1], best[2]
    lq, lt = snap.latest_quote, snap.latest_trade
    print(f"target strike {tgt}: found {sym} strike={best[0]} bid={lq.bid_price if lq else '-'} ask={lq.ask_price if lq else '-'} last={lt.price if lt else '-'} iv={snap.implied_volatility}")