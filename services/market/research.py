"""Durable local reports and in-dashboard research state. No external delivery."""
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from calculations import calendar, iso, parse_time, pct, previous_close
from monitor_report import render_monitor, JEV_STATUS

MAX_SNAPSHOT_BYTES = 5_000_000
MAX_REPORT_BYTES = 6_000_000

def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)

def utc(now):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('A timezone-aware clock is required')
    return now.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

def evidence(label, value, ref):
    return {'label':label, 'value':value, 'recordId':ref}

def aligned_returns(assets, names, periods=5, *, cutoff=None, generated_at=None):
    """Require every completed NYSE session, ending at the snapshot cutoff.

    Histories contain adjusted daily closes from the price adapter. An
    intersection alone would silently compress missing sessions into a window.
    """
    selected=[assets.get(name) for name in names]
    if not selected or any(a is None or a.get('status') not in ('delayed','live') for a in selected):
        return None
    try:
        if not cutoff or not generated_at or not 1 <= periods <= 260:
            return None
        end=parse_time(cutoff)
        if previous_close(parse_time(generated_at)) != end:
            return None
        cal=calendar(end.year)
        session=end.astimezone(ZoneInfo('America/New_York')).date().isoformat()
        if not cal.is_session(session) or cal.session_close(session).to_pydatetime()!=end:
            return None
        expected=[end]
        for _ in range(periods):
            session=cal.previous_session(session)
            expected.append(cal.session_close(session).to_pydatetime())
        expected=set(expected)
        start=min(expected)
        histories=[]
        for asset in selected:
            points=asset['history']
            if not points or len(points)>5000:
                return None
            history={}
            for point in points:
                at=parse_time(point['time'])
                value=point['value']
                if at in history or isinstance(value,bool) or not math.isfinite(value) or value<=0:
                    return None
                history[at]=value
            if max(history)!=end or {at for at in history if start<=at<=end}!=expected:
                return None
            histories.append(history)
        returns={name:pct(history[end],history[start]) for name,history in zip(names,histories)}
        if any(value is None for value in returns.values()):
            return None
        return returns,iso(start),iso(end)
    except (KeyError,TypeError,ValueError,OverflowError):
        return None

def evaluate(snapshot, context, previous):
    assets={a['symbol']:a for a in snapshot['assets']}
    failed=[a for a in assets.values() if a['status'] in ('stale','unavailable')]
    rules=[dict(ruleId='price-feed-health', title='Price evidence needs attention', severity='data',
        state='active' if failed else 'resolved', detail=f'{len(failed)} of {len(assets)} assets have stale or unavailable observations.',
        evidence=[evidence(a['symbol'],a['status'],snapshot['id']+'#'+a['symbol']) for a in failed])]
    macro=context['macro']['series']
    missing=[s for s in macro if s['status'] in ('stale','unavailable')]
    rules.append(dict(ruleId='macro-feed-health', title='Macro evidence is incomplete', severity='data',
        state='active' if missing or not macro else 'resolved',
        detail=f'{len(missing)} macro series need fresh source evidence.' if macro else 'Macro sources have not supplied observations.',
        evidence=[evidence(s['label'],s['status'],snapshot['id']+'#'+s['id']) for s in missing]))
    breadth=aligned_returns(assets,['SPY','RSP','IWM'],cutoff=snapshot.get('commonCloseAt'),generated_at=snapshot.get('generatedAt'))
    if breadth:
        returns,start,end=breadth
        rsp=returns['RSP']-returns['SPY']
        iwm=returns['IWM']-returns['SPY']
        threshold=0.15 if previous.get('selective-broadening',{}).get('state')=='active' else 0.25
        state='active' if rsp>threshold and iwm<=0 else 'resolved'
        detail=f'RSP minus SPY: {rsp:+.2f} pp; IWM minus SPY: {iwm:+.2f} pp across five aligned completed sessions. Unvalidated research watch: entry >0.25 pp, exit at <=0.15 pp, with IWM minus SPY <=0 pp. This participation proxy is not fund-flow evidence or a trade signal.'
        refs=[evidence('Window',start[:10]+' to '+end[:10],snapshot['id']),
              evidence('RSP − SPY',f'{rsp:+.2f} pp',snapshot['id']+'#RSP'),
              evidence('IWM − SPY',f'{iwm:+.2f} pp',snapshot['id']+'#IWM')]
    else:
        state='suppressed'; detail='Fresh, aligned SPY/RSP/IWM history is required. No analytical conclusion is issued.';refs=[]
    rules.append(dict(ruleId='selective-broadening', title='Equal weight leads without small-cap confirmation', severity='watch', state=state, detail=detail,evidence=refs))
    return rules

def briefing(snapshot, previous=None):
    context=snapshot.get('context') or {'macro':{'series':[]},'flows':{'series':[]},'alerts':[]}
    usable=[a for a in snapshot['assets'] if a['price'] is not None]
    facts=[{'text':f'{len(usable)} of {len(snapshot["assets"])} assets have a price observation. Return windows differ between US sessions and crypto.',
            'evidence':[evidence('Snapshot', snapshot['generatedAt'],snapshot['id'])]}]
    for alert in context['alerts']:
        if alert['state']=='active': facts.append({'text':alert['detail'],'evidence':alert['evidence']})
    for s in context['macro']['series']:
        if s['value'] is not None:
            unit='%' if s['unit']=='percent' else ' bp'
            facts.append({'text':f'{s["label"]}: {s["value"]:.2f}{unit}, observed {s["observedOn"]}; {s["status"]}.',
                'evidence':[evidence(s['label'],str(s['value']),snapshot['id']+'#'+s['id'])]})
    gaps=[]
    if not context['flows']['series']:gaps.append('No sourced weekly flow records have been imported.')
    if any(s['value'] is None for s in context['macro']['series']) or not context['macro']['series']:gaps.append('Some macro observations are unavailable.')
    gaps.extend(['DXY, credit spreads, positioning and event calendars are not connected.',
                 'Historical provider revisions and publication vintages are not complete; this report is not a point-in-time backtest.'])
    gaps.append(JEV_STATUS)
    participation=aligned_returns({a['symbol']:a for a in snapshot['assets']},['SPY','RSP','IWM'],
        cutoff=snapshot.get('commonCloseAt'),generated_at=snapshot.get('generatedAt'))
    return {'title':'Market evidence briefing','facts':facts,'gaps':gaps,
        'monitorMarkdown':render_monitor(snapshot,previous,participation)}

class ResearchStore:
    def __init__(self,path,clock=None):
        self.path=str(path)
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.connection() as conn:
            conn.executescript('''
              CREATE TABLE IF NOT EXISTS desk_alerts (rule_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS alert_events (id INTEGER PRIMARY KEY AUTOINCREMENT,rule_id TEXT NOT NULL,changed_at TEXT NOT NULL,payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY,kind TEXT NOT NULL,report_date TEXT NOT NULL,
                created_at TEXT NOT NULL,snapshot_id TEXT NOT NULL,digest TEXT NOT NULL,payload TEXT NOT NULL,
                UNIQUE(kind,snapshot_id));
              CREATE UNIQUE INDEX IF NOT EXISTS one_daily_report ON reports(report_date) WHERE kind='daily';
              CREATE TRIGGER IF NOT EXISTS reports_no_update BEFORE UPDATE ON reports BEGIN SELECT RAISE(ABORT,'Reports are immutable'); END;
              CREATE TRIGGER IF NOT EXISTS reports_no_delete BEFORE DELETE ON reports BEGIN SELECT RAISE(ABORT,'Reports are immutable'); END;
              CREATE TRIGGER IF NOT EXISTS reports_no_replace BEFORE INSERT ON reports
                WHEN EXISTS (SELECT 1 FROM reports WHERE id=NEW.id OR (kind=NEW.kind AND snapshot_id=NEW.snapshot_id)
                  OR (kind='daily' AND NEW.kind='daily' AND report_date=NEW.report_date))
                BEGIN SELECT RAISE(ABORT,'Reports are immutable'); END;
              CREATE TRIGGER IF NOT EXISTS reports_no_rowid_replace BEFORE INSERT ON reports
                WHEN EXISTS (SELECT 1 FROM reports WHERE rowid=NEW.rowid)
                BEGIN SELECT RAISE(ABORT,'Reports are immutable'); END;
            ''')

    def connection(self):
        return sqlite3.connect(self.path,timeout=5)

    def alerts(self,snapshot,context):
        now=utc(self.clock())
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            previous={r[0]:json.loads(r[1]) for r in conn.execute('SELECT rule_id,payload FROM desk_alerts')}
            output=[]
            for rule in evaluate(snapshot,context,previous):
                old=previous.get(rule['ruleId'])
                changed=not old or old['state']!=rule['state']
                value=dict(rule, id=rule['ruleId'], firstSeen=old['firstSeen'] if old else now,
                    lastSeen=now, changedAt=now if changed else old['changedAt'],
                    acknowledgedAt=None if changed else old['acknowledgedAt'],
                    transitions=(old['transitions'] if old else 0)+int(changed))
                conn.execute('INSERT OR REPLACE INTO desk_alerts VALUES (?,?)',(rule['ruleId'],encode(value)))
                if changed:conn.execute('INSERT INTO alert_events(rule_id,changed_at,payload) VALUES (?,?,?)',(rule['ruleId'],now,encode(dict(value,eventType='transition'))))
                output.append(value)
        return output

    def acknowledge(self,rule_id):
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT payload FROM desk_alerts WHERE rule_id=?',(rule_id,)).fetchone()
            if not row:raise KeyError('Alert not found')
            data=json.loads(row[0])
            if data['acknowledgedAt'] is None:
                data['acknowledgedAt']=utc(self.clock())
                conn.execute('UPDATE desk_alerts SET payload=? WHERE rule_id=?',(encode(data),rule_id))
                conn.execute('INSERT INTO alert_events(rule_id,changed_at,payload) VALUES (?,?,?)',(rule_id,data['acknowledgedAt'],encode(dict(data,eventType='acknowledgment'))))
            return data

    def save_report(self,snapshot,kind='manual'):
        if snapshot['mode']!='connected':raise ValueError('Server reports require a connected source snapshot')
        if kind not in ('manual','daily'):raise ValueError('Unknown report kind')
        now=self.clock(); utc(now)
        day=now.astimezone(ZoneInfo('Asia/Singapore')).date().isoformat()
        encoded_snapshot=encode(snapshot).encode()
        if len(encoded_snapshot)>MAX_SNAPSHOT_BYTES:
            raise ValueError('Snapshot exceeds the report size limit')
        digest=hashlib.sha256(encoded_snapshot).hexdigest()
        snapshot=json.loads(encoded_snapshot)
        identity=hashlib.sha256((kind+':'+snapshot['id']).encode()).hexdigest()[:24]
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            collisions=conn.execute('SELECT digest FROM reports WHERE snapshot_id=?',(snapshot['id'],)).fetchall()
            if any(row[0]!=digest for row in collisions):
                raise ValueError('Snapshot ID already has different content')
            existing=conn.execute('SELECT payload FROM reports WHERE kind=? AND snapshot_id=?',(kind,snapshot['id'])).fetchone()
            if not existing and kind=='daily':existing=conn.execute("SELECT payload FROM reports WHERE kind='daily' AND report_date=?",(day,)).fetchone()
            if existing:return json.loads(existing[0])
            prior=conn.execute('SELECT id FROM reports ORDER BY created_at DESC,id DESC LIMIT 1').fetchone()
            previous=self.report(prior[0])['snapshot'] if prior else None
            data=dict(id=identity,kind=kind,reportDate=day,createdAt=utc(now),snapshotId=snapshot['id'],
                digest=digest,title='Market evidence · '+day,previousId=prior[0] if prior else None,
                snapshot=snapshot,briefing=briefing(snapshot,previous))
            payload=encode(data)
            if len(payload.encode())>MAX_REPORT_BYTES:
                raise ValueError('Report exceeds the storage size limit')
            conn.execute('INSERT INTO reports VALUES (?,?,?,?,?,?,?)',(identity,kind,day,utc(now),snapshot['id'],digest,payload))
        return data

    def list_reports(self):
        with self.connection() as conn:
            rows=conn.execute('''SELECT id,kind,report_date,created_at,snapshot_id,digest,
                json_extract(payload,'$.title'),json_extract(payload,'$.previousId')
                FROM reports ORDER BY created_at DESC,id DESC LIMIT 100''').fetchall()
        fields=('id','kind','reportDate','createdAt','snapshotId','digest','title','previousId')
        return [dict(zip(fields,row)) for row in rows]

    def report(self,identity):
        with self.connection() as conn:row=conn.execute('SELECT payload,digest,snapshot_id FROM reports WHERE id=?',(identity,)).fetchone()
        if not row:raise KeyError('Report not found')
        data=json.loads(row[0])
        if (hashlib.sha256(encode(data['snapshot']).encode()).hexdigest()!=row[1]
                or data['digest']!=row[1] or data['snapshot']['id']!=row[2]
                or data['snapshotId']!=row[2] or data['id']!=identity):
            raise ValueError('Stored report failed its integrity check')
        return data
