import asyncio
from technicals import technical_evidence
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from calculations import indicators, iso, parse_time, pct, previous_close, calendar
from models import Asset, Indicators, Point, Quote, Snapshot, Source

ASSETS = [
 ('SPY','S&P 500','Equities'), ('QQQ','Nasdaq 100','Equities'),
 ('IWM','Russell 2000','Equities'), ('RSP','S&P 500 Equal Weight','Equities'),
 ('IEF','7–10 Year Treasury','Rates & credit'), ('TLT','20+ Year Treasury','Rates & credit'),
 ('LQD','Investment Grade Credit','Rates & credit'), ('HYG','High Yield Credit','Rates & credit'),
 ('GLD','Gold','Gold'), ('BTC','Bitcoin','Crypto'), ('ETH','Ethereum','Crypto'),
 ('SGOV','0–3 Month Treasury','Cash'),
]

class Store:
    """Per-operation connections; last-good data survives failed subsequent fetches."""
    def __init__(self,path):
        self.path=str(path)
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.connection() as conn:
            conn.executescript('''
            CREATE TABLE IF NOT EXISTS observations (
              symbol TEXT PRIMARY KEY, payload TEXT NOT NULL,
              observation_at TEXT NOT NULL, publication_at TEXT,
              ingestion_at TEXT NOT NULL, source_url TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts (
              symbol TEXT PRIMARY KEY, attempted_at TEXT NOT NULL, error TEXT);
            CREATE TABLE IF NOT EXISTS snapshots (
              id TEXT PRIMARY KEY, generated_at TEXT NOT NULL, payload TEXT NOT NULL);
            ''')

    def connection(self):
        return sqlite3.connect(self.path,timeout=2)

    def read(self,symbol):
        with self.connection() as conn:
            good=conn.execute('SELECT payload,ingestion_at FROM observations WHERE symbol=?',(symbol,)).fetchone()
            attempt=conn.execute('SELECT attempted_at,error FROM attempts WHERE symbol=?',(symbol,)).fetchone()
        return (json.loads(good[0]) if good else None,good[1] if good else None,attempt)

    def save(self,symbol,data,now,error=None):
        with self.connection() as conn:
            if data is not None:
                source='https://api.exchange.coinbase.com' if symbol in ('BTC','ETH') else 'https://query1.finance.yahoo.com'
                conn.execute('INSERT OR REPLACE INTO observations VALUES (?,?,?,?,?,?)',(symbol,json.dumps(data,allow_nan=False),data['asOf'],data.get('providerPublicationAt'),iso(now),source))
            conn.execute('INSERT OR REPLACE INTO attempts VALUES (?,?,?)',(symbol,iso(now),error))

    def snapshot(self,snapshot):
        with self.connection() as conn:
            conn.execute('INSERT INTO snapshots VALUES (?,?,?)',(snapshot.id,snapshot.generatedAt,snapshot.model_dump_json()))
            # Bounded development retention; one snapshot per request, latest 100 retained.
            conn.execute('DELETE FROM snapshots WHERE id NOT IN (SELECT id FROM snapshots ORDER BY generated_at DESC LIMIT 100)')


def safe_error(exc):
    # Avoid reflecting provider URLs, credentials or request internals into the UI.
    import httpx
    from governor import RequestDenied
    if isinstance(exc, RequestDenied):
        return str(exc)
    if isinstance(exc,httpx.HTTPStatusError):
        return f'Provider HTTP {exc.response.status_code}'
    if isinstance(exc,(httpx.TimeoutException, TimeoutError)):
        return 'Provider timed out (5s request / 6s total refresh bound)'
    if isinstance(exc,httpx.ProxyError):
        return 'Configured network proxy denied or failed the provider connection'
    if isinstance(exc,httpx.RequestError):
        return 'Provider connection unavailable'
    return f'Provider response could not be validated ({type(exc).__name__})'


def quote_from(data, at=None):
    rows=data['rawBars']
    if at is None:
        price,asof=data['price'],data['asOf']
    else:
        # For ETFs, this view represents a verified completed session aggregate.
        # Its calendar close is the aggregation endpoint, not an exact trade tick.
        match=next((bar for bar in rows if bar['time']==iso(at)),None)
        if match is None:
            return None
        price,asof=match['close'],match['time']
    observed=parse_time(asof)
    # Equity daily bars are labelled by actual NYSE session close. Current trade
    # compares with bars from earlier session dates, avoiding comparison to itself.
    session=observed.astimezone(ZoneInfo('America/New_York')).date()
    cal=calendar(observed.year)
    if not cal.is_session(str(session)):
        return Quote(price=price,asOf=asof)
    by_date={parse_time(r['time']).astimezone(ZoneInfo('America/New_York')).date():r['close'] for r in rows}
    prior=[]
    target=str(session)
    for _ in range(5):
        target=cal.previous_session(target)
        prior.append(by_date.get(target.date()))
    return Quote(price=price,asOf=asof,change1d=pct(price,prior[0]),change1w=pct(price,prior[4]))


def stale_observation(symbol, data, now, cutoff):
    observed=parse_time(data['asOf'])
    if symbol in ('BTC','ETH'):
        return now-observed>timedelta(minutes=2)
    cal=calendar(now.year)
    date=now.astimezone(ZoneInfo('America/New_York')).date()
    threshold=cutoff-timedelta(minutes=20) if cutoff else now-timedelta(days=4)
    if cal.is_session(str(date)):
        opened=cal.session_open(str(date)).to_pydatetime()
        closed=cal.session_close(str(date)).to_pydatetime()
        if opened+timedelta(minutes=20)<now<closed:
            threshold=now-timedelta(minutes=20)
    return observed<threshold

class MarketService:
    def __init__(self,store,provider,clock=None):
        self.store=store
        self.provider=provider
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.lock=asyncio.Lock()

    async def load(self,symbol,now):
        data,ingested,attempt=self.store.read(symbol)
        ttl=30 if symbol in ('BTC','ETH') else 300
        if attempt and 0 <= (now-parse_time(attempt[0])).total_seconds()<ttl:
            return data,ingested,attempt
        try:
            async with asyncio.timeout(6):
                fetched=await self.provider.fetch(symbol,now)
            self.store.save(symbol,fetched,self.clock())
        except Exception as exc:
            self.store.save(symbol,None,self.clock(),safe_error(exc))
        return self.store.read(symbol)

    async def snapshot(self):
        # Serialize refreshes to prevent request-driven provider stampedes.
        async with self.lock:
            now=self.clock()
            loaded=await asyncio.gather(*(self.load(symbol,now) for symbol,_,_ in ASSETS))
            now=self.clock()
            try:
                cutoff=previous_close(now)
                calendar_note='NYSE calendar respects holidays, DST and early closes.'
            except Exception:
                cutoff=None
                calendar_note='Market calendar unavailable; common close withheld.'
            assets=[]
            sources=[]
            for (symbol,name,group),(data,ingested,attempt) in zip(ASSETS,loaded):
                crypto=symbol in ('BTC','ETH')
                source_id=f'coinbase-{symbol.lower()}' if crypto else f'yahoo-{symbol.lower()}'
                error=attempt[1] if attempt else 'Source has not been requested'
                status='unavailable' if data is None else ('stale' if error or stale_observation(symbol,data,now,cutoff) else 'delayed')
                base=dict(symbol=symbol,name=name,group=group,sourceId=source_id,status=status)
                if data is None:
                    asset=Asset(**base,note=f'{error}. No substitute observations are generated.')
                else:
                    age=max(0,(now-parse_time(data['asOf'])).total_seconds())
                    if crypto:
                        quote=Quote(price=data['price'],asOf=data['asOf'],change1d=pct(data['price'],data.get('open24h')))
                        common=None
                    else:
                        quote=quote_from(data)
                        common=quote_from(data,cutoff) if cutoff else None
                    note=f"{data['window']} Observation age {age:.0f}s. Cache ingested {ingested}."
                    if error:
                        note+=f' Last refresh failed: {error}; showing stale last good data.'
                    calculated=indicators(data['bars'],crypto)
                    technicals=technical_evidence(data['bars'],crypto,calculated)
                    expected=now.replace(hour=0,minute=0,second=0,microsecond=0) if crypto else cutoff
                    if technicals and (expected is None or parse_time(technicals['asOf'])!=expected):
                        technicals['classification']='unclear'
                        technicals['reason']='Completed history does not reach the latest expected daily close; trend confirmation is withheld.'
                    asset=Asset(**base,**quote.model_dump(),commonClose=common,note=note,history=[Point(time=b['time'],value=b['close']) for b in data['bars']],indicators=Indicators(**calculated),technicals=technicals)
                assets.append(asset)
                sources.append(Source(id=source_id,name=f'Coinbase Exchange · {symbol}' if crypto else f'Yahoo Finance (unofficial) · {symbol}',status=status,lastObservation=data['asOf'] if data else None,lastFetch=attempt[0] if attempt else None,cadence='30 second cache; public last trade / daily candles' if crypto else '5 minute cache; public regular-session quote / daily adjusted chart',detail=(error or 'Last fetch succeeded')+'. Research-only public access; not a licensed production real-time feed. '+calendar_note,url=f'https://api.exchange.coinbase.com/products/{symbol}-USD' if crypto else f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'))
            result=Snapshot(id=str(uuid.uuid4()),mode='connected',generatedAt=iso(now),commonCloseAt=iso(cutoff) if cutoff else None,assets=assets,sources=sources)
            self.store.snapshot(result)
            return result
