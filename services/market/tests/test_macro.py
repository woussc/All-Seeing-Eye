"""Synthetic Treasury XML fixtures only; these values are never served live."""
import asyncio
import sqlite3
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree as ET

import httpx
import pytest

from macro import MAX_BYTES, MacroService, parse_treasury_xml

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)

def test_official_feed_path_matches_treasury_published_links():
    from macro import source_url
    assert source_url('nominal',2026)=='https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value=2026'
    assert 'data=daily_treasury_real_yield_curve' in source_url('real',2026)


def feed(rows, updated='2099-01-01T12:34:56Z'):
    entries = []
    for day, values in rows:
        properties = f'<d:NEW_DATE>{day}</d:NEW_DATE>'
        for field, value in values.items():
            properties += f'<d:{field}>{value}</d:{field}>'
        entries.append(f'<entry><updated>{updated}</updated><content><m:properties>{properties}</m:properties></content></entry>')
    return (f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices" '
            f'xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"><updated>{updated}</updated>'
            + ''.join(entries) + '</feed>').encode()


def yields(two='4.0', ten='4.5'):
    return {'BC_2YEAR': two, 'BC_10YEAR': ten}


def responses(request, nominal=None, real=None):
    is_real = request.url.params['data'] == 'daily_treasury_real_yield_curve'
    rows = real if is_real else nominal
    if rows is None:
        rows = [('2026-10-15T00:00:00', {'TC_10YEAR': '1.8'} if is_real else yields()),
                ('2026-10-16T00:00:00', {'TC_10YEAR': '1.9'} if is_real else yields('4.1', '4.7'))]
    year = request.url.params['field_tdr_date_value']
    return httpx.Response(200, content=feed([(day, values) for day, values in rows if day[:4] == year]))


def by_id(snapshot):
    return {row['id']: row for row in snapshot['series']}


def test_parser_rejects_duplicates_invalid_future_nonfinite_and_wrong_year():
    rows = [
        ('2026-10-12T00:00:00', yields('3', '4')),
        ('2026-10-12T00:00:00', yields('3.1', '4.1')),  # ambiguous revision within one feed
        ('2026-10-13T00:00:00', yields('nan', '4')),
        ('2026-10-14T00:00:00', yields('4', 'inf')),
        ('2026-10-15T00:00:00', yields('invalid', '4')),
        ('2026-10-16T00:00:00', yields('-0.1', '0')),
        ('2026-10-17T00:00:00', {'BC_10YEAR': '4.25'}),
        ('2026-10-18T00:00:00', {'BC_2YEAR': '', 'BC_10YEAR': ''}),
        ('2026-10-20T00:00:00', yields()),
        ('2025-10-16T00:00:00', yields()),
        ('2026-10-16not-a-date', yields()),
        ('2026-02-30T00:00:00', yields()),
    ]
    parsed = parse_treasury_xml(feed(rows), 'nominal', 2026, NOW.date())
    assert parsed['treasury-2y'] == [{'date': '2026-10-16', 'value': -0.1}]
    assert parsed['treasury-10y'] == [{'date': '2026-10-16', 'value': 0}, {'date': '2026-10-17', 'value': 4.25}]


@pytest.mark.parametrize('payload', [b'<html/>', b'not XML', b'<!DOCTYPE feed><feed/>', b'x' * (MAX_BYTES + 1)])
def test_parser_rejects_bad_documents(payload):
    with pytest.raises((ValueError, ET.ParseError)):
        parse_treasury_xml(payload, 'nominal', 2026, NOW.date())


@pytest.mark.asyncio
async def test_snapshot_alignment_missing_tenor_and_publication_unknown(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        assert request.extensions['timeout']['read'] == 5
        assert request.url.host == 'home.treasury.gov'
        nominal = [('2026-10-14T00:00:00', yields('4', '4.4')),
                   ('2026-10-15T00:00:00', yields('4.1', '4.6')),
                   ('2026-10-16T00:00:00', {'BC_10YEAR': '4.8'})]
        return responses(request, nominal=nominal)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = by_id(await MacroService(tmp_path / 'market.sqlite3', client, lambda: NOW).snapshot())
    assert len(requests) == 4
    assert {r.url.params['data'] for r in requests} == {'daily_treasury_yield_curve', 'daily_treasury_real_yield_curve'}
    assert {r.url.params['field_tdr_date_value'] for r in requests} == {'2025', '2026'}
    assert result['treasury-curve']['value'] == 50
    assert result['treasury-curve']['previous'] == 40
    assert result['treasury-curve']['change'] == 10
    assert result['treasury-curve']['observedOn'] == '2026-10-15'
    assert result['treasury-curve']['unit'] == 'basis-points'
    assert result['treasury-10y']['observedOn'] == '2026-10-16'
    assert result['treasury-2y']['observedOn'] == '2026-10-15'
    assert result['treasury-2y']['change'] == pytest.approx(.1)
    assert all(row['publishedAt'] is None for row in result.values())
    assert all(row['fetchedAt'] == '2026-10-19T12:00:00Z' for row in result.values())
    assert all(row['status'] == 'delayed' for row in result.values())


@pytest.mark.asyncio
async def test_cache_revisions_stale_fallback_and_failure_retry(tmp_path):
    clock, calls, state = [NOW], [], ['ok']
    def handler(request):
        calls.append(request)
        if state[0] == 'error':
            return httpx.Response(403, text='secret=do-not-reflect')
        nominal = None if state[0] == 'ok' else [('2026-10-15T00:00:00', yields()), ('2026-10-16T00:00:00', yields('4.2', '4.9'))]
        return responses(request, nominal=nominal)
    path = tmp_path / 'market.sqlite3'
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = MacroService(path, client, lambda: clock[0])
        first = await service.snapshot()
        await asyncio.gather(*(service.snapshot() for _ in range(3)))
        assert len(calls) == 4
        clock[0] += timedelta(hours=6)
        state[0] = 'revised'
        revised = by_id(await service.snapshot())
        assert revised['treasury-10y']['value'] == 4.9
        assert len(revised['treasury-10y']['history']) == 2
        clock[0] += timedelta(hours=6)
        state[0] = 'error'
        stale = by_id(await service.snapshot())
        assert all(row['status'] == 'stale' for row in stale.values())
        assert stale['treasury-10y']['value'] == 4.9
        assert stale['treasury-10y']['fetchedAt'] == revised['treasury-10y']['fetchedAt']
        assert 'HTTP 403' in stale['treasury-10y']['detail']
        assert all('secret=' not in row['detail'] for row in stale.values())
        call_count = len(calls)
        restored = MacroService(path, client, lambda: clock[0])
        assert by_id(await restored.snapshot())['treasury-10y'] == stale['treasury-10y']
        assert len(calls) == call_count
        clock[0] += timedelta(minutes=59)
        await service.snapshot()
        assert len(calls) == call_count
        clock[0] += timedelta(minutes=1)
        await service.snapshot()
        assert len(calls) == call_count + 4
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT count(*) FROM macro_cache').fetchone()[0] == 2
    assert by_id(first)['treasury-10y']['value'] == 4.7


@pytest.mark.asyncio
async def test_initial_failure_partial_failure_and_empty_data(tmp_path):
    clock, mode, calls = [NOW], ['blocked'], []
    def handler(request):
        calls.append(request)
        if mode[0] == 'blocked' or request.url.params['data'] == 'daily_treasury_real_yield_curve':
            raise httpx.ProxyError('private connection details')
        return responses(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = MacroService(tmp_path / 'market.sqlite3', client, lambda: clock[0])
        unavailable = await service.snapshot()
        assert all(row['status'] == 'unavailable' and row['value'] is None and row['history'] == [] for row in unavailable['series'])
        assert all(row['fetchedAt'] is None and row['publishedAt'] is None for row in unavailable['series'])
        assert all('private' not in row['detail'] for row in unavailable['series'])
        await service.snapshot()
        assert len(calls) == 4
        clock[0] += timedelta(hours=1)
        mode[0] = 'partial'
        partial = by_id(await service.snapshot())
        assert partial['treasury-2y']['status'] == 'delayed'
        assert partial['treasury-real-10y']['status'] == 'unavailable'
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=feed([])))) as client:
        empty = await MacroService(tmp_path / 'empty.sqlite3', client, lambda: NOW).snapshot()
        assert all(row['status'] == 'unavailable' for row in empty['series'])


@pytest.mark.asyncio
async def test_january_boundary_and_coarse_date_staleness(tmp_path):
    clock = [datetime(2026, 1, 1, 12, tzinfo=timezone.utc)]
    rows = [('2025-12-30T00:00:00', yields()), ('2025-12-31T00:00:00', yields('4.1', '4.7'))]
    real = [('2025-12-30T00:00:00', {'TC_10YEAR': '-0.2'}), ('2025-12-31T00:00:00', {'TC_10YEAR': '-0.1'})]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: responses(request, rows, real))) as client:
        service = MacroService(tmp_path / 'market.sqlite3', client, lambda: clock[0])
        first = by_id(await service.snapshot())
        assert first['treasury-10y']['previousOn'] == '2025-12-30'
        assert 'field_tdr_date_value=2025' in first['treasury-10y']['sourceUrl']
        assert first['treasury-10y']['status'] == 'delayed'
        clock[0] = datetime(2026, 1, 7, 12, tzinfo=timezone.utc)
        assert by_id(await service.snapshot())['treasury-10y']['status'] == 'delayed'
        clock[0] += timedelta(days=1)
        assert by_id(await service.snapshot())['treasury-10y']['status'] == 'stale'


@pytest.mark.asyncio
async def test_history_bound_and_stream_payload_limit(tmp_path):
    start = date(2026, 1, 1)
    rows = [((start + timedelta(days=i)).isoformat(), yields()) for i in range(290)]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: responses(request, nominal=rows))) as client:
        result = await MacroService(tmp_path / 'market.sqlite3', client, lambda: NOW).snapshot()
        assert len(by_id(result)['treasury-2y']['history']) == 260
        assert len(by_id(result)['treasury-curve']['history']) == 260
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b'x' * (MAX_BYTES + 1)))) as client:
        result = await MacroService(tmp_path / 'large.sqlite3', client, lambda: NOW).snapshot()
        assert all(row['status'] == 'unavailable' for row in result['series'])


@pytest.mark.asyncio
async def test_missing_previous_observation_withholds_change(tmp_path):
    rows = [('2026-10-16T00:00:00', {'BC_10YEAR': '4'})]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: responses(request, nominal=rows))) as client:
        result = by_id(await MacroService(tmp_path / 'market.sqlite3', client, lambda: NOW).snapshot())
    assert result['treasury-10y']['previous'] is None
    assert result['treasury-10y']['change'] is None
    assert result['treasury-2y']['status'] == 'unavailable'
    assert result['treasury-curve']['status'] == 'unavailable'


@pytest.mark.asyncio
async def test_nonfinite_derived_arithmetic_is_withheld(tmp_path):
    rows = [('2026-10-15T00:00:00', yields('-1e308', '-1e308')),
            ('2026-10-16T00:00:00', yields('-1e308', '1e308'))]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: responses(request, nominal=rows))) as client:
        result = by_id(await MacroService(tmp_path / 'market.sqlite3', client, lambda: NOW).snapshot())
    assert result['treasury-10y']['change'] is None
    assert result['treasury-curve']['observedOn'] == '2026-10-15'
    assert result['treasury-curve']['value'] == 0


@pytest.mark.asyncio
async def test_timeout_and_malformed_refresh_preserve_lastgood(tmp_path):
    clock, state = [NOW], ['ok']
    def handler(request):
        if state[0] == 'timeout':
            raise httpx.ReadTimeout('private URL or token')
        if state[0] == 'invalid':
            return httpx.Response(200, content=b'<html>not Treasury XML</html>')
        return responses(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = MacroService(tmp_path / 'market.sqlite3', client, lambda: clock[0])
        await service.snapshot()
        clock[0] += timedelta(hours=6)
        state[0] = 'timeout'
        timeout = await service.snapshot()
        assert all(row['status'] == 'stale' and row['value'] is not None for row in timeout['series'])
        assert all('timed out' in row['detail'] and 'private' not in row['detail'] for row in timeout['series'])
        clock[0] += timedelta(hours=1)
        state[0] = 'invalid'
        invalid = await service.snapshot()
        assert all(row['status'] == 'stale' and row['value'] is not None for row in invalid['series'])
        assert all('could not be validated' in row['detail'] for row in invalid['series'])


def test_parser_limits_row_count_and_rejects_duplicate_fields():
    with pytest.raises(ValueError, match='row count'):
        parse_treasury_xml(feed([('2026-10-16', yields())] * 371), 'nominal', 2026, NOW.date())
    payload = feed([('2026-10-16', yields())]).replace(b'</m:properties>', b'<d:BC_2YEAR>5</d:BC_2YEAR></m:properties>')
    assert parse_treasury_xml(payload, 'nominal', 2026, NOW.date()) == {'treasury-2y': [], 'treasury-10y': []}
