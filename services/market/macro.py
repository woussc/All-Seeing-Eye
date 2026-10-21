"""Bounded, end-of-day Treasury context; dates never imply publication times."""
import asyncio
import json
import math
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import httpx

BASE_URL = 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml'
FEEDS = {
    'nominal': ('daily_treasury_yield_curve', {'treasury-2y': 'BC_2YEAR', 'treasury-10y': 'BC_10YEAR'}),
    'real': ('daily_treasury_real_yield_curve', {'treasury-real-10y': 'TC_10YEAR'}),
}
LABELS = {
    'treasury-2y': 'Treasury nominal 2-year yield',
    'treasury-10y': 'Treasury nominal 10-year yield',
    'treasury-real-10y': 'Treasury real 10-year yield',
    'treasury-curve': 'Treasury 10-year minus 2-year spread',
}
MAX_BYTES = 2_000_000
MAX_HISTORY = 260


def _iso(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('A timezone-aware clock is required')
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def source_url(kind, year):
    return f'{BASE_URL}?data={FEEDS[kind][0]}&field_tdr_date_value={year}'


def _local_name(tag):
    return tag.rsplit('}', 1)[-1]


def _difference(current, previous, scale=1):
    value = (current - previous) * scale
    return round(value, 10) if math.isfinite(value) else None


def parse_treasury_xml(payload, kind, year, today):
    """Ignore invalid rows; duplicate dates are ambiguous and discarded entirely.

    Missing tenors remain missing. A present invalid tenor invalidates that row.
    Feed/entry ``updated`` is deliberately ignored: its publication semantics
    have not been verified. The feed's NEW_DATE is an observation date only.
    """
    if len(payload) > MAX_BYTES or b'<!DOCTYPE' in payload.upper() or b'<!ENTITY' in payload.upper():
        raise ValueError('Unsafe or oversized Treasury XML')
    root = ET.fromstring(payload)
    if _local_name(root.tag) != 'feed':
        raise ValueError('Expected a Treasury XML feed')
    fields = FEEDS[kind][1]
    result = {series_id: [] for series_id in fields}
    rows, seen, duplicates = {}, set(), set()
    entries = [node for node in root if _local_name(node.tag) == 'entry']
    if len(entries) > 370:
        raise ValueError('Unexpected Treasury feed row count')
    for entry in entries:
        properties = [node for node in entry.iter() if _local_name(node.tag) == 'properties']
        if len(properties) != 1:
            continue
        nodes = list(properties[0])
        values = {_local_name(node.tag): node.text for node in nodes}
        if len(values) != len(nodes):
            continue
        raw_date = values.get('NEW_DATE') or ''
        try:
            # Treasury labels a date using midnight; do not accept arbitrary
            # suffixes or normalize an invalid timestamp into a valid date.
            observed = date.fromisoformat(raw_date[:10])
            if raw_date not in (observed.isoformat(), observed.isoformat() + 'T00:00:00'):
                continue
        except ValueError:
            continue
        if observed.year != year or observed > today:
            continue
        key = observed.isoformat()
        if key in seen:
            duplicates.add(key)
            continue
        seen.add(key)
        row = {}
        try:
            for series_id, field in fields.items():
                raw = values.get(field)
                if raw is None or not raw.strip():
                    continue
                number = float(raw)
                if not math.isfinite(number):
                    raise ValueError('Nonfinite yield')
                row[series_id] = number
        except (ValueError, OverflowError):
            continue
        rows[key] = row
    for observed, row in sorted(rows.items()):
        if observed in duplicates:
            continue
        for series_id, value in row.items():
            result[series_id].append({'date': observed, 'value': value})
    return result


def _error(exc):
    from governor import RequestDenied
    if isinstance(exc, RequestDenied):
        return str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        return f'Treasury HTTP {exc.response.status_code}'
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return 'Treasury request timed out'
    if isinstance(exc, httpx.ProxyError):
        return 'Configured network proxy denied or failed the Treasury connection'
    if isinstance(exc, httpx.RequestError):
        return 'Treasury connection unavailable'
    return 'Treasury response could not be validated'


class MacroService:
    """Caller owns the AsyncClient. Last-good records share only the DB path.

    A six-hour successful cache and one-hour failed-attempt cache bound repeated
    access. Each refresh requests only the current and previous calendar years.
    """
    def __init__(self, store_path, httpx_client, clock=None):
        self.path = str(store_path)
        self.client = httpx_client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = asyncio.Lock()
        Path(store_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS macro_cache (
                kind TEXT PRIMARY KEY, payload TEXT, fetched_at TEXT,
                attempted_at TEXT NOT NULL, error TEXT)''')

    def _connection(self):
        return sqlite3.connect(self.path, timeout=2)

    def _read(self, kind):
        with self._connection() as conn:
            row = conn.execute('SELECT payload,fetched_at,attempted_at,error FROM macro_cache WHERE kind=?', (kind,)).fetchone()
        if row is None:
            return None, None, None, None
        return json.loads(row[0]) if row[0] else None, *row[1:]

    def _save(self, kind, payload, at, error=None):
        with self._connection() as conn:
            if payload is not None:
                conn.execute('INSERT OR REPLACE INTO macro_cache VALUES (?,?,?,?,?)',
                             (kind, json.dumps(payload, allow_nan=False), at, at, None))
            else:
                conn.execute('''INSERT INTO macro_cache(kind,attempted_at,error) VALUES (?,?,?)
                    ON CONFLICT(kind) DO UPDATE SET attempted_at=excluded.attempted_at,error=excluded.error''',
                             (kind, at, error))

    async def _fetch_year(self, kind, year, today):
        async with self.client.stream('GET', source_url(kind, year), timeout=5,
                                      follow_redirects=False, headers={'Accept': 'application/xml'}) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=65_536):
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise ValueError('Oversized Treasury XML')
        return parse_treasury_xml(bytes(content), kind, year, today)

    async def _load(self, kind, now):
        data, fetched, attempted, error = self._read(kind)
        if attempted:
            age = (now - datetime.fromisoformat(attempted.replace('Z', '+00:00'))).total_seconds()
            if 0 <= age < (3600 if error else 21600):
                return data, fetched, attempted, error
        try:
            today = now.astimezone(ZoneInfo('America/New_York')).date()
            async with asyncio.timeout(8):
                # Drain both bounded requests even if one fails; do not leave
                # orphaned fetches running after a failed refresh returns.
                years = await asyncio.gather(*(self._fetch_year(kind, year, today)
                                             for year in (today.year - 1, today.year)), return_exceptions=True)
                for result in years:
                    if isinstance(result, BaseException):
                        raise result
            merged = {key: sorted([point for data in years for point in data[key]],
                                  key=lambda point: point['date'])[-MAX_HISTORY:]
                      for key in FEEDS[kind][1]}
            if not any(merged.values()):
                raise ValueError('Treasury feed contains no usable observations')
            self._save(kind, merged, _iso(self.clock()))
        except Exception as exc:
            self._save(kind, None, _iso(self.clock()), _error(exc))
        return self._read(kind)

    async def snapshot(self):
        async with self.lock:
            now = self.clock()
            _iso(now)
            loaded = await asyncio.gather(*(self._load(kind, now) for kind in FEEDS))
            now = self.clock()
            today = now.astimezone(ZoneInfo('America/New_York')).date()
            series = []
            for kind, (data, fetched, attempted, error) in zip(FEEDS, loaded):
                for key in FEEDS[kind][1]:
                    history = data.get(key, []) if data else []
                    series.append(self._series(key, history, kind, fetched, attempted, error, today))
                if kind == 'nominal':
                    ten = {p['date']: p['value'] for p in data.get('treasury-10y', [])} if data else {}
                    two = {p['date']: p['value'] for p in data.get('treasury-2y', [])} if data else {}
                    curve = [{'date': day, 'value': spread}
                             for day in sorted(ten.keys() & two.keys())
                             if (spread := _difference(ten[day], two[day], 100)) is not None][-MAX_HISTORY:]
                    series.append(self._series('treasury-curve', curve, kind, fetched, attempted, error, today))
            series.sort(key=lambda item: list(LABELS).index(item['id']))
            return {'generatedAt': _iso(now), 'series': series}

    @staticmethod
    def _series(key, history, kind, fetched, attempted, error, today):
        current = history[-1] if history else None
        previous = history[-2] if len(history) > 1 else None
        stale = current and (error or today - date.fromisoformat(current['date']) > timedelta(days=7))
        detail = ('Official Treasury daily end-of-day observations, in percent; no intraday quote. '
                  'Publication time unverified. Date age is coarse: no Treasury holiday/publication calendar is applied; '
                  'observations older than 7 calendar days are stale. Changes compare consecutive available observations, '
                  'not guaranteed consecutive sessions. Six-hour cache; failed refresh retried after one hour.')
        if key == 'treasury-curve':
            detail += ' Spread is (10Y minus 2Y) × 100 basis points, using matching dates only.'
        else:
            detail += ' Yield changes are percentage points.'
        if error:
            detail += f' Last refresh failed: {error}.'
            if current:
                detail += ' Showing stale last-good observations.'
        elif not current:
            detail += ' No usable observations for this series.'
        if attempted:
            detail += f' Last attempt {attempted}.'
        return {
            'id': key, 'label': LABELS[key], 'unit': 'basis-points' if key == 'treasury-curve' else 'percent',
            'value': current['value'] if current else None,
            'previous': previous['value'] if previous else None,
            'change': _difference(current['value'], previous['value']) if current and previous else None,
            'observedOn': current['date'] if current else None,
            'previousOn': previous['date'] if previous else None,
            'publishedAt': None, 'fetchedAt': fetched,
            'status': 'unavailable' if not current else ('stale' if stale else 'delayed'),
            'sourceUrl': source_url(kind, int(current['date'][:4]) if current else today.year),
            'detail': detail, 'history': history,
        }
