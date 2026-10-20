# Market snapshot contract

`contracts/market.ts` is the integration contract. Backend route: `GET /snapshot`, returning Snapshot JSON. `GET /health` reports service health. Web fetches through its own server route; browser never calls a data vendor directly.

Use ISO8601 UTC timestamps. Daily and weekly change are percent units; null means unavailable, including insufficient history. All twelve assets remain in the response even if sources fail. `mode: connected` means source adapters were requested, not that every feed is live. Label each asset with its actual latency/status. Report sources that failed. Never substitute synthetic history for missing provider data.

`history` contains verified completed daily price observations, time ascending, dates explicit. ETF `commonClose` can use a verified completed regular-session daily close, with its time labeled as the aggregation endpoint, not an exact trade tick. `commonCloseAt` is the actual prior market session cutoff, with holidays/DST/early closes respected. Do not fabricate a crypto mark at that cutoff from a midnight daily bar. Latest quote changes are source-defined windows; describe windows in `note`, especially crypto 24h versus exchange-session returns. Persist observation/publication/ingestion metadata as available, and document unsupported information-cutoff reconstruction.

Indicators return null until history, adjustments and definitions are sufficient. Core data calculations remain deterministic. Credential values are server-side only. Cache provider queries and preserve a visibly stale last-good record when appropriate. Enforce provider timeouts; requests must not hang the interface.

The local development store may use SQLite to validate snapshot persistence without external services. PostgreSQL remains the deployment target; record this staged implementation choice.

## Research context

`contracts/context.ts` defines optional `Snapshot.context`: macro observations, flow rollups and persistent watch states. The backend builds these in `GET /snapshot`; the browser validates the complete response before adopting it. Older snapshots may omit context. Invalid/partial context is not silently accepted as trustworthy numerical data.

Macro values use percent for yields and basis points for the 10Y–2Y curve. `observedOn` is a source date, `publishedAt` remains null until verified, and `fetchedAt` is ingestion time. Previous/change compare consecutive available observations, with both dates explicit. See [macro sources](macro-sources.md).

Flows are signed USD net flow over exact seven-calendar-date published periods. Complete 1/4/13-week windows require contiguous complete periods with unchanged constituent membership. Missing weeks, partial baskets or invalid coverage withhold totals. A real zero stays zero. Optional normalization uses the first week's beginning AUM, dated one day before that week begins. Publication and import timestamps remain distinct. Overlapping scopes are never summed. See [import and revision rules](research-operations.md).

Analytical watches consume adjusted history at exact aligned completed NYSE sessions. This differs from the overview's raw-price return prompts. A watch carries evidence, state (active/resolved/suppressed), transition history and separate acknowledgment. States are research observations, not execution instructions.

## API and storage

| Route                           | Behavior                                                                                         |
| ------------------------------- | ------------------------------------------------------------------------------------------------ |
| `POST /flows/import`            | Validate `{document, commit}`; preview rolls back and confirmed batches commit atomically        |
| `GET /reports`                  | Latest 100 report summaries; full archive remains stored                                         |
| `POST /reports`                 | Archive `{snapshotId}` from the server's existing cache; arbitrary client snapshots are rejected |
| `GET /reports/{id}`             | Full report, snapshot, briefing and verified SHA-256 digest                                      |
| `POST /alerts/{id}/acknowledge` | Persist acknowledgment independently of resolution                                               |

Next.js forwards permitted research routes through `/api/research/...`. Request bodies and route shapes are bounded; mutations enforce the configured browser origin or local loopback origin. This does not establish user authentication.

The snapshot cache retains the latest 100 responses. Full archived reports and flow revisions are retained separately without that eviction rule. Reports preserve evidence as captured, with immutable storage guards and read-time digest checks; they cannot reconstruct source-publication vintages. Optional daily reports use Singapore dates and actual capture times. SQLite backup/restore is implemented and verified; hosted storage and external backup retention remain deployment work.

New `briefing.monitorMarkdown` is an optional deterministic report export, so existing archived reports remain readable. It uses matching `commonClose.asOf` values only, cites sources and retains unavailable values. Yield changes remain percentage points in the API for percent-valued series; the UI and monitor export multiply those changes by 100 once to display basis points. Curve changes already use basis points. Prior archived snapshots are integrity-checked before use as a baseline; a baseline generated after the current snapshot is withheld.

## Completed-bar technical context

Optional `Asset.technicals` carries `trend-v1` evidence: exact completed-bar timestamp, price basis, 4/13-week returns, five-bar EMA percentage moves, ATR14/close, confirmed high/low pivots and a descriptive classification/reason. Legacy snapshots may omit it. The frontend validates finite values, units, timestamps and bounded pivot arrays. In a common-close view, evidence from another timestamp is withheld; stale sources cannot establish trend confirmation. [Technical definitions](technical-evidence.md) specify warmup, session/day windows, pivot confirmation and thresholds.
