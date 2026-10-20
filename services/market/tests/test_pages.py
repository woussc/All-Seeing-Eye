"""Public publication tests use synthetic fixtures only."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from export_pages import publish, read_reports
from models import Asset, Snapshot
from research import encode
from service import ASSETS

NOW = datetime(2026, 10, 20, 0, 17, tzinfo=timezone.utc)


def fixture(identity='test-only'):
    return Snapshot(id=identity, mode='connected', generatedAt=NOW.isoformat(),
        commonCloseAt='2026-10-19T20:00:00Z', sources=[],
        assets=[Asset(symbol=s, name=n, group=g, sourceId='test-source', status='unavailable',
                      note='Synthetic fixture; no public observations') for s, n, g in ASSETS],
        context={'generatedAt': NOW.isoformat(), 'macro': {'generatedAt': NOW.isoformat(), 'series': []},
                 'flows': {'series': [], 'recordCount': 0}, 'alerts': []}).model_dump()


def test_publication_files_digest_missing_data_and_report(tmp_path):
    s = fixture()
    public = tmp_path / 'public'
    r = publish(s, public, NOW)
    assert sorted(p.name for p in public.iterdir()) == ['monitor.md', 'publication.json', 'reports', 'reports.json', 'snapshot.json']
    assert r['digest'] == hashlib.sha256(encode(s).encode()).hexdigest()
    assert read_reports(public / 'reports')[0] == r
    assert 'Jev: disabled; connection approval pending' in (public / 'monitor.md').read_text()
    assert all(a['price'] is None for a in json.loads((public / 'snapshot.json').read_text())['assets'])
    assert json.loads((public / 'publication.json').read_text())['availableAssets'] == 0
    assert publish(s, public, NOW + timedelta(hours=1)) == r
    s['assets'][0]['price'] = 999
    with pytest.raises(ValueError, match='different content'):
        publish(s, public, NOW)


def test_retention_previous_links_and_preserved_reports(tmp_path):
    public = tmp_path / 'public'
    prior = None
    for i in range(32):
        r = publish(fixture(str(i)), public, NOW + timedelta(days=i))
        assert r['previousId'] == prior
        prior = r['id']
    reports = read_reports(public / 'reports')
    assert len(reports) == 30
    assert reports[0]['snapshotId'] == '31' and reports[-1]['snapshotId'] == '2'
    assert len(json.loads((public / 'reports.json').read_text())['reports']) == 30


def test_private_imports_and_demo_cannot_be_published(tmp_path):
    public = tmp_path / 'public'
    publish(fixture(), public, NOW)
    before = (public / 'snapshot.json').read_bytes()
    s = fixture('private')
    s['context']['flows'] = {'series': [{'id': 'private-test'}], 'recordCount': 1}
    with pytest.raises(ValueError, match='private flow'):
        publish(s, public, NOW)
    s = fixture('demo'); s['mode'] = 'demo'
    with pytest.raises(ValueError, match='source snapshots'):
        publish(s, public, NOW)
    assert (public / 'snapshot.json').read_bytes() == before


def test_corrupt_previous_archive_does_not_replace_publication(tmp_path):
    public = tmp_path / 'public'
    r = publish(fixture(), public, NOW)
    before = (public / 'snapshot.json').read_bytes()
    r['snapshot']['assets'][0]['price'] = 123
    (public / 'reports' / (r['id'] + '.json')).write_text(encode(r))
    with pytest.raises(ValueError, match='integrity'):
        publish(fixture('new'), public, NOW)
    assert (public / 'snapshot.json').read_bytes() == before
