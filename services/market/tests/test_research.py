"""Synthetic research fixtures; no external data or delivery occurs here."""
import copy
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import research
from calculations import calendar, iso
from research import ResearchStore, aligned_returns, encode, evaluate

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)


def session_times(end='2026-10-16', count=6):
    cal = calendar(int(end[:4]))
    session = end
    result = []
    for _ in range(count):
        result.append(iso(cal.session_close(session).to_pydatetime()))
        session = cal.previous_session(session)
    return list(reversed(result))


def fixture(snapshot_id='snapshot-1', rsp_lead=.3, iwm_lead=-.2, times=None):
    times = times or session_times()
    assets = []
    for symbol, final in [('SPY', 101), ('RSP', 101 + rsp_lead), ('IWM', 101 + iwm_lead)]:
        history = [{'time': at, 'value': 100 + (final - 100) * i / (len(times) - 1)} for i, at in enumerate(times)]
        assets.append({'symbol': symbol, 'price': final, 'status': 'delayed', 'asOf': times[-1], 'history': history})
    context = {'macro': {'series': [{'id': 'treasury-10y', 'label': '10Y Treasury', 'status': 'delayed',
                                   'unit': 'percent', 'value': 4, 'observedOn': '2026-10-16'}]},
               'flows': {'series': []}, 'alerts': []}
    return {'id': snapshot_id, 'mode': 'connected', 'generatedAt': iso(NOW),
            'commonCloseAt': session_times()[-1], 'assets': assets, 'sources': [], 'context': context}


def broadening(snapshot, previous=None):
    rules = evaluate(snapshot, snapshot['context'], previous or {})
    return next(rule for rule in rules if rule['ruleId'] == 'selective-broadening')


def alerts_by_id(alerts):
    return {item['ruleId']: item for item in alerts}


def test_exact_completed_five_session_returns_and_evidence():
    snapshot = fixture()
    result = aligned_returns({a['symbol']: a for a in snapshot['assets']}, ['SPY', 'RSP', 'IWM'],
                             cutoff=snapshot['commonCloseAt'], generated_at=snapshot['generatedAt'])
    returns, start, end = result
    assert returns == pytest.approx({'SPY': 1, 'RSP': 1.3, 'IWM': .8})
    assert start == session_times()[0] and end == snapshot['commonCloseAt']
    rule = broadening(snapshot)
    assert rule['state'] == 'active'
    assert 'Unvalidated research watch' in rule['detail']
    assert 'not fund-flow evidence or a trade signal' in rule['detail']
    assert {item['recordId'] for item in rule['evidence']} == {'snapshot-1', 'snapshot-1#RSP', 'snapshot-1#IWM'}


@pytest.mark.parametrize('change', ['stale', 'unavailable', 'demo', 'missing-asset', 'missing-session',
                                    'old-cutoff', 'missing-cutoff', 'future-history', 'invalid-price',
                                    'nonfinite-price', 'duplicate-time', 'naive-time', 'missing-end'])
def test_incomplete_or_unfresh_evidence_suppresses(change):
    snapshot = fixture(times=session_times(count=8))
    asset = snapshot['assets'][1]
    if change in ('stale', 'unavailable', 'demo'):
        asset['status'] = change
    elif change == 'missing-asset':
        snapshot['assets'].pop()
    elif change == 'missing-session':
        # Enough intersecting observations remain; calendar continuity must fail.
        for a in snapshot['assets']:
            del a['history'][-3]
    elif change == 'old-cutoff':
        snapshot['commonCloseAt'] = session_times()[-2]
        for a in snapshot['assets']:
            a['history'].pop()
    elif change == 'missing-cutoff':
        snapshot['commonCloseAt'] = None
    elif change == 'future-history':
        for a in snapshot['assets']:
            a['history'].append({'time': '2026-10-19T20:00:00Z', 'value': 101})
    elif change == 'invalid-price':
        asset['history'][0]['value'] = 0
    elif change == 'nonfinite-price':
        asset['history'][-1]['value'] = float('inf')
    elif change == 'duplicate-time':
        asset['history'].append(copy.deepcopy(asset['history'][-1]))
    elif change == 'naive-time':
        asset['history'][-1]['time'] = '2026-10-16T20:00:00'
    elif change == 'missing-end':
        asset['history'].pop()
    rule = broadening(snapshot)
    assert rule['state'] == 'suppressed'
    assert rule['evidence'] == []


def test_holiday_early_close_and_dst_windows_use_actual_sessions():
    for end, generated in [('2026-11-27', '2026-11-27T19:00:00Z'),
                           ('2026-07-06', '2026-07-06T21:00:00Z'),
                           ('2026-03-09', '2026-03-09T21:00:00Z')]:
        times = session_times(end)
        snapshot = fixture(times=times)
        snapshot['commonCloseAt'] = times[-1]
        snapshot['generatedAt'] = generated
        assert broadening(snapshot)['state'] == 'active'
    assert session_times('2026-11-27')[-1] == '2026-11-27T18:00:00Z'
    assert '2026-07-03T20:00:00Z' not in session_times('2026-07-06')
    assert session_times('2026-03-09')[-2:] == ['2026-03-06T21:00:00Z', '2026-03-09T20:00:00Z']


def test_alert_hysteresis_dedup_acknowledgment_and_restart(tmp_path):
    clock = [NOW]
    path = tmp_path / 'nested' / 'research.sqlite3'
    store = ResearchStore(path, lambda: clock[0])
    snapshot = fixture()
    first = alerts_by_id(store.alerts(snapshot, snapshot['context']))
    active = first['selective-broadening']
    assert active['state'] == 'active' and active['transitions'] == 1
    clock[0] += timedelta(seconds=1)
    ack = store.acknowledge('selective-broadening')
    assert ack['acknowledgedAt'] == iso(clock[0])
    assert ack['transitions'] == 1
    assert store.acknowledge('selective-broadening') == ack
    clock[0] += timedelta(seconds=1)
    store = ResearchStore(path, lambda: clock[0])
    medium = fixture(rsp_lead=.2)
    held = alerts_by_id(store.alerts(medium, medium['context']))['selective-broadening']
    assert held['state'] == 'active'
    assert held['acknowledgedAt'] == ack['acknowledgedAt']
    assert held['firstSeen'] == active['firstSeen'] and held['changedAt'] == active['changedAt']
    assert held['lastSeen'] == iso(clock[0]) and held['transitions'] == 1
    with store.connection() as conn:
        events = [json.loads(row[0]) for row in conn.execute('SELECT payload FROM alert_events')]
    assert len(events) == 4  # Three initial states plus one acknowledgment.
    assert sum(event['eventType'] == 'transition' for event in events) == 3
    assert sum(event['eventType'] == 'acknowledgment' for event in events) == 1
    clock[0] += timedelta(seconds=1)
    low = fixture(rsp_lead=.1)
    resolved = alerts_by_id(store.alerts(low, low['context']))['selective-broadening']
    assert resolved['state'] == 'resolved' and resolved['acknowledgedAt'] is None
    assert resolved['transitions'] == 2 and resolved['changedAt'] == iso(clock[0])
    assert alerts_by_id(store.alerts(medium, medium['context']))['selective-broadening']['state'] == 'resolved'
    again = alerts_by_id(store.alerts(snapshot, snapshot['context']))['selective-broadening']
    assert again['state'] == 'active' and again['transitions'] == 3
    with pytest.raises(KeyError):
        store.acknowledge('unknown-rule')


def test_suppression_resets_ack_and_smallcap_confirmation_resolves(tmp_path):
    store = ResearchStore(tmp_path / 'research.sqlite3', lambda: NOW)
    snapshot = fixture()
    store.alerts(snapshot, snapshot['context'])
    store.acknowledge('selective-broadening')
    snapshot['assets'][0]['status'] = 'stale'
    suppressed = alerts_by_id(store.alerts(snapshot, snapshot['context']))
    assert suppressed['selective-broadening']['state'] == 'suppressed'
    assert suppressed['selective-broadening']['acknowledgedAt'] is None
    assert suppressed['price-feed-health']['state'] == 'active'
    snapshot = fixture(iwm_lead=.1)
    assert broadening(snapshot, {'selective-broadening': {'state': 'active'}})['state'] == 'resolved'
    snapshot['context']['macro']['series'][0]['status'] = 'unavailable'
    health = alerts_by_id(store.alerts(snapshot, snapshot['context']))['macro-feed-health']
    assert health['state'] == 'active'
    assert health['evidence'][0]['recordId'] == 'snapshot-1#treasury-10y'


def test_report_digest_whole_snapshot_idempotency_detachment_and_restart(tmp_path):
    path = tmp_path / 'research.sqlite3'
    clock = [NOW]
    store = ResearchStore(path, lambda: clock[0])
    snapshot = fixture()
    original = copy.deepcopy(snapshot)
    report = store.save_report(snapshot)
    assert report['digest'] == hashlib.sha256(encode(original).encode()).hexdigest()
    assert report['snapshot'] == original and report['snapshotId'] == original['id']
    assert report['previousId'] is None
    snapshot['assets'][0]['price'] = 999
    assert report['snapshot'] == original
    clock[0] += timedelta(hours=1)
    assert store.save_report(original) == report
    restored = ResearchStore(path, lambda: clock[0])
    assert restored.report(report['id']) == report
    assert restored.save_report(original) == report
    report['snapshot']['assets'][0]['price'] = -1
    assert restored.report(report['id'])['snapshot'] == original
    listed = restored.list_reports()
    assert len(listed) == 1 and 'snapshot' not in listed[0] and 'briefing' not in listed[0]
    refs = restored.report(report['id'])['briefing']['facts'][-1]['evidence']
    assert refs[0]['recordId'] == 'snapshot-1#treasury-10y'
    with pytest.raises(KeyError):
        restored.report('missing')


@pytest.mark.parametrize('field', ['assets', 'context', 'sources', 'generatedAt'])
def test_snapshot_id_collision_rejects_any_changed_content_across_kinds(tmp_path, field):
    store = ResearchStore(tmp_path / 'research.sqlite3', lambda: NOW)
    snapshot = fixture()
    store.save_report(snapshot)
    changed = copy.deepcopy(snapshot)
    if field == 'assets':
        changed[field][0]['price'] += 1
    elif field == 'context':
        changed[field]['macro']['series'][0]['value'] += 1
    elif field == 'sources':
        changed[field] = [{'status': 'unavailable'}]
    else:
        changed[field] = iso(NOW + timedelta(seconds=1))
    for kind in ('manual', 'daily'):
        with pytest.raises(ValueError, match='different content'):
            store.save_report(changed, kind)
    assert len(store.list_reports()) == 1


def test_daily_reports_use_singapore_day_and_first_capture_is_immutable(tmp_path):
    clock = [datetime(2026, 10, 19, 15, 59, tzinfo=timezone.utc)]
    store = ResearchStore(tmp_path / 'research.sqlite3', lambda: clock[0])
    first = store.save_report(fixture(), 'daily')
    assert first['reportDate'] == '2026-10-19'
    same_day = store.save_report(fixture('snapshot-2'), 'daily')
    assert same_day == first
    clock[0] += timedelta(minutes=1)
    second = store.save_report(fixture('snapshot-2'), 'daily')
    assert second['reportDate'] == '2026-10-20'
    assert second['previousId'] == first['id']
    assert second['id'] != first['id'] and len(store.list_reports()) == 2
    assert store.save_report(fixture(), 'daily') == first


def test_sql_blocks_update_delete_replace_and_unique_key_replacement(tmp_path):
    store = ResearchStore(tmp_path / 'research.sqlite3', lambda: NOW)
    report = store.save_report(fixture())
    daily = store.save_report(fixture('daily-snapshot'), 'daily')
    with store.connection() as conn:
        row = conn.execute('SELECT * FROM reports WHERE id=?', (report['id'],)).fetchone()
        rowid = conn.execute('SELECT rowid FROM reports WHERE id=?', (report['id'],)).fetchone()[0]
        daily_row = conn.execute('SELECT * FROM reports WHERE id=?', (daily['id'],)).fetchone()
    statements = [('UPDATE reports SET digest=? WHERE id=?', ('forged', report['id'])),
                  ('DELETE FROM reports WHERE id=?', (report['id'],)),
                  ('INSERT OR REPLACE INTO reports VALUES (?,?,?,?,?,?,?)', row),
                  ('INSERT OR REPLACE INTO reports VALUES (?,?,?,?,?,?,?)', ('new-id', *row[1:])),
                  ('INSERT OR REPLACE INTO reports(rowid,id,kind,report_date,created_at,snapshot_id,digest,payload) VALUES (?,?,?,?,?,?,?,?)',
                   (rowid, 'new-rowid-id', row[1], row[2], row[3], 'new-rowid-snapshot', row[5], row[6])),
                  ('INSERT OR REPLACE INTO reports VALUES (?,?,?,?,?,?,?)', ('new-daily-id', daily_row[1], daily_row[2], daily_row[3], 'new-snapshot-id', daily_row[5], daily_row[6]))]
    for sql, params in statements:
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            with store.connection() as conn:
                conn.execute(sql, params)
    assert store.report(report['id']) == report
    assert len(store.list_reports()) == 2


@pytest.mark.parametrize('forge_payload_digest', [False, True])
def test_report_read_detects_digest_mismatch_even_after_trigger_bypass(tmp_path, forge_payload_digest):
    store = ResearchStore(tmp_path / 'research.sqlite3', lambda: NOW)
    report = store.save_report(fixture())
    tampered = copy.deepcopy(report)
    tampered['snapshot']['context']['macro']['series'][0]['value'] = 999
    if forge_payload_digest:
        tampered['digest'] = hashlib.sha256(encode(tampered['snapshot']).encode()).hexdigest()
    with store.connection() as conn:
        conn.execute('DROP TRIGGER reports_no_update')
        conn.execute('UPDATE reports SET payload=? WHERE id=?', (encode(tampered), report['id']))
    with pytest.raises(ValueError, match='integrity'):
        store.report(report['id'])


def test_report_input_bounds_kind_mode_and_timezone(tmp_path, monkeypatch):
    store = ResearchStore(tmp_path / 'research.sqlite3', lambda: NOW)
    snapshot = fixture()
    snapshot['mode'] = 'demo'
    with pytest.raises(ValueError, match='connected'):
        store.save_report(snapshot)
    snapshot['mode'] = 'connected'
    with pytest.raises(ValueError, match='kind'):
        store.save_report(snapshot, 'arbitrary')
    snapshot['assets'][0]['price'] = float('nan')
    with pytest.raises(ValueError):
        store.save_report(snapshot)
    monkeypatch.setattr(research, 'MAX_SNAPSHOT_BYTES', 100)
    with pytest.raises(ValueError, match='size limit'):
        store.save_report(fixture())
    monkeypatch.setattr(research, 'MAX_SNAPSHOT_BYTES', 5_000_000)
    monkeypatch.setattr(research, 'MAX_REPORT_BYTES', 100)
    with pytest.raises(ValueError, match='size limit'):
        store.save_report(fixture())
    assert store.list_reports() == []
    naive = ResearchStore(tmp_path / 'naive.sqlite3', lambda: NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match='timezone-aware'):
        naive.save_report(fixture())
