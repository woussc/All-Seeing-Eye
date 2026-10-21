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

from calculations import iso, parse_time
from macro import MacroService
from models import Snapshot
from providers import PublicProviders
from research import ResearchStore, briefing, encode
from service import MarketService, Store
from governor import GovernedRequests, Policy, QuotaLedger

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


def change_tape(snapshot, previous):
    """Differences in recorded observations, never inferred flows or trade signals."""
    if previous is None:
        return []
    changes = []
    old_assets = {a['symbol']: a for a in previous['assets']}
    for asset in snapshot['assets']:
        old = old_assets.get(asset['symbol'])
        if not old:
            continue
        if asset['status'] != old['status']:
            changes.append({'kind': 'health', 'label': asset['symbol'] + ' source status',
                'before': old['status'], 'after': asset['status'], 'asOf': snapshot['generatedAt']})
        if (old['price'] is not None and asset['price'] is not None and old['price'] != asset['price']
            and asset['asOf'] and old['asOf'] and parse_time(asset['asOf']) >= parse_time(old['asOf'])):
            changes.append({'kind': 'price', 'label': asset['symbol'] + ' last quote (USD)',
                'before': str(old['price']), 'after': str(asset['price']), 'asOf': asset['asOf']})
    old_macro = {s['id']: s for s in (previous.get('context') or {}).get('macro', {}).get('series', [])}
    for series in (snapshot.get('context') or {}).get('macro', {}).get('series', []):
        old = old_macro.get(series['id'])
        if old and series['value'] is not None and (series['value'], series['observedOn']) != (old['value'], old['observedOn']):
            change_label = 'revised observation' if series['observedOn'] == old['observedOn'] else 'new observation'
            changes.append({'kind': 'macro', 'label': series['label'] + ' · ' + series['unit'] + ' · ' + change_label,
                'before': str(old['value']) + ' / ' + str(old['observedOn']),
                'after': str(series['value']) + ' / ' + str(series['observedOn']), 'asOf': snapshot['generatedAt']})
    return changes[:30]


def publish(snapshot, public_dir, now=None, quota=None):
    """Stage a complete generation before replacing the previous publication."""
    now = now or datetime.now(timezone.utc)
    Snapshot.model_validate(snapshot)
    if snapshot['mode'] != 'connected' or len(snapshot['assets']) != 12:
        raise ValueError('Only twelve-reference source snapshots can be published')
    # Public release never includes manually imported data or acknowledgment state.
    if (snapshot.get('context') or {}).get('flows') != {'series': [], 'recordCount': 0}:
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
    publication = {
        'version': 2, 'cadenceSeconds': 86400, 'overdueAfterSeconds': 129600,
        'generatedAt': snapshot['generatedAt'], 'schedule': '17 0 * * * UTC',
        'expectedCadence': 'Daily around 08:17 Asia/Singapore; GitHub may delay or skip scheduled jobs.',
        'availableAssets': sum(a['price'] is not None for a in snapshot['assets']),
        'freshAssetsAtCapture': sum(a['status'] == 'delayed' for a in snapshot['assets']),
        'reportCount': len(reports), 'retentionLimit': MAX_REPORTS,
        'quota': quota, 'changes': change_tape(snapshot, previous['snapshot'] if previous else None),
    }
    snapshot_bytes = encode(snapshot).encode()
    publication_bytes = encode(publication).encode()
    generation = hashlib.sha256(snapshot_bytes + publication_bytes).hexdigest()
    folder = staging / 'generations' / generation
    folder.mkdir(parents=True)
    (folder / 'snapshot.json').write_bytes(snapshot_bytes)
    (folder / 'publication.json').write_bytes(publication_bytes)
    (staging / 'publication.json').write_bytes(publication_bytes)
    manifest = {'version': 1, 'generation': generation, 'generatedAt': snapshot['generatedAt'],
        'snapshotPath': f'generations/{generation}/snapshot.json',
        'snapshotSha256': hashlib.sha256(snapshot_bytes).hexdigest(),
        'publicationPath': f'generations/{generation}/publication.json',
        'publicationSha256': hashlib.sha256(publication_bytes).hexdigest(), 'pollAfterSeconds': 60}
    old_manifest = public_dir / 'feed.json'
    if old_manifest.exists():
        old_generation = json.loads(old_manifest.read_text())['generation']
        if not re.fullmatch('[a-f0-9]{64}', old_generation):
            raise ValueError('Invalid prior generation')
        if old_generation != generation:
            shutil.copytree(public_dir / 'generations' / old_generation, staging / 'generations' / old_generation)
    (staging / 'feed.json').write_text(encode(manifest))
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


async def collect(state_dir, policy_path=None, ledger_path=None):
    state_dir.mkdir(parents=True, exist_ok=True)
    db = state_dir / 'sources.sqlite3'
    store = Store(db)
    research = ResearchStore(db)
    policy = Policy.load(policy_path or Path(__file__).resolve().parents[2] / 'config/source-policies.json')
    ledger = QuotaLedger(ledger_path or state_dir.parent / 'pages-quota.sqlite3', policy)
    async with httpx.AsyncClient(headers={'User-Agent': 'AllSeeingEyePublicMonitor/0.1'}, timeout=5,
                                 limits=httpx.Limits(max_connections=16),
                                 event_hooks=GovernedRequests(ledger).hooks) as client:
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
    publish(snapshot, state_dir / 'public', quota=ledger.status())
    print(json.dumps({'snapshotId': snapshot['id'], 'generatedAt': snapshot['generatedAt'],
                      'available': sum(a['price'] is not None for a in snapshot['assets']),
                      'stale': sum(a['status'] == 'stale' for a in snapshot['assets'])}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=Path('.runtime/pages-state'))
    parser.add_argument('--policy', type=Path)
    parser.add_argument('--ledger', type=Path)
    parser.add_argument('--initialize-ledger', action='store_true', help='Create a new ledger only; never enables sources')
    args = parser.parse_args()
    if args.initialize_ledger:
        policy = Policy.load(args.policy or Path(__file__).resolve().parents[2] / 'config/source-policies.json')
        QuotaLedger(args.ledger or args.state_dir.parent / 'pages-quota.sqlite3', policy).initialize()
        print('Quota ledger initialized; source policies remain unchanged.')
    else:
        asyncio.run(collect(args.state_dir, args.policy, args.ledger))
