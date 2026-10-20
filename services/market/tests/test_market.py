"""Synthetic values in this file are test fixtures only; never served by the API."""
import asyncio
import copy
import json
from datetime import datetime,timedelta,timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import app
from calculations import ema, indicators, iso, parse_time, pct, previous_close
from models import Snapshot
from providers import PublicProviders, parse_coinbase, parse_yahoo
from service import ASSETS, MarketService, Store, quote_from

UTC=timezone.utc
NOW=datetime(2026,10,19,12,tzinfo=UTC)

class Provider:
    def __init__(self,data=None,error=None):
        self.data=data
        self.error=error
        self.calls=[]
    async def fetch(self,symbol,now):
        self.calls.append(symbol)
        if self.error:
            raise self.error
        return copy.deepcopy(self.data)


def fixture_record(asof='2026-10-16T20:00:00Z'):
    return {'price':123.123456789,'asOf':asof,'bars':[],'rawBars':[], 'open24h':120,'window':'Test fixture only','providerPublicationAt':None}

@pytest.mark.parametrize('timestamp,expected',[
 ('2026-10-19T12:00:00Z','2026-10-16T20:00:00Z'), # Monday before open
 ('2026-07-06T12:00:00Z','2026-07-02T20:00:00Z'), # July 3 observed holiday
 ('2026-11-27T19:00:00Z','2026-11-27T18:00:00Z'), # Thanksgiving early close
 ('2026-03-09T12:00:00Z','2026-03-06T21:00:00Z'), # DST weekend
 ('2026-03-09T21:00:00Z','2026-03-09T20:00:00Z'),
])
def test_calendar(timestamp,expected):
    assert iso(previous_close(parse_time(timestamp)))==expected


def test_timestamp_precision_and_percent():
    assert parse_time('2026-03-09T16:00:00-04:00')==parse_time('2026-03-09T20:00:00Z')
    with pytest.raises(ValueError):parse_time('2026-03-09T16:00:00')
    with pytest.raises(ValueError):iso(datetime(2026,1,1))
    assert pct(101.25,100)==pytest.approx(1.25)
    assert pct(1,0) is None
    assert pct(float('inf'),100) is None
    assert Snapshot(id='test',mode='connected',generatedAt=iso(NOW),commonCloseAt=None,assets=[],sources=[]).model_dump()['mode']=='connected'

@pytest.mark.asyncio
async def test_all_assets_survive_unavailable_and_negative_cache(tmp_path):
    provider=Provider(error=httpx.ProxyError('Blocked'))
    service=MarketService(Store(tmp_path/'market.sqlite3'),provider,lambda:NOW)
    snapshot=await service.snapshot()
    assert len(snapshot.assets)==12
    assert [a.symbol for a in snapshot.assets]==[a[0] for a in ASSETS]
    assert snapshot.mode=='connected'
    assert snapshot.commonCloseAt=='2026-10-16T20:00:00Z'
    assert all(a.status=='unavailable' and a.price is None and not a.history and a.commonClose is None for a in snapshot.assets)
    assert all(a.indicators.ema20 is None for a in snapshot.assets)
    assert all(s.lastObservation is None and s.lastFetch==iso(NOW) and 'proxy' in s.detail for s in snapshot.sources)
    await service.snapshot()
    assert len(provider.calls)==12 # failures are cached, no request stampede
    assert set(snapshot.model_dump())=={'id','mode','generatedAt','commonCloseAt','assets','sources','context'}
    assert set(snapshot.assets[0].model_dump())=={'symbol','name','group','sourceId','status','note','commonClose','history','indicators','technicals','price','change1d','change1w','asOf'}

@pytest.mark.asyncio
async def test_successful_cache_stale_fallback_persistence(tmp_path):
    clock=[NOW]
    provider=Provider(fixture_record())
    path=tmp_path/'market.sqlite3'
    service=MarketService(Store(path),provider,lambda:clock[0])
    first=await service.snapshot()
    assert first.assets[0].price==123.123456789
    assert first.assets[0].status=='delayed'
    assert first.assets[9].status=='stale' # old crypto tick cannot be live
    await service.snapshot()
    assert len(provider.calls)==12
    clock[0]+=timedelta(minutes=6)
    provider.error=httpx.ReadTimeout('test timeout')
    stale=await service.snapshot()
    assert all(a.status=='stale' and a.price==123.123456789 for a in stale.assets)
    assert 'Last refresh failed' in stale.assets[0].note
    # A new process/store instance retains last-good observations and metadata.
    restored=Store(path)
    data,ingested,attempt=restored.read('SPY')
    assert data['price']==123.123456789
    assert ingested==iso(NOW)
    assert attempt[0]==iso(clock[0]) and 'timed out' in attempt[1]
    with restored.connection() as conn:
        row=conn.execute('SELECT observation_at,publication_at,ingestion_at FROM observations WHERE symbol="SPY"').fetchone()
        assert row==('2026-10-16T20:00:00Z',None,iso(NOW))

@pytest.mark.asyncio
async def test_concurrent_requests_do_not_duplicate_provider_calls(tmp_path):
    provider=Provider(fixture_record())
    service=MarketService(Store(tmp_path/'market.sqlite3'),provider,lambda:NOW)
    await asyncio.gather(*(service.snapshot() for _ in range(4)))
    assert len(provider.calls)==12


def yahoo_fixture():
    # Yahoo uses the regular session opening timestamp for daily chart rows.
    at=[int(parse_time(v).timestamp()) for v in ['2026-10-15T13:30:00Z','2026-10-16T13:30:00Z','2026-10-19T13:30:00Z']]
    return {'chart':{'error':None,'result':[{'meta':{'regularMarketTime':int(parse_time('2026-10-16T19:59:59Z').timestamp()),'regularMarketPrice':110.123456789},'timestamp':at,'indicators':{'quote':[{'close':[100,110,999],'high':[101,111,1000],'low':[99,109,998],'volume':[1000,1100,1]}],'adjclose':[{'adjclose':[99,110,999]}]}}]}}


def test_yahoo_completed_bars_adjustment_and_session_common_close():
    record=parse_yahoo(yahoo_fixture(),NOW)
    assert len(record['bars'])==2
    assert record['bars'][0]['time']=='2026-10-15T20:00:00Z'
    assert record['bars'][0]['close']==99
    assert record['bars'][0]['high']==pytest.approx(99.99)
    assert record['price']==110.123456789
    q=quote_from(record)
    assert q.change1d==pytest.approx(10.123456789)
    common=quote_from(record,parse_time('2026-10-16T20:00:00Z'))
    assert common.price==110 # completed raw session close, not last trade 110.123...
    assert common.change1d==pytest.approx(10)
    record['asOf']='2026-10-16T20:00:00Z'
    assert quote_from(record,parse_time(record['asOf'])).price==110


def test_yahoo_incomplete_adjustments_and_future_quote():
    data=yahoo_fixture()
    data['chart']['result'][0]['indicators'].pop('adjclose')
    record=parse_yahoo(data,NOW)
    assert record['bars']==[]
    assert record['rawBars'] # quote returns supported; adjusted indicators withheld
    data['chart']['result'][0]['meta']['regularMarketTime']=int((NOW+timedelta(days=1)).timestamp())
    with pytest.raises(ValueError,match='future'):parse_yahoo(data,NOW)


def test_coinbase_timestamped_trade_and_completed_candles():
    candles=[[int((NOW-timedelta(days=i)).replace(hour=0).timestamp()),90,110,95,100,150] for i in (0,1,2)]
    record=parse_coinbase({'last':'101.123456789','open':'100'},candles,NOW,'2026-10-19T11:59:59.123456Z')
    assert len(record['bars'])==2
    assert record['price']==101.123456789
    assert record['asOf']=='2026-10-19T11:59:59.123456Z'
    assert [b['time'] for b in record['bars']]==sorted(b['time'] for b in record['bars'])
    with pytest.raises(ValueError,match='Timestamped'):parse_coinbase({'last':'101'},candles,NOW)
    with pytest.raises(ValueError):parse_coinbase({'last':'nan'},candles,NOW,iso(NOW))


def test_indicators_known_sequences_and_warmup():
    assert ema([100]*20,20)==100
    assert ema([1]*19,20) is None
    bars=[{'time':iso(NOW-timedelta(days=1011-i)),'close':float(i),'high':float(i+1),'low':float(i-1),'volume':100.0} for i in range(1,1011)]
    result=indicators(bars,crypto=True)
    assert result['ema20']==pytest.approx(1000.5)
    assert result['ema50']==pytest.approx(985.5)
    assert result['ema200']==pytest.approx(910.5)
    assert result['rsi14']==100
    assert result['atr14']==2
    assert result['adx14']==100
    assert result['relativeVolume']==1
    assert result['volatility']>0
    assert all(v is None for v in indicators([]).values())
    assert indicators([],crypto=False)['relativeVolume'] is None
    assert indicators(bars[-99:],crypto=True)['ema20'] is None
    assert indicators(bars[-149:],crypto=True)['rsi14'] is None

@pytest.mark.asyncio
async def test_real_adapter_requests_timeout_headers_and_ticker_price():
    requests=[]
    def handler(request):
        requests.append(request)
        assert request.extensions['timeout']['read']==5
        if request.url.path.endswith('/stats'):
            return httpx.Response(200,json={'open':'100','last':'999'})
        if request.url.path.endswith('/candles'):
            return httpx.Response(200,json=[])
        return httpx.Response(200,json={'price':'101','time':iso(NOW)})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result=await PublicProviders(client).fetch('BTC',NOW)
    assert result['price']==101 # tick price + timestamp, not untimestamped stats last
    assert len(requests)==3
    assert all(r.url.host=='api.exchange.coinbase.com' for r in requests)


def test_api_health_and_contract_without_external_network(tmp_path,monkeypatch):
    monkeypatch.setenv('MARKET_DB_PATH',str(tmp_path/'api.sqlite3'))
    with TestClient(app) as client:
        app.state.market.provider=Provider(error=httpx.ProxyError('blocked'))
        async def macro_fixture():
            return {'generatedAt':iso(NOW),'series':[]}
        app.state.macro.snapshot=macro_fixture
        assert client.get('/health').json()['status']=='ok'
        response=client.get('/snapshot')
        assert response.status_code==200
        snapshot=Snapshot.model_validate(response.json())
        assert len(snapshot.assets)==12
        assert all(a.status=='unavailable' for a in snapshot.assets)
        assert all(a.commonClose is None for a in snapshot.assets)


def test_missing_session_does_not_change_return_window():
    data=fixture_record('2026-10-16T20:00:00Z')
    data['rawBars']=[{'time':'2026-10-14T20:00:00Z','close':100}]
    assert quote_from(data).change1d is None # October 15 observation is missing
    assert quote_from(data).change1w is None


def test_gap_resets_indicator_warmup_and_flat_market_definition():
    bars=[{'time':iso(NOW-timedelta(days=200-i)),'close':100,'high':101,'low':99,'volume':100} for i in range(200)]
    full=indicators(bars,crypto=True)
    assert full['rsi14']==50 and full['adx14']==0 and full['volatility']==0
    # Removing a recent day leaves only 19 contiguous bars, not 199.
    missing=bars[:180]+bars[181:]
    result=indicators(missing,crypto=True)
    assert all(value is None for value in result.values())

@pytest.mark.asyncio
async def test_malformed_provider_response_is_unavailable(tmp_path):
    class Malformed:
        async def fetch(self,symbol,now):
            raise ValueError('Provider token=do-not-reflect-sensitive-details')
    snapshot=await MarketService(Store(tmp_path/'market.sqlite3'),Malformed(),lambda:NOW).snapshot()
    assert all(a.status=='unavailable' for a in snapshot.assets)
    assert all('token=' not in a.note for a in snapshot.assets)

@pytest.mark.asyncio
async def test_short_crypto_cache_expires_before_equity_cache(tmp_path):
    clock=[NOW]
    provider=Provider(fixture_record(iso(NOW)))
    service=MarketService(Store(tmp_path/'market.sqlite3'),provider,lambda:clock[0])
    await service.snapshot()
    clock[0]+=timedelta(seconds=31)
    await service.snapshot()
    assert len(provider.calls)==14
    assert provider.calls[-2:]==['BTC','ETH']


def test_five_year_etf_history_fits_calendar_bounds():
    data=yahoo_fixture()
    result=data['chart']['result'][0]
    result['timestamp'][0]=int(parse_time('2021-10-18T13:30:00Z').timestamp())
    record=parse_yahoo(data,NOW)
    assert record['bars'][0]['time']=='2021-10-18T20:00:00Z'
    assert len(record['bars'])==2


def test_completed_etf_close_is_available_when_last_trade_precedes_cutoff():
    fixture=yahoo_fixture()
    fixture['chart']['result'][0]['meta']['regularMarketTime']=parse_time('2026-10-16T19:59:59.990Z').timestamp()
    data=parse_yahoo(fixture,NOW)
    assert data['asOf']=='2026-10-16T19:59:59.990000Z'
    quote=quote_from(data,parse_time('2026-10-16T20:00:00Z'))
    assert quote.asOf=='2026-10-16T20:00:00Z'
    assert quote.price==110
    assert quote.change1d==pytest.approx(10)
    assert quote_from(data,parse_time('2026-10-19T20:00:00Z')) is None
