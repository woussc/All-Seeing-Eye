"""Fail-closed request reservations. Limits are application caps, not API entitlements."""
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from calculations import iso, parse_time

DAY = 86_400
HOSTS = {
    'query1.finance.yahoo.com': 'yahoo', 'api.exchange.coinbase.com': 'coinbase',
    'home.treasury.gov': 'treasury', 'api.bls.gov': 'bls',
    'fred.stlouisfed.org': 'fred', 'publicreporting.cftc.gov': 'cftc',
}


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class Window(Strict):
    seconds: int = Field(ge=1, le=DAY * 31)
    requests: int = Field(ge=1, le=1_000_000)


class SourcePolicy(Strict):
    enabled: bool = False
    dailyCap: int = Field(ge=1, le=100_000)
    verifiedAt: str | None = None
    verificationUrl: str | None = None
    verifiedWindows: list[Window] = Field(default_factory=list, max_length=10)

    def verified(self, now):
        try:
            at = parse_time(self.verifiedAt)
            url = urlsplit(self.verificationUrl)
            return (at <= now < at + timedelta(days=30) and bool(self.verifiedWindows)
                    and url.scheme == 'https' and bool(url.hostname)
                    and not url.username and not url.password)
        except (ValueError, TypeError, AttributeError):
            return False


class Policy(Strict):
    version: int = Field(ge=1, le=1)
    globalDailyCap: int = Field(ge=1, le=100_000)
    sources: dict[str, SourcePolicy]

    @classmethod
    def load(cls, path):
        path = Path(path)
        if path.stat().st_size > 100_000:
            raise ValueError('Source policy is too large')
        result = cls.model_validate(json.loads(path.read_text()))
        if set(result.sources) != set(HOSTS.values()):
            raise ValueError('Source policies must name each supported provider exactly once')
        return result


class RequestDenied(httpx.RequestError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('Collection withheld: ' + reason)


class QuotaLedger:
    def __init__(self, path, policy, clock=None):
        self.path = Path(path).resolve()
        self.policy = policy
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def initialize(self):
        """Explicit operator step; a scheduled request never creates a missing ledger."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive file creation prevents replacement of existing accounting state.
        with self.path.open('xb'):
            pass
        with sqlite3.connect(self.path) as conn:
            conn.executescript('''
              CREATE TABLE metadata (version INTEGER NOT NULL, last_clock REAL NOT NULL);
              CREATE TABLE reservations (id TEXT PRIMARY KEY,provider TEXT NOT NULL,at REAL NOT NULL);
              CREATE INDEX reservation_window ON reservations(provider,at);
              CREATE TABLE blocks (provider TEXT PRIMARY KEY,until REAL NOT NULL,reason TEXT NOT NULL,
                                   verification_after REAL);
            ''')
            conn.execute('INSERT INTO metadata VALUES (1,?)', (self.clock().timestamp(),))

    def connection(self):
        return sqlite3.connect('file:' + quote(str(self.path), safe='/') + '?mode=rw',
                               uri=True, timeout=5)

    @staticmethod
    def check_clock(conn, now):
        rows = conn.execute('SELECT version,last_clock FROM metadata').fetchall()
        if len(rows) != 1 or rows[0][0] != 1 or rows[0][1] > now + 5:
            raise RequestDenied('ledger invalid or clock moved backwards')

    def reserve(self, provider):
        now = self.clock()
        stamp = now.timestamp()
        source = self.policy.sources.get(provider)
        if not source or not source.enabled:
            raise RequestDenied('source disabled')
        if not source.verified(now):
            raise RequestDenied('source limits are unverified or verification expired')
        try:
            with self.connection() as conn:
                conn.execute('BEGIN IMMEDIATE')
                self.check_clock(conn, stamp)
                block = conn.execute('SELECT until,reason,verification_after FROM blocks WHERE provider=?', (provider,)).fetchone()
                if block and block[2] is not None and parse_time(source.verifiedAt).timestamp() <= block[2]:
                    raise RequestDenied('source access denied; verification required')
                if block and block[0] > stamp:
                    raise RequestDenied(block[1])
                if block:
                    conn.execute('DELETE FROM blocks WHERE provider=?', (provider,))
                all_count = conn.execute('SELECT count(*) FROM reservations WHERE at>?', (stamp - DAY,)).fetchone()[0]
                if all_count >= self.policy.globalDailyCap:
                    raise RequestDenied('global rolling-day budget exhausted')
                windows = [Window(seconds=DAY, requests=source.dailyCap), *source.verifiedWindows]
                for window in windows:
                    count = conn.execute('SELECT count(*) FROM reservations WHERE provider=? AND at>?',
                                         (provider, stamp - window.seconds)).fetchone()[0]
                    if count >= window.requests:
                        raise RequestDenied('provider request window exhausted')
                identity = uuid.uuid4().hex
                conn.execute('INSERT INTO reservations VALUES (?,?,?)', (identity, provider, stamp))
                conn.execute('UPDATE metadata SET last_clock=?', (stamp,))
                # Retain the longest configured window; failed/cancelled attempts are never refunded.
                longest = max([DAY, *(w.seconds for s in self.policy.sources.values() for w in s.verifiedWindows)])
                conn.execute('DELETE FROM reservations WHERE at<=?', (stamp - longest,))
                return identity
        except sqlite3.Error as exc:
            raise RequestDenied('quota ledger unavailable; no request was sent') from exc

    def block(self, provider, seconds, reason):
        stamp = self.clock().timestamp()
        verification_after = stamp if reason == 'source access denied; verification required' else None
        try:
            with self.connection() as conn:
                conn.execute('BEGIN IMMEDIATE')
                self.check_clock(conn, stamp)
                conn.execute('''INSERT INTO blocks VALUES (?,?,?,?) ON CONFLICT(provider) DO UPDATE
                    SET until=max(blocks.until,excluded.until),reason=excluded.reason,
                        verification_after=CASE WHEN excluded.verification_after IS NULL
                            THEN blocks.verification_after
                            ELSE max(coalesce(blocks.verification_after,excluded.verification_after),
                                     excluded.verification_after) END''',
                    (provider, stamp + seconds, reason, verification_after))
                conn.execute('UPDATE metadata SET last_clock=?', (stamp,))
        except sqlite3.Error as exc:
            raise RequestDenied('quota ledger unavailable') from exc

    def status(self):
        now = self.clock()
        stamp = now.timestamp()
        result = {'state': 'available', 'windowSeconds': DAY, 'globalLimit': self.policy.globalDailyCap,
                  'globalUsed': 0, 'sources': [], 'asOf': iso(now)}
        try:
            with self.connection() as conn:
                self.check_clock(conn, stamp)
                rows = conn.execute('SELECT provider,at FROM reservations WHERE at>? ORDER BY at', (stamp - DAY,)).fetchall()
                blocks = {p: (until, reason, verify) for p, until, reason, verify in conn.execute(
                    'SELECT provider,until,reason,verification_after FROM blocks')}
        except (sqlite3.Error, RequestDenied):
            result['state'] = 'unavailable'
            rows, blocks = [], {}
        result['globalUsed'] = len(rows) if result['state'] == 'available' else None
        for provider, source in self.policy.sources.items():
            attempts = [at for p, at in rows if p == provider]
            blocked = blocks.get(provider)
            used = len(attempts) if result['state'] == 'available' else None
            result['sources'].append({
                'id': provider, 'enabled': source.enabled, 'verified': source.verified(now),
                'used': used, 'limit': source.dailyCap,
                'remaining': max(0, source.dailyCap - used) if used is not None else None,
                'nextCapacityAt': iso(datetime.fromtimestamp(attempts[0] + DAY, timezone.utc)) if attempts else None,
                'blockedUntil': iso(datetime.fromtimestamp(blocked[0], timezone.utc)) if blocked and blocked[0] > stamp else None,
                'verificationRequired': bool(blocked and blocked[2] is not None
                                             and (not source.verified(now) or parse_time(source.verifiedAt).timestamp() <= blocked[2])),
            })
        return result


class GovernedRequests:
    """HTTPX hooks apply before proxy routing and redirects, counting every actual attempt."""
    def __init__(self, ledger):
        self.ledger = ledger

    async def before(self, request):
        provider = HOSTS.get(request.url.host)
        if request.url.scheme != 'https' or not provider or request.url.username or request.url.password:
            raise RequestDenied('unsupported source destination')
        self.ledger.reserve(provider)

    async def after(self, response):
        provider = HOSTS.get(response.request.url.host)
        if response.status_code in (401, 403):
            self.ledger.block(provider, DAY, 'source access denied; verification required')
        elif response.status_code in (429, 503):
            from email.utils import parsedate_to_datetime
            raw = response.headers.get('retry-after', '')
            try:
                delay = max(1, int(raw))
            except ValueError:
                try:
                    delay = max(1, int((parsedate_to_datetime(raw) - self.ledger.clock()).total_seconds()))
                except (ValueError, TypeError, OverflowError):
                    delay = 300
            self.ledger.block(provider, min(delay, DAY * 31), 'source cooldown')

    @property
    def hooks(self):
        return {'request': [self.before], 'response': [self.after]}
