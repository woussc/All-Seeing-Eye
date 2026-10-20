"""Public research adapters. Never replace missing observations with generated values."""
import asyncio
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from calculations import iso, session_close

TIMEOUT_SECONDS = 5
NY = ZoneInfo('America/New_York')

def number(value):
    if value is None or isinstance(value,bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError,TypeError):
        return None

def positive(value):
    value = number(value)
    return value if value is not None and value>0 else None

def timestamp(value):
    value = number(value)
    if value is None:
        raise ValueError('Missing observation timestamp')
    return datetime.fromtimestamp(value,timezone.utc)

def parse_yahoo(data, now):
    chart = data['chart']
    if chart.get('error'):
        raise ValueError('Provider reported a chart error')
    result = chart['result'][0]
    meta = result['meta']
    quote_at = timestamp(meta['regularMarketTime'])
    price = positive(meta.get('regularMarketPrice'))
    if not price or quote_at > now+timedelta(seconds=5):
        raise ValueError('Invalid ETF quote or future timestamp')
    quote = result['indicators']['quote'][0]
    adjusted = (result['indicators'].get('adjclose') or [{}])[0].get('adjclose',[])
    bars=[]
    raw=[]
    for i,ts in enumerate(result.get('timestamp',[])):
        day = timestamp(ts).astimezone(NY).date()
        close_at = session_close(day,now.year)
        if close_at is None or close_at >= now:
            continue
        close = positive(quote['close'][i])
        high = positive(quote['high'][i])
        low = positive(quote['low'][i])
        adj = positive(adjusted[i]) if i<len(adjusted) else None
        if not close:
            continue
        raw.append({'time':iso(close_at),'close':close})
        if adj and high and low and low<=close<=high:
            factor = adj / close
            bars.append({'time':iso(close_at),'close':adj,'high':high*factor,'low':low*factor,'volume':number(quote.get('volume',[None]*len(result['timestamp']))[i])})
    bars.sort(key=lambda b:b['time'])
    raw.sort(key=lambda b:b['time'])
    return {'price':price,'asOf':iso(quote_at),'bars':bars,'rawBars':raw,'window':'ETF regular-session last trade; 1D/1W compare raw price with 1/5 prior session raw closes (price return, not total return; splits/distributions can distort changes). Daily history and indicators use provider-adjusted prices: classify completed-session trend using the latest history value, not the raw live quote. Common-close ETF prices are verified completed regular-session raw closing bars, labelled by their NYSE aggregation endpoint, not exact trade ticks. ETF relative volume is unsupported.','providerPublicationAt':None}

def parse_coinbase(stats,candles,now, observed_at=None):
    # stats has no exchange timestamp: the ticker is required for a timestamped last trade.
    if observed_at is None:
        raise ValueError('Timestamped Coinbase ticker required')
    quote_at = datetime.fromisoformat(observed_at.replace('Z','+00:00'))
    if quote_at.tzinfo is None or quote_at>now+timedelta(seconds=5):
        raise ValueError('Invalid crypto observation timestamp')
    price=positive(stats.get('last'))
    if not price:
        raise ValueError('Missing crypto last trade')
    bars=[]
    for candle in candles:
        start=timestamp(candle[0])
        close_at=start+timedelta(days=1)
        low,high,close=positive(candle[1]),positive(candle[2]),positive(candle[4])
        if close_at<=now and low and high and close and low<=close<=high:
            bars.append({'time':iso(close_at),'close':close,'low':low,'high':high,'volume':number(candle[5])})
    bars.sort(key=lambda b:b['time'])
    return {'price':price,'asOf':iso(quote_at),'open24h':positive(stats.get('open')),'bars':bars,'rawBars':bars,'window':'Coinbase latest timestamped trade; 1D is the provider rolling 24h stats window (fetched with ticker, not atomically). 1W is null: exact seven-day trade comparison unavailable. Daily history uses completed UTC candles; no exact US-close crypto observation is available.','providerPublicationAt':None}

class PublicProviders:
    def __init__(self, client):
        self.client=client

    async def json(self,url,params=None):
        response=await self.client.get(url,params=params,timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()

    async def fetch(self,symbol,now):
        if symbol not in ('BTC','ETH'):
            data=await self.json(f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}', {'range':'5y','interval':'1d','events':'div,splits'})
            return parse_yahoo(data,now)
        root=f'https://api.exchange.coinbase.com/products/{symbol}-USD'
        stats,candles,ticker=await asyncio.gather(self.json(root+'/stats'),self.json(root+'/candles',{'granularity':86400}),self.json(root+'/ticker'))
        # Keep price and timestamp from the same ticker response.
        stats['last']=ticker['price']
        return parse_coinbase(stats,candles,now,ticker['time'])
