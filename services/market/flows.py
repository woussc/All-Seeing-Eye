"""Audited weekly flow imports. No synthetic observations or price-derived flows."""
import hashlib
import json
import math
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Literal

Category = Literal['named-etf', 'bitcoin-etfs', 'ethereum-etfs', 'gold-etfs', 'fund-category']
MAX_SERIES = 100
# Broad ceilings accommodate global fund categories while rejecting corrupt
# exponents. A sub-dollar fund AUM is not a useful normalization denominator.
MAX_NET_FLOW_USD = 100_000_000_000_000.0
MAX_AUM_USD = 1_000_000_000_000_000.0
SERIES_METADATA = ('label', 'category', 'scope', 'sourceName', 'sourceUrl', 'rightsNote')

class FlowRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)
    seriesId: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{1,79}$')
    label: str = Field(min_length=1, max_length=120)
    category: Category
    scope: str = Field(min_length=1, max_length=300)
    periodStart: str
    periodEnd: str
    publishedAt: str
    netFlowUsd: float | None = Field(default=None, ge=-MAX_NET_FLOW_USD, le=MAX_NET_FLOW_USD)
    beginningAumUsd: float | None = Field(default=None, ge=1, le=MAX_AUM_USD)
    aumAsOf: str | None = None
    expectedConstituents: list[str] = Field(min_length=1, max_length=100)
    coveredConstituents: list[str] = Field(max_length=100)
    complete: bool
    revision: int = Field(ge=1, le=100000)
    sourceName: str = Field(min_length=1, max_length=120)
    sourceUrl: str = Field(max_length=1000)
    rightsNote: str = Field(min_length=1, max_length=500)

    @field_validator('label', 'scope', 'sourceName', 'rightsNote')
    @classmethod
    def text(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError('Use nonempty text without control characters')
        return value.strip()

    @field_validator('periodStart', 'periodEnd', 'aumAsOf')
    @classmethod
    def day(cls, value):
        if value is None:
            return value
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('Use an ISO calendar date')
        date.fromisoformat(value)
        return value

    @field_validator('publishedAt')
    @classmethod
    def instant(cls, value):
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            raise ValueError('Publication time must include its timezone')
        return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

    @field_validator('sourceUrl')
    @classmethod
    def source(cls, value):
        url = urlsplit(value)
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError('Use a public HTTPS source URL without credentials')
        return value

    @field_validator('expectedConstituents', 'coveredConstituents')
    @classmethod
    def constituents(cls, values):
        if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._ /:-]{0,79}', v) for v in values):
            raise ValueError('Constituent identifiers must be short, nonempty names')
        if len(set(values)) != len(values):
            raise ValueError('Duplicate constituent identifiers')
        return sorted(values)

    @model_validator(mode='after')
    def coherence(self):
        start, end = date.fromisoformat(self.periodStart), date.fromisoformat(self.periodEnd)
        if (end - start).days != 6:
            raise ValueError('Each record must cover exactly seven consecutive calendar dates')
        if datetime.fromisoformat(self.publishedAt.replace('Z', '+00:00')).date() < end:
            raise ValueError('Publication cannot precede the observation period end')
        if not set(self.coveredConstituents) <= set(self.expectedConstituents):
            raise ValueError('Covered constituents must belong to the expected basket')
        if self.complete and (self.coveredConstituents != self.expectedConstituents or self.netFlowUsd is None):
            raise ValueError('A complete period requires a value and every expected constituent')
        if self.beginningAumUsd is not None and self.aumAsOf != (start - timedelta(days=1)).isoformat():
            raise ValueError('Beginning AUM must be dated the day before periodStart')
        if self.beginningAumUsd is None and self.aumAsOf is not None:
            raise ValueError('An AUM date requires an AUM value')
        return self

class FlowImport(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schemaVersion: Literal[1]
    records: list[FlowRecord] = Field(min_length=1, max_length=500)

def instant(now):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('A timezone-aware clock is required')
    return now.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

def _time_key(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S%f')

def _sql_time_key(column):
    # Stored timestamps are normalized UTC, with optional fractional seconds.
    # Padding avoids the lexical inversion between "00Z" and "00.1Z".
    return f"(substr({column},1,19) || substr(replace(substr({column},21),'Z','') || '000000',1,6))"

class FlowStore:
    def __init__(self, path, clock=None):
        self.path = str(path)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS flow_records (
                id TEXT PRIMARY KEY, series_id TEXT NOT NULL, period_end TEXT NOT NULL,
                revision INTEGER NOT NULL, published_at TEXT NOT NULL, imported_at TEXT NOT NULL,
                payload TEXT NOT NULL, UNIQUE(series_id, period_end, revision))''')
            conn.execute('CREATE INDEX IF NOT EXISTS flow_records_window ON flow_records(series_id,period_end,revision DESC)')

    def connection(self):
        return sqlite3.connect(self.path, timeout=5)

    def import_records(self, raw, commit=False):
        batch = FlowImport.model_validate(raw)
        now = self.clock()
        instant(now)
        prepared = []
        keys = set()
        metadata = {}
        for record in batch.records:
            if datetime.fromisoformat(record.publishedAt.replace('Z', '+00:00')) > now:
                raise ValueError('A publication timestamp is in the future')
            if date.fromisoformat(record.periodEnd) >= now.date():
                raise ValueError('Only periods ending before today can be imported')
            key = (record.seriesId, record.periodEnd, record.revision)
            if key in keys:
                raise ValueError('The import contains a duplicate series, period and revision')
            keys.add(key)
            series_metadata = tuple(record.model_dump()[field] for field in SERIES_METADATA)
            if record.seriesId in metadata and metadata[record.seriesId] != series_metadata:
                raise ValueError('Conflicting series metadata in the batch; use a new seriesId')
            metadata[record.seriesId] = series_metadata
            payload = record.model_dump_json()
            identity = hashlib.sha256(payload.encode()).hexdigest()
            prepared.append((identity, record, payload))
        inserted, skipped = 0, 0
        # A transaction makes preview validation and actual import use the same rules.
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE' if commit else 'BEGIN')
            stored_series = {row[0] for row in conn.execute('SELECT DISTINCT series_id FROM flow_records LIMIT ?', (MAX_SERIES + 1,))}
            if len(stored_series | set(metadata)) > MAX_SERIES:
                raise ValueError(f'Flow storage is limited to {MAX_SERIES} distinct series')
            for identity, record, payload in prepared:
                existing = conn.execute('SELECT id FROM flow_records WHERE series_id=? AND period_end=? AND revision=?', (record.seriesId, record.periodEnd, record.revision)).fetchone()
                if existing:
                    if existing[0] != identity:
                        raise ValueError('A stored revision has different content; publish a higher revision')
                    skipped += 1
                    continue
                prior = conn.execute('SELECT payload FROM flow_records WHERE series_id=? ORDER BY revision DESC LIMIT 1', (record.seriesId,)).fetchone()
                if prior:
                    old = json.loads(prior[0])
                    if any(old[k] != record.model_dump()[k] for k in SERIES_METADATA):
                        raise ValueError('Scope/source changes require a new seriesId')
                chronology = conn.execute(f'''SELECT 1 FROM flow_records WHERE series_id=? AND period_end=?
                    AND ((revision<? AND {_sql_time_key('published_at')}>?)
                      OR (revision>? AND {_sql_time_key('published_at')}<?)) LIMIT 1''',
                    (record.seriesId, record.periodEnd, record.revision, _time_key(record.publishedAt),
                     record.revision, _time_key(record.publishedAt))).fetchone()
                if chronology:
                    raise ValueError('Revision publication times must follow revision order')
                # Check overlaps against the latest version of every other period.
                end = date.fromisoformat(record.periodEnd)
                overlap = conn.execute('''SELECT 1 FROM flow_records WHERE series_id=? AND period_end<>?
                    AND period_end BETWEEN ? AND ? LIMIT 1''', (record.seriesId, record.periodEnd,
                    (end-timedelta(days=6)).isoformat(), (end+timedelta(days=6)).isoformat())).fetchone()
                if overlap:
                    raise ValueError('Periods in the same series cannot overlap')
                conn.execute('INSERT INTO flow_records VALUES (?,?,?,?,?,?,?)', (identity, record.seriesId, record.periodEnd, record.revision, record.publishedAt, instant(now), payload))
                inserted += 1
            if not commit:
                conn.rollback()
        return {'committed': commit, 'inserted': inserted, 'unchanged': skipped,
                'series': sorted({r.seriesId for r in batch.records}),
                'incomplete': sum(not r.complete for r in batch.records)}

    def snapshot(self):
        now = self.clock()
        with self.connection() as conn:
            count = conn.execute('SELECT COUNT(*) FROM flow_records').fetchone()[0]
            # Rank metadata before retrieving payloads. Preserve all immutable
            # revisions on disk but deserialize at most 13 periods per series.
            rows = conn.execute(f'''WITH eligible AS (
                  SELECT series_id,period_end,revision FROM flow_records
                    WHERE {_sql_time_key('published_at')}<=? AND {_sql_time_key('imported_at')}<=?),
                ends AS (SELECT series_id,MAX(period_end) AS last_end FROM eligible
                  GROUP BY series_id ORDER BY series_id LIMIT ?),
                latest AS (SELECT e.series_id,e.period_end,MAX(e.revision) AS revision FROM eligible e
                  JOIN ends s ON e.series_id=s.series_id
                  WHERE e.period_end>=date(s.last_end,'-84 days') GROUP BY e.series_id,e.period_end)
                SELECT f.id,f.payload,f.imported_at FROM latest l JOIN flow_records f
                  ON f.series_id=l.series_id AND f.period_end=l.period_end AND f.revision=l.revision
                  ORDER BY f.series_id,f.period_end''', (_time_key(instant(now)), _time_key(instant(now)), MAX_SERIES)).fetchall()
        latest = {}
        for identity, payload, imported in rows:
            record = json.loads(payload)
            latest[(record['seriesId'], record['periodEnd'])] = dict(record, id=identity, importedAt=imported)
        groups = {}
        for record in latest.values():
            groups.setdefault(record['seriesId'], []).append(record)
        series = []
        for sid, records in sorted(groups.items()):
            records.sort(key=lambda r:r['periodEnd'])
            last = records[-1]
            series.append(dict(id=sid, label=last['label'], category=last['category'], scope=last['scope'],
                sourceName=last['sourceName'], sourceUrl=last['sourceUrl'], rightsNote=last['rightsNote'],
                latestPublishedAt=max((r['publishedAt'] for r in records), key=_time_key), importedAt=max((r['importedAt'] for r in records), key=_time_key),
                revision=max(r['revision'] for r in records), constituents=last['expectedConstituents'],
                windows=[window(records, weeks, now) for weeks in (1,4,13)]))
        return {'series': series, 'recordCount': count}

def window(records, weeks, now):
    by_end = {r['periodEnd']: r for r in records}
    end = date.fromisoformat(records[-1]['periodEnd'])
    start = end - timedelta(days=7*weeks-1)
    picked = [by_end.get((end-timedelta(days=7*i)).isoformat()) for i in reversed(range(weeks))]
    present = [r for r in picked if r is not None]
    covered = sum(bool(r and r['complete']) for r in picked)
    constituents = [r['expectedConstituents'] for r in present]
    same_basket = all(c == constituents[0] for c in constituents)
    valid = covered == weeks and same_basket
    stale = (now.date()-end).days > 14
    flow = None
    numeric_failure = False
    if valid:
        try:
            flow = math.fsum(r['netFlowUsd'] for r in present)
            if not math.isfinite(flow):
                raise ValueError('Nonfinite total')
        except (OverflowError, ValueError, TypeError):
            flow, valid, numeric_failure = None, False, True
    denominator = picked[0]['beginningAumUsd'] if valid else None
    normalized = None
    if denominator and flow is not None:
        try:
            value = flow / denominator * 100
            if denominator > 0 and math.isfinite(denominator) and math.isfinite(value):
                normalized = value
        except (OverflowError, ValueError, TypeError, ZeroDivisionError):
            pass
    return dict(weeks=weeks, periodStart=start.isoformat(), periodEnd=end.isoformat(),
        netFlowUsd=flow, normalizedPct=normalized,
        coveredPeriods=covered, expectedPeriods=weeks,
        status='stale' if valid and stale else 'complete' if valid else 'incomplete',
        reason=('Latest period is more than fourteen calendar days old.' if stale else 'Complete consecutive published weeks.') if valid else
            'Nonfinite flow total; totals are withheld.' if numeric_failure else
            'Constituent membership changed; totals are withheld.' if not same_basket else 'Missing or incomplete weekly periods; totals are withheld.',
        recordIds=[r['id'] for r in present])
