"""Deterministic accounting fixtures; no provider connections are opened."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from governor import GovernedRequests, Policy, QuotaLedger, RequestDenied, SourcePolicy, Window

NOW = datetime(2026, 10, 21, 4, tzinfo=timezone.utc)


def setup(tmp_path, cap=3, global_cap=5, window=100, requests=20):
    source = SourcePolicy(enabled=True, dailyCap=cap, verifiedAt=NOW.isoformat(),
        verificationUrl='https://example.org/test-policy', verifiedWindows=[Window(seconds=window, requests=requests)])
    policy = Policy(version=1, globalDailyCap=global_cap, sources={'yahoo': source, 'coinbase': source.model_copy()})
    clock = [NOW]
    ledger = QuotaLedger(tmp_path / 'ledger.sqlite3', policy, lambda: clock[0])
    return ledger, clock


def test_missing_corrupt_and_existing_ledger_fail_closed(tmp_path):
    ledger, _ = setup(tmp_path)
    with pytest.raises(RequestDenied, match='unavailable'):
        ledger.reserve('yahoo')
    assert not ledger.path.exists()
    assert ledger.status()['globalUsed'] is None
    ledger.initialize()
    with pytest.raises(FileExistsError):
        ledger.initialize()
    ledger.path.write_bytes(b'broken-ledger')
    with pytest.raises(RequestDenied):
        ledger.reserve('yahoo')


def test_atomic_global_and_provider_limits_across_workers(tmp_path):
    ledger, _ = setup(tmp_path); ledger.initialize()
    def reserve(_):
        try: return ledger.reserve('yahoo')
        except RequestDenied: return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert len([x for x in pool.map(reserve, range(20)) if x]) == 3
    ledger.reserve('coinbase'); ledger.reserve('coinbase')
    with pytest.raises(RequestDenied, match='global'):
        ledger.reserve('coinbase')
    restored = QuotaLedger(ledger.path, ledger.policy, ledger.clock)
    assert restored.status()['globalUsed'] == 5


def test_rolling_windows_not_midnight_resets_and_clock_rollback(tmp_path):
    ledger, clock = setup(tmp_path, window=60, requests=1); ledger.initialize()
    ledger.reserve('yahoo')
    clock[0] += timedelta(seconds=59)
    with pytest.raises(RequestDenied, match='window'):
        ledger.reserve('yahoo')
    clock[0] += timedelta(seconds=1)
    ledger.reserve('yahoo')
    clock[0] -= timedelta(seconds=10)
    with pytest.raises(RequestDenied, match='backwards'):
        ledger.reserve('yahoo')
    clock[0] = NOW + timedelta(days=1)
    assert ledger.status()['globalUsed'] == 1


@pytest.mark.parametrize('change', ['disabled', 'expired', 'future', 'no-window', 'bad-url'])
def test_verification_gates(change, tmp_path):
    ledger, _ = setup(tmp_path); ledger.initialize()
    source = ledger.policy.sources['yahoo']
    if change == 'disabled': source.enabled = False
    elif change == 'expired': source.verifiedAt = (NOW - timedelta(days=31)).isoformat()
    elif change == 'future': source.verifiedAt = (NOW + timedelta(days=1)).isoformat()
    elif change == 'no-window': source.verifiedWindows = []
    else: source.verificationUrl = 'https://user:password@example.org'
    with pytest.raises(RequestDenied): ledger.reserve('yahoo')
    assert ledger.status()['globalUsed'] == 0


@pytest.mark.asyncio
async def test_failed_cancelled_and_redirected_requests_are_counted(tmp_path):
    ledger, _ = setup(tmp_path, cap=10, global_cap=10); ledger.initialize()
    def handle(request):
        if request.url.path == '/fail': raise httpx.ConnectError('fixture failure')
        if request.url.path == '/redirect': return httpx.Response(302, headers={'Location': '/done'})
        return httpx.Response(200)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), event_hooks=GovernedRequests(ledger).hooks,
                                 follow_redirects=True) as client:
        with pytest.raises(httpx.ConnectError): await client.get('https://query1.finance.yahoo.com/fail')
        await client.get('https://query1.finance.yahoo.com/redirect')
        with pytest.raises(RequestDenied): await client.get('https://unapproved.example/test')
    assert ledger.status()['globalUsed'] == 3
    ledger.reserve('yahoo')  # cancelled work cannot refund the reservation
    assert ledger.status()['globalUsed'] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize('status,retry', [(429, '90'), (503, 'Wed, 21 Oct 2026 04:02:00 GMT'), (403, '')])
async def test_server_cooldown_and_access_denial_prevent_next_wire_call(tmp_path, status, retry):
    ledger, clock = setup(tmp_path); ledger.initialize()
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers={'Retry-After': retry})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), event_hooks=GovernedRequests(ledger).hooks) as client:
        await client.get('https://query1.finance.yahoo.com/test')
        with pytest.raises(RequestDenied): await client.get('https://query1.finance.yahoo.com/test')
    assert len(calls) == 1 and ledger.status()['globalUsed'] == 1
    assert ledger.status()['sources'][0]['blockedUntil'] is not None


def test_committed_policy_is_disabled_and_requires_exact_known_sources():
    from pathlib import Path
    policy = Policy.load(Path(__file__).resolve().parents[3] / 'config/source-policies.json')
    assert policy.globalDailyCap == 500
    assert all(not source.enabled for source in policy.sources.values())


def test_access_denial_requires_new_verification_even_after_cooldown(tmp_path):
    ledger, clock = setup(tmp_path); ledger.initialize()
    ledger.block('yahoo', 86400, 'source access denied; verification required')
    clock[0] += timedelta(days=2)
    with pytest.raises(RequestDenied, match='verification required'):
        ledger.reserve('yahoo')
    assert ledger.status()['sources'][0]['verificationRequired'] is True
    ledger.policy.sources['yahoo'].verifiedAt = clock[0].isoformat()
    ledger.reserve('yahoo')
    assert ledger.status()['sources'][0]['verificationRequired'] is False


@pytest.mark.parametrize('access_first', [True, False])
def test_concurrent_response_order_cannot_erase_access_gate_or_longer_cooldown(tmp_path, access_first):
    ledger, clock = setup(tmp_path); ledger.initialize()
    blocks = [(86400, 'source access denied; verification required'), (86400 * 5, 'source cooldown')]
    for seconds, reason in blocks if access_first else reversed(blocks):
        ledger.block('yahoo', seconds, reason)
    clock[0] += timedelta(days=2)
    with pytest.raises(RequestDenied, match='verification required'):
        ledger.reserve('yahoo')
    assert ledger.status()['sources'][0]['verificationRequired'] is True
    ledger.policy.sources['yahoo'].verifiedAt = clock[0].isoformat()
    assert ledger.status()['sources'][0]['verificationRequired'] is False
    with pytest.raises(RequestDenied):
        ledger.reserve('yahoo')  # Verification does not erase a longer server cooldown.
    clock[0] += timedelta(days=3)
    ledger.reserve('yahoo')
