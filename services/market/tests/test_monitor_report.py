import copy

from monitor_report import render_monitor, JEV_STATUS, UNIVERSE
from research import ResearchStore
from test_research import fixture, NOW


def snapshot():
    value = fixture()
    value['assets'][0]['commonClose'] = {'price': 100, 'change1d': 1, 'change1w': 2, 'asOf': value['commonCloseAt']}
    value['assets'].append({'symbol': 'BTC', 'price': 99999, 'asOf': value['generatedAt'], 'status': 'delayed', 'history': []})
    value['context']['macro']['series'][0].update(change=.03, previousOn='2026-10-15', sourceUrl='https://home.treasury.gov')
    return value


def test_report_keeps_all_references_and_separates_crypto_clocks():
    report = render_monitor(snapshot())
    for symbol in UNIVERSE:
        assert f'| {symbol} |' in report
    btc = next(line for line in report.splitlines() if line.startswith('| BTC |'))
    assert '99,999' not in btc and 'No matching close' in btc
    assert '- BTC: 99,999.00 USD' in report
    assert '2026-10-16 16:00 EDT' in report and '2026-10-19 20:00 SGT' in report
    assert 'No sourced flow release is connected' in report and JEV_STATUS in report
    assert 'Baseline: unavailable' in report


def test_nonmatching_bar_cannot_fill_common_close_and_stale_is_visible():
    value = snapshot()
    value['assets'][0]['commonClose']['asOf'] = value['generatedAt']
    line = next(line for line in render_monitor(value).splitlines() if line.startswith('| SPY |'))
    assert 'No matching close' in line and '100.00 USD' not in line
    value['assets'][0]['commonClose']['asOf'] = value['commonCloseAt']
    value['assets'][0]['status'] = 'stale'
    line = next(line for line in render_monitor(value).splitlines() if line.startswith('| SPY |'))
    assert '100.00 USD' in line and '| stale |' in line and '| Unclear |' in line


def test_yield_changes_convert_percentage_points_once():
    value = snapshot()
    value['context']['macro']['series'].append({'id':'curve','label':'Curve','unit':'basis-points','value':38,'change':3,'status':'delayed','observedOn':'2026-10-16','previousOn':'2026-10-15'})
    report = render_monitor(value)
    assert '| 4.00% | +3.00 bp |' in report
    assert '| 38.00 bp | +3.00 bp |' in report


def test_zero_partial_and_scoped_flows_remain_distinct():
    value = snapshot()
    series = {'id':'btc-basket','category':'bitcoin-etfs','label':'BTC basket','scope':'US test basket','sourceUrl':'https://example.com/flows',
              'latestPublishedAt':'2026-10-16T22:00:00Z','importedAt':value['generatedAt'],'constituents':['TEST'],
              'windows':[{'weeks':1,'status':'complete','periodEnd':'2026-10-16','netFlowUsd':0,'normalizedPct':None},
                         {'weeks':4,'status':'incomplete','netFlowUsd':900,'normalizedPct':1},
                         {'weeks':13,'status':'unavailable','netFlowUsd':None,'normalizedPct':None}]}
    value['context']['flows']['series'] = [series,dict(copy.deepcopy(series),id='broad',category='fund-category',label='Broader category')]
    report = render_monitor(value)
    assert '### US spot crypto ETF baskets' in report and '### Broader-scope context' in report
    assert '+0.00 USD | Unavailable | Unavailable | Unavailable' in report
    assert '900.00' not in report
    btc = next(line for line in report.splitlines() if line.startswith('| BTC |'))
    assert 'See scoped flow table' not in btc  # Fund baskets are not direct coin flows.


def test_prior_state_is_used_without_inventing_release_or_vintage_claims(tmp_path):
    store = ResearchStore(tmp_path/'reports.sqlite3',lambda:NOW)
    first = store.save_report(snapshot())
    second = snapshot();second['id'] = 'second'
    current = store.save_report(second)
    assert f'Baseline: snapshot {first["snapshotId"]}' in current['briefing']['monitorMarkdown']
    assert store.report(first['id']) == first
    future = snapshot();future['generatedAt'] = '2026-10-20T00:00:00Z'
    assert 'Baseline: unavailable' in render_monitor(second,future)


def test_ema_alignment_does_not_claim_established_trend():
    value = snapshot()
    value['assets'][0]['indicators'] = {'ema20':99,'ema50':98,'ema200':97,'adx14':30}
    report = render_monitor(value)
    assert 'Unclear (missing trend evidence)' in report
    assert '| Established uptrend |' not in report


def test_new_trend_evidence_is_dated_and_stale_or_later_evidence_is_withheld():
    value=snapshot()
    asset=value['assets'][0]
    asset['technicals']={'asOf':value['commonCloseAt'],'methodVersion':'trend-v1','classification':'uptrend','return4w':4,'return13w':13}
    report=render_monitor(value)
    assert '+4.00% | +13.00% | Uptrend (rule v1)' in report
    asset['status']='stale'
    assert 'Uptrend (rule v1)' not in render_monitor(value)
    asset['status']='delayed';asset['technicals']['asOf']=value['generatedAt']
    report=render_monitor(value)
    assert '+4.00%' not in report and 'Uptrend (rule v1)' not in report
