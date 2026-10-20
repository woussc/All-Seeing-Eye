"""Pure deterministic calculations. Percent values are in percentage points."""
import math
import statistics
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

UTC = timezone.utc

def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError('Naive timestamps are not accepted')
    return dt.astimezone(UTC).isoformat().replace('+00:00', 'Z')

def parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Naive timestamps are not accepted')
    return dt.astimezone(UTC)

@lru_cache(maxsize=8)
def calendar(year: int):
    # Yahoo backfill requests five years. Keep calendar coverage wider than that
    # history, including when a quote crosses a year boundary.
    return xcals.get_calendar('XNYS', start=f'{year-8}-01-01', end=f'{year+2}-12-31')

def session_close(date, year: int) -> datetime | None:
    cal = calendar(year)
    label = pd.Timestamp(date)
    if not cal.is_session(label):
        return None
    return cal.session_close(label).to_pydatetime()

def previous_close(now: datetime) -> datetime:
    cal = calendar(now.year)
    sessions = cal.sessions_in_range((now-timedelta(days=14)).date(), now.date())
    closes = [cal.session_close(s).to_pydatetime() for s in sessions]
    return max(close for close in closes if close < now)

def pct(value, base):
    if value is None or base is None or base <= 0:
        return None
    result = (value/base - 1) * 100
    return result if math.isfinite(result) else None

def ema(values: list[float], period: int):
    if len(values) < period:
        return None
    result = sum(values[:period]) / period
    alpha = 2 / (period + 1)
    for value in values[period:]:
        result += alpha * (value-result)
    return result

def wilder(values: list[float], period=14):
    if len(values) < period:
        return []
    result = sum(values[:period]) / period
    out = [result]
    for value in values[period:]:
        result = (result*(period-1)+value)/period
        out.append(result)
    return out

def contiguous_bars(bars: list[dict], crypto=False) -> list[dict]:
    # A missing daily observation must not be silently compressed into a session.
    # Use only the contiguous suffix for indicator lookback / warmup.
    start=0
    for i in range(1,len(bars)):
        a,b=parse_time(bars[i-1]['time']),parse_time(bars[i]['time'])
        if crypto:
            contiguous=b-a==timedelta(days=1)
        else:
            cal=calendar(b.year)
            adate=a.astimezone(ZoneInfo('America/New_York')).date()
            bdate=b.astimezone(ZoneInfo('America/New_York')).date()
            contiguous=cal.next_session(str(adate)).date()==bdate
        if not contiguous:
            start=i
    return bars[start:]

def indicators(bars: list[dict], crypto=False) -> dict:
    bars=contiguous_bars(bars,crypto)
    values = [bar['close'] for bar in bars]
    result = {f'ema{p}': ema(values,p) if len(values)>=5*p else None for p in (20,50,200)}
    result.update(rsi14=None, atr14=None, adx14=None, relativeVolume=None, volatility=None)
    # 150 bars allows Wilder's initialization to decay before publication.
    if len(values)>=150:
        changes = [b-a for a,b in zip(values, values[1:])]
        gains = wilder([max(v,0) for v in changes])[-1]
        losses = wilder([max(-v,0) for v in changes])[-1]
        result['rsi14'] = 100 - 100/(1+gains/losses) if losses else (100 if gains else 50)
        tr, plus, minus = [], [], []
        for a,b in zip(bars,bars[1:]):
            tr.append(max(b['high']-b['low'], abs(b['high']-a['close']),abs(b['low']-a['close'])))
            up, down = b['high']-a['high'], a['low']-b['low']
            plus.append(up if up>down and up>0 else 0)
            minus.append(down if down>up and down>0 else 0)
        trs, ups, downs = wilder(tr),wilder(plus),wilder(minus)
        result['atr14'] = trs[-1]
        dx = [100*abs(p-m)/(p+m) if p+m else 0 for p,m in zip(ups,downs)]
        smoothed = wilder(dx)
        result['adx14'] = smoothed[-1] if smoothed else None
    if len(values)>=21:
        returns = [math.log(b/a) for a,b in zip(values[-21:],values[-20:])]
        result['volatility'] = statistics.stdev(returns) * math.sqrt(365 if crypto else 252) * 100
        # Coinbase reports actual crypto units; ETF split-adjusted volume is unsupported.
        volumes = [b['volume'] for b in bars[-21:-1]]
        if crypto and all(v is not None and v>0 for v in volumes) and bars[-1]['volume'] is not None:
            result['relativeVolume'] = bars[-1]['volume'] / statistics.mean(volumes)
    return result
