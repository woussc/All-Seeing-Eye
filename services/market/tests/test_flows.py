from copy import deepcopy
from datetime import datetime, timezone, timedelta
import sqlite3

import pytest
from flows import FlowStore

NOW = datetime(2026, 10, 19, 10, tzinfo=timezone.utc)

def record(end='2026-10-16', **overrides):
    day = datetime.fromisoformat(end).date()
    start = day - timedelta(days=6)
    value = dict(seriesId='fixture-fund', label='Test fixture', category='named-etf',
        scope='One test fund', periodStart=start.isoformat(), periodEnd=end,
        publishedAt=(day+timedelta(days=1)).isoformat()+'T09:00:00Z',
        netFlowUsd=100.0, beginningAumUsd=1000.0, aumAsOf=(start-timedelta(days=1)).isoformat(),
        expectedConstituents=['TEST'], coveredConstituents=['TEST'], complete=True,
        revision=1, sourceName='Fixture only', sourceUrl='https://example.com/test',
        rightsNote='Synthetic unit test fixture; never import into the running service.')
    return value | overrides

def batch(*records):
    return {'schemaVersion':1, 'records':list(records)}

@pytest.fixture
def store(tmp_path):
    return FlowStore(tmp_path/'research.db', lambda:NOW)

def test_preview_is_atomic_and_does_not_persist(store):
    result=store.import_records(batch(record()))
    assert result['inserted']==1 and not result['committed']
    assert store.snapshot()=={'series':[], 'recordCount':0}

def test_zero_is_a_complete_observation_and_reimports_are_idempotent(store):
    data=batch(record(netFlowUsd=0.0))
    assert store.import_records(data, True)['inserted']==1
    assert store.import_records(data, True)['unchanged']==1
    first=store.snapshot()['series'][0]['windows'][0]
    assert first['netFlowUsd']==0 and first['normalizedPct']==0 and first['status']=='complete'

def test_incomplete_constituents_never_sum_as_zero(store):
    store.import_records(batch(record(complete=False, coveredConstituents=[], netFlowUsd=None)), True)
    result=store.snapshot()['series'][0]['windows'][0]
    assert result['netFlowUsd'] is None and result['coveredPeriods']==0
    with pytest.raises(ValueError):
        store.import_records(batch(record(coveredConstituents=[])))

def test_four_weeks_use_beginning_aum_not_sum_of_denominators(store):
    values=[record((NOW.date()-timedelta(days=3+7*i)).isoformat()) for i in range(4)]
    store.import_records(batch(*values), True)
    window=store.snapshot()['series'][0]['windows'][1]
    assert window['netFlowUsd']==400 and window['normalizedPct']==40
    assert window['periodStart']=='2026-09-19' and window['coveredPeriods']==4
    assert store.snapshot()['series'][0]['windows'][2]['netFlowUsd'] is None

def test_gap_withholds_total_even_when_four_rows_exist(store):
    store.import_records(batch(*(record(d) for d in ['2026-10-16','2026-10-09','2026-09-25','2026-09-18'])), True)
    result=store.snapshot()['series'][0]['windows'][1]
    assert result['coveredPeriods']==3 and result['netFlowUsd'] is None

def test_basket_change_blocks_cross_period_aggregation(store):
    values=[record((NOW.date()-timedelta(days=3+7*i)).isoformat()) for i in range(4)]
    values[0].update(expectedConstituents=['NEW'], coveredConstituents=['NEW'])
    store.import_records(batch(*values), True)
    assert store.snapshot()['series'][0]['windows'][1]['netFlowUsd'] is None

def test_revisions_are_immutable_and_preserved(store):
    store.import_records(batch(record()), True)
    with pytest.raises(ValueError, match='higher revision'):
        store.import_records(batch(record(netFlowUsd=999.0)), True)
    store.import_records(batch(record(revision=2, netFlowUsd=150.0)), True)
    snap=store.snapshot()
    assert snap['recordCount']==2 and snap['series'][0]['revision']==2
    assert snap['series'][0]['windows'][0]['netFlowUsd']==150

def test_failed_batch_rolls_back_earlier_rows(store):
    with pytest.raises(ValueError, match='overlap'):
        store.import_records(batch(record(), record('2026-10-15')), True)
    assert store.snapshot()['recordCount']==0

def test_missing_aum_withholds_normalization_only(store):
    store.import_records(batch(record(beginningAumUsd=None, aumAsOf=None)), True)
    window=store.snapshot()['series'][0]['windows'][0]
    assert window['netFlowUsd']==100 and window['normalizedPct'] is None

@pytest.mark.parametrize('changes', [
    {'beginningAumUsd':0.0}, {'aumAsOf':'2026-10-10'}, {'netFlowUsd':float('nan')},
    {'publishedAt':'2027-01-01T00:00:00Z'}, {'publishedAt':'2026-10-17T00:00:00'},
    {'sourceUrl':'javascript:alert(1)'}, {'sourceUrl':'https://user:pass@example.com'},
    {'periodStart':'2026-10-01'}, {'expectedConstituents':['TEST','TEST']},
])
def test_invalid_evidence_rejected(store, changes):
    with pytest.raises(ValueError): store.import_records(batch(record(**changes)), True)
    assert store.snapshot()['recordCount']==0

def test_old_complete_period_stays_visible_as_stale(store):
    store.import_records(batch(record('2026-09-25')), True)
    result=store.snapshot()['series'][0]['windows'][0]
    assert result['status']=='stale' and result['netFlowUsd']==100

def test_source_change_requires_new_series(store):
    store.import_records(batch(record()), True)
    with pytest.raises(ValueError, match='new seriesId'):
        store.import_records(batch(record('2026-10-09', sourceName='Other vendor')), True)

def test_store_survives_reopen(store):
    store.import_records(batch(record()), True)
    restored=FlowStore(store.path, lambda:NOW)
    assert restored.snapshot()==store.snapshot()

@pytest.mark.parametrize('changes',[{'netFlowUsd':1e100},{'beginningAumUsd':1e100},{'beginningAumUsd':1e-100}])
def test_corrupt_exponents_are_rejected(store,changes):
    with pytest.raises(ValueError):store.import_records(batch(record(**changes)),True)
    assert store.snapshot()['recordCount']==0

def test_derived_overflow_from_legacy_payload_is_withheld():
    from flows import window
    row=record(netFlowUsd=1e308,beginningAumUsd=1e-300) | {'id':'legacy'}
    result=window([row],1,NOW)
    assert result['netFlowUsd']==1e308 and result['normalizedPct'] is None
    records=[record((NOW.date()-timedelta(days=3+7*i)).isoformat(),netFlowUsd=1e308) | {'id':str(i)} for i in range(4)]
    records.sort(key=lambda r:r['periodEnd'])
    result=window(records,4,NOW)
    assert result['netFlowUsd'] is None and 'Nonfinite' in result['reason']

def test_series_limit_is_atomic(store):
    store.import_records(batch(*(record(seriesId=f'fund-{i}') for i in range(100))),True)
    with pytest.raises(ValueError,match='100 distinct'):
        store.import_records(batch(record(seriesId='fund-extra')),True)
    assert len(store.snapshot()['series'])==100

def test_history_is_bounded_but_all_revisions_count(store):
    records=[record((NOW.date()-timedelta(days=3+7*i)).isoformat()) for i in range(30)]
    records.append(record('2026-10-16',revision=2,publishedAt='2026-10-18T09:00:00Z',netFlowUsd=120.0))
    store.import_records(batch(*records),True)
    snap=store.snapshot()
    assert snap['recordCount']==31
    assert len(snap['series'][0]['windows'][2]['recordIds'])==13
    assert snap['series'][0]['windows'][2]['netFlowUsd']==1320

@pytest.mark.parametrize('reverse',[True,False])
def test_revision_publication_order_is_checked_in_both_import_orders(store,reverse):
    older=record(publishedAt='2026-10-18T09:00:00Z')
    newer=record(revision=2,publishedAt='2026-10-17T09:00:00Z')
    values=[newer,older] if reverse else [older,newer]
    with pytest.raises(ValueError,match='publication times'):
        store.import_records(batch(*values),True)
    assert store.snapshot()['recordCount']==0

def test_fractional_publication_and_ingestion_times_sort_correctly(tmp_path):
    clock=[NOW.replace(microsecond=0)]
    store=FlowStore(tmp_path/'fraction.db',lambda:clock[0])
    store.import_records(batch(record(publishedAt='2026-10-17T09:00:00Z')),True)
    clock[0]=NOW.replace(microsecond=100000)
    store.import_records(batch(record(revision=2,publishedAt='2026-10-17T09:00:00.100000Z')),True)
    assert store.snapshot()['series'][0]['revision']==2
    clock[0]=NOW.replace(microsecond=50000)
    assert store.snapshot()['series'][0]['revision']==1

def test_conflicting_batch_metadata_is_atomic(store):
    with pytest.raises(ValueError,match='metadata'):
        store.import_records(batch(record(),record('2026-10-09',label='Other label')),True)
    assert store.snapshot()['recordCount']==0
