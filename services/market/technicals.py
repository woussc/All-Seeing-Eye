"""Completed-bar trend evidence. These descriptive rules have no proven trading edge."""
import math

from calculations import contiguous_bars, ema, indicators, pct


def confirmed_structure(bars):
    """Strict pivots need two completed bars on each side; ties are not pivots."""
    window = bars[-126:]
    highs, lows = [], []
    if len(window) >= 20:
        for i in range(2, len(window)-2):
            bar = window[i]
            neighbours = window[i-2:i] + window[i+1:i+3]
            if all(bar['high'] > other['high'] for other in neighbours):
                highs.append({'time':bar['time'], 'value':bar['high'], 'confirmedAt':window[i+2]['time']})
            if all(bar['low'] < other['low'] for other in neighbours):
                lows.append({'time':bar['time'], 'value':bar['low'], 'confirmedAt':window[i+2]['time']})
    state = 'unavailable'
    if len(window) >= 20 and len({b['high'] for b in window}) == len({b['low'] for b in window}) == 1:
        state = 'flat'
    elif len(highs) >= 2 and len(lows) >= 2:
        state = ('higher-highs-higher-lows' if highs[-1]['value'] > highs[-2]['value'] and lows[-1]['value'] > lows[-2]['value']
                 else 'lower-highs-lower-lows' if highs[-1]['value'] < highs[-2]['value'] and lows[-1]['value'] < lows[-2]['value']
                 else 'mixed')
    return {'state':state, 'highs':highs[-2:], 'lows':lows[-2:], 'lookbackBars':len(window), 'confirmationBars':2}


def classify(close, values, slopes, structure):
    levels = [values.get(f'ema{p}') for p in (20,50,200)]
    adx = values.get('adx14')
    if any(v is None for v in levels + slopes + [adx]) or structure == 'unavailable':
        return 'unclear', 'EMA/slope warmup, ADX or confirmed structure evidence is incomplete.'
    e20,e50,e200 = levels
    if close > e20 > e50 > e200 and all(s > 0 for s in slopes) and adx >= 20 and structure == 'higher-highs-higher-lows':
        return 'uptrend', 'Upward EMA alignment, all five-bar EMA slopes positive, ADX at least 20 and confirmed higher highs/lows.'
    if close < e20 < e50 < e200 and all(s < 0 for s in slopes) and adx >= 20 and structure == 'lower-highs-lower-lows':
        return 'downtrend', 'Downward EMA alignment, all five-bar EMA slopes negative, ADX at least 20 and confirmed lower highs/lows.'
    if (adx < 20 and structure in ('mixed','flat') and all(abs(s) <= .25 for s in slopes)
            and (max(levels)-min(levels))/close*100 <= 1):
        return 'range', 'ADX below 20, five-bar EMA moves within 0.25%, EMA band within 1% of close and mixed/flat structure.'
    return 'unclear', 'EMA alignment, slopes, ADX and confirmed structure do not agree under the configured rule.'


def technical_evidence(bars, crypto=False, calculated=None):
    bars = contiguous_bars(bars,crypto)
    if not bars:
        return None
    values = [b['close'] for b in bars]
    base = calculated if calculated is not None else indicators(bars,crypto)
    slopes = [pct(base.get(f'ema{p}'),ema(values[:-5],p)) if len(values) >= 5*p+5 else None for p in (20,50,200)]
    structure = confirmed_structure(bars)
    classification, reason = classify(values[-1],base,slopes,structure['state'])
    atr = base.get('atr14')
    atr_pct = atr/values[-1]*100 if atr is not None and values[-1] > 0 else None
    if atr_pct is not None and not math.isfinite(atr_pct):
        atr_pct = None
    periods = (28,91) if crypto else (20,65)
    returns = [pct(values[-1],values[-n-1]) if len(values) > n else None for n in periods]
    return {'asOf':bars[-1]['time'], 'basis':'venue-daily-bars' if crypto else 'provider-adjusted-daily-bars',
            'windowUnit':'calendar-days' if crypto else 'sessions', 'return4w':returns[0], 'return13w':returns[1],
            'ema20Slope5Pct':slopes[0], 'ema50Slope5Pct':slopes[1], 'ema200Slope5Pct':slopes[2], 'atr14Pct':atr_pct,
            'structure':structure, 'classification':classification, 'reason':reason, 'methodVersion':'trend-v1'}
