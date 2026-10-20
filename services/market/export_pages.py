"""Publish public-source snapshots; never export the private app database or imports."""
import argparse
import asyncio
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from calculations import iso
from macro import MacroService
from models import Snapshot
from providers import PublicProviders
from research import ResearchStore, briefing, encode
from service import MarketService, Store

REPORT_FIELDS = ('id', 'kind', 'reportDate', 'createdAt', 'snapshotId', 'digest', 'title', 'previousId')
MAX_REPORTS = 30


def read_reports(directory):
    result = []
    if not directory.exists():
        return result
    for path in directory.glob('*.json'):
        if not re.fullmatch(r'[a-f0-9]{24}', path.stem) or path.stat().st_size > 6_000_000:
            raise ValueError('Invalid cached public report')
        report = json.loads(path.read_text())
        snapshot = report['snapshot']
        Snapshot.model_validate(snapshot)
        if (snapshot['mode'] != 'connected' or report['id'] != path.stem
            or report['snapshotId'] != snapshot['id']
            or report['digest'] != hashlib.sha256(encode(snapshot).encode()).hexdigest()):
            raise ValueError('Cached public report failed integrity validation')
        result.append(report)
    return sorted(result, key=lambda r: (r['createdAt'], r['id']), reverse=True)


def publish(snapshot, public_dir, now=None):
    """Stage a complete generation before replacing the previous publication."""
    now = now or datetime.now(timezone.utc)
    Snapshot.model_validate(snapshot)
    if snapshot['mode'] != 'connected' or len(snapshot['assets']) != 12:
        raise ValueError('Only twelve-reference source snapshots can be published')
    # Public release never includes manually imported data or acknowledgment state.
    if snapshot.get('context', {}).get('flows') != {'series': [], 'recordCount': 0}:
        raise ValueError('Public export does not accept private flow imports')
    reports = read_reports(public_dir / 'reports')
    digest = hashlib.sha256(encode(snapshot).encode()).hexdigest()
    for existing in reports:
        if existing['snapshotId'] == snapshot['id']:
            if existing['digest'] != digest:
                raise ValueError('Snapshot identity already has different content')
            return existing
    previous = reports[0] if reports else None
    day = now.astimezone(ZoneInfo('Asia/Singapore')).date().isoformat()
    identity = hashlib.sha256(('daily:' + snapshot['id']).encode()).hexdigest()[:24]
    report = dict(id=identity, kind='daily', reportDate=day, createdAt=iso(now),
                  snapshotId=snapshot['id'], digest=digest,
                  title='Market evidence · ' + day, previousId=previous['id'] if previous else None,
                  snapshot=snapshot, briefing=briefing(snapshot, previous['snapshot'] if previous else None))
    reports = [report] + [r for r in reports if r['id'] != identity]
    reports = reports[:MAX_REPORTS]
    staging = public_dir.with_name(public_dir.name + '-staging')
    if staging.exists():
        shutil.rmtree(staging)
    (staging / 'reports').mkdir(parents=True)
    (staging / 'snapshot.json').write_text(encode(snapshot))
    (staging / 'reports.json').write_text(encode({'reports': [{k: r[k] for k in REPORT_FIELDS} for r in reports]}))
    for item in reports:
        (staging / 'reports' / (item['id'] + '.json')).write_text(encode(item))
    (staging / 'monitor.md').write_text(report['briefing']['monitorMarkdown'])
    (staging / 'publication.json').write_text(encode({
        'generatedAt': snapshot['generatedAt'], 'schedule': '17 0 * * * UTC',
        'expectedCadence': 'Daily around 08:17 Asia/Singapore; GitHub may delay or skip scheduled jobs.',
        'availableAssets': sum(a['price'] is not None for a in snapshot['assets']),
        'freshAssetsAtCapture': sum(a['status'] == 'delayed' for a in snapshot['assets']),
        'reportCount': len(reports), 'retentionLimit': MAX_REPORTS,
    }))
    backup = public_dir.with_name(public_dir.name + '-previous')
    if backup.exists():
        shutil.rmtree(backup)
    if public_dir.exists():
        public_dir.rename(backup)
    try:
        staging.rename(public_dir)
    except Exception:
        if backup.exists():
            backup.rename(public_dir)
        raise
    if backup.exists():
        shutil.rmtree(backup)
    return report


async def collect(state_dir):
    state_dir.mkdir(parents=True, exist_ok=True)
    db = state_dir / 'sources.sqlite3'
    store = Store(db)
    research = ResearchStore(db)
    async with httpx.AsyncClient(headers={'User-Agent': 'AllSeeingEyePublicMonitor/0.1'}, timeout=5,
                                 limits=httpx.Limits(max_connections=16)) as client:
        market, macro = await asyncio.gather(
            MarketService(store, PublicProviders(client)).snapshot(),
            MacroService(db, client).snapshot())
    snapshot = market.model_dump()
    context = {'generatedAt': snapshot['generatedAt'], 'macro': macro,
               'flows': {'series': [], 'recordCount': 0}, 'alerts': []}
    context['alerts'] = research.alerts(snapshot, context)
    for alert in context['alerts']:
        alert['acknowledgedAt'] = None
    snapshot['context'] = context
    # No report database grows in the cache. Public JSON retains at most 30 captures.
    with store.connection() as conn:
        conn.execute('DELETE FROM snapshots WHERE id != ?', (snapshot['id'],))
    publish(snapshot, state_dir / 'public')
    print(json.dumps({'snapshotId': snapshot['id'], 'generatedAt': snapshot['generatedAt'],
                      'available': sum(a['price'] is not None for a in snapshot['assets']),
                      'stale': sum(a['status'] == 'stale' for a in snapshot['assets'])}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=Path('.runtime/pages-state'))
    args = parser.parse_args()
    asyncio.run(collect(args.state_dir))
