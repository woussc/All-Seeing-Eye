# Governed collection and publication controls

The Pages collector now has request accounting and explicit activation gates. The committed policies are **disabled** because provider access/limits have not been verified. This upgrade does not activate an hourly/15-minute schedule or a live crypto stream. The existing workflow remains daily. The local development API retains its earlier behavior; the new ledger governs the separate public Pages collector.

## Source policies

`config/source-policies.json` defines application rolling-day ceilings: 500 combined, with smaller per-provider caps. They are not purchased allowances or claims about a provider's free tier. A source requires all of the following before the governed collector sends a request:

- `enabled: true` following an actual successful source/limit review;
- a timezone-aware `verifiedAt` within the last 30 days;
- an HTTPS `verificationUrl` without embedded credentials;
- one or more `verifiedWindows`, each with a positive `seconds` and `requests` limit matching the verified provider policy;
- an existing valid ledger with capacity in the global, provider and verified rolling windows, with no active access-denial gate/cooldown.

Do not populate verification metadata from guesses, copied quota claims about another provider, or synthetic test fixtures. Unknown limits remain unverified. Record the source evidence when changing activation settings; passing fixtures does not verify a provider allowance. No account or key is added by the policy file.

## Ledger lifecycle

The collector reads an explicit ledger path, defaulting to `.runtime/pages-quota.sqlite3`, **outside** the disposable source cache at `.runtime/pages-state`. A missing, invalid or corrupted ledger prevents governed requests and reports unknown usage. It never starts a new budget automatically.

An operator may initialize a genuinely new ledger once:

```sh
services/market/.venv/bin/python services/market/export_pages.py --initialize-ledger
```

This creates accounting state only; it does not enable sources, start collection or replace an existing file. Preserve and back up this ledger. After ledger loss, do not initialize a replacement and assume prior traffic was zero. Restore authoritative state or wait for the longest applicable rolling window and explicitly reconcile prior usage.

Normal collection:

```sh
services/market/.venv/bin/python services/market/export_pages.py
```

Optional `--ledger`, `--policy` and `--state-dir` select explicit paths. Do not point this publisher at the local private application's database. Public export rejects imported flow records and never publishes the ledger or source-cache database.

SQLite transactions serialize reservations across processes. Each HTTP request is reserved before proxy routing/transport, including redirects, failures and retries. Failed/cancelled attempts are not refunded. Both application rolling-day caps and every verified rolling window apply. A significant backwards clock movement or invalid schema fails closed. Server `Retry-After` controls 429/503 cooldown; access denial requires a newer source verification before collection resumes.

**Remaining activation gate:** durable accounting on an authoritative GitHub data branch, including pre-request reservation persistence, has not been wired into Actions. Never restore this ledger only through `actions/cache` and call the budget durable. Default disabled policies prevent new requests while that integration and source verification remain incomplete. The existing source cache may still supply explicitly dated retained observations.

## Consistent publications

The exporter preserves legacy snapshot/report files and adds:

- `feed.json`: a small typed manifest with generation, timestamp, payload paths and SHA-256 hashes;
- `generations/<generation>/snapshot.json` and `publication.json`: matching source and collection metadata;
- versioned publication metadata with cadence/overdue thresholds, source-budget status and a factual change tape.

The preceding generation is retained during rollout. Each generation is staged before publication replacement; malformed prior history prevents replacement. The browser bounds response sizes, validates paths/types/timestamps and checks both payload hashes before changing the view. An unchanged generation fetches only the manifest. An invalid update preserves the previous validated view. Snapshot/report history is not retrospectively enriched with new metadata.

The change tape compares captured last quotes, source status and macro observations. It distinguishes a revised macro observation from a newly dated one; it never infers flows, trades or causal allocation from prices. Empty tape means no comparable material record change, possibly the first capture.

## Client refresh behavior

Manual and automatic Pages checks share one in-flight/cooldown gate. Normal checks are eligible once per minute; failures back off to two, four and at most five minutes. Hidden/offline tabs spend no automatic checks; visibility/connectivity recovery uses the same gate. Multi-tab leader election and live streaming remain future work.

Published observation clocks remain separate from the completed-session baseline. The publication supplies its own overdue threshold, so a future verified hourly profile cannot silently inherit the legacy 36-hour grace. Overdue analytical confirmations are suppressed. Archived snapshots stay frozen and retain their original capture dates. The Sources screen shows accounting **at capture time**, with unknown usage distinguished from zero; it is not a live billing meter.

## Validation and recovery

Numerical and failure checks use mock HTTP transports, temporary ledgers and injected clocks. They test atomic caps, rolling windows, clock rollback, missing/corrupt state, expired/absent verification, response cooldowns, failed attempts, unsafe destinations and public-export privacy. Client tests cover response integrity, generation consistency, path/size limits, cooldowns and stale publication thresholds. Browser checks exercise the static project subpath and legacy local workflows.

The resumable implementation record is `docs/freshness-checkpoint.md`. It records passing checks, any unresolved failure and the exact next step. Runtime logs and test artifacts stay under `.runtime`; checkpointing does not guarantee survival of workspace loss or an automatic restart after a usage-limit refresh.
