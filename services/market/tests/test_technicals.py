from datetime import timedelta

import pytest

from calculations import calendar, iso, parse_time
from technicals import classify, confirmed_structure, technical_evidence


def bars(count, crypto=True, flat=False):
    end=parse_time('2026-10-19T00:00:00Z')
    if crypto:
        times=[iso(end-timedelta(days=count-1-i)) for i in range(count)]
    else:
        cal=calendar(2026)
        sessions=cal.sessions_in_range('2020-01-01','2026-10-16')[-count:]
        times=[iso(cal.session_close(s).to_pydatetime()) for s in sessions]
    return [{'time':at,'close':100 if flat else 100+i,'high':101 if flat else 101+i,
             'low':99 if flat else 99+i,'volume':100} for i,at in enumerate(times)]


def wave(direction=1):
    result=bars(30)
    for i,bar in enumerate(result):
        close=100+direction*(2*(i//6)+[0,1,3,2,1,-1][i%6])
        bar.update(close=close,high=close+.5,low=close-.5)
    return result


def test_return_windows_use_exact_sessions_or_calendar_days():
    crypto=technical_evidence(bars(100),True)
    equity=technical_evidence(bars(100,False),False)
    assert crypto['return4w']==pytest.approx((199/171-1)*100)
    assert crypto['return13w']==pytest.approx((199/108-1)*100)
    assert equity['return4w']==pytest.approx((199/179-1)*100)
    assert equity['return13w']==pytest.approx((199/134-1)*100)
    assert crypto['windowUnit']=='calendar-days' and equity['windowUnit']=='sessions'
    assert crypto['asOf']==bars(100)[-1]['time']


def test_gap_resets_longer_returns_and_slope_warmup():
    values=bars(1100)
    missing=values[:-20]+values[-19:]
    result=technical_evidence(missing,True)
    assert result['return4w'] is result['return13w'] is result['ema200Slope5Pct'] is None
    assert result['classification']=='unclear' and result['structure']['lookbackBars']==19
    assert technical_evidence([],True) is None


def test_five_bar_ema_slope_has_warmed_both_endpoints():
    values=bars(105)
    result=technical_evidence(values,True)
    # A linear ramp has EMA20 equal to the last close minus 9.5.
    assert result['ema20Slope5Pct']==pytest.approx((194.5/189.5-1)*100)
    assert technical_evidence(values[:104],True)['ema20Slope5Pct'] is None
    assert result['ema50Slope5Pct'] is None
    long=technical_evidence(bars(1005),True)
    assert long['ema200Slope5Pct']==pytest.approx((1004.5/999.5-1)*100)


def test_atr_percent_uses_the_same_completed_bar_close_and_flat_range():
    result=technical_evidence(bars(1005,flat=True),True)
    assert result['atr14Pct']==pytest.approx(2)
    assert result['return4w']==result['return13w']==0
    assert result['classification']=='range' and result['structure']['state']=='flat'


def test_strict_pivots_wait_for_two_later_closed_bars():
    values=wave()
    before=confirmed_structure(values[:27])
    after=confirmed_structure(values[:29])
    assert before['highs'][-1]['time']==values[20]['time']
    assert after['highs'][-1]['time']==values[26]['time']
    assert after['highs'][-1]['confirmedAt']==values[28]['time']
    assert after['state']=='higher-highs-higher-lows'
    assert confirmed_structure(wave(-1))['state']=='lower-highs-lower-lows'
    values[-1]['high']=9999
    assert all(p['value']!=9999 for p in confirmed_structure(values)['highs'])
    flat=confirmed_structure(bars(50,flat=True))
    assert flat['highs']==flat['lows']==[]  # Equal highs/lows do not create pivots.


@pytest.mark.parametrize('close,levels,slopes,adx,structure,expected',[
    (110,[109,108,107],[1,1,1],25,'higher-highs-higher-lows','uptrend'),
    (110,[109,108,107],[1,-1,1],25,'higher-highs-higher-lows','unclear'),
    (110,[109,108,107],[1,1,1],19,'higher-highs-higher-lows','unclear'),
    (110,[109,108,107],[1,1,1],25,'mixed','unclear'),
    (90,[91,92,93],[-1,-1,-1],25,'lower-highs-lower-lows','downtrend'),
    (100,[100,100,100],[0,0,0],0,'flat','range'),
    (100,[100,100,100],[0,0,None],0,'flat','unclear'),
    (100,[100,100,100],[0,0,0],0,'unavailable','unclear'),
])
def test_all_trend_evidence_must_agree(close,levels,slopes,adx,structure,expected):
    values=dict(zip(['ema20','ema50','ema200'],levels),adx14=adx)
    assert classify(close,values,slopes,structure)[0]==expected
