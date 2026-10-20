# Treasury macro context

`services/market/macro.py` adds four daily series, separately from market-price
observations. This is end-of-day context, with no intraday or live claim.

| ID                  | Official Treasury field             | Value unit   | Change unit       |
| ------------------- | ----------------------------------- | ------------ | ----------------- |
| `treasury-2y`       | Nominal par yield curve `BC_2YEAR`  | Percent      | Percentage points |
| `treasury-10y`      | Nominal par yield curve `BC_10YEAR` | Percent      | Percentage points |
| `treasury-real-10y` | Real par yield curve `TC_10YEAR`    | Percent      | Percentage points |
| `treasury-curve`    | Derived `(10Y - 2Y) × 100`          | Basis points | Basis points      |

Source URLs use the official Treasury annual XML feeds:

```text
https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value=2026
https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_real_yield_curve&field_tdr_date_value=2026
```

Treasury's [interest-rate statistics page](https://home.treasury.gov/resource-center/data-chart-center/interest-rates)
provides source context. These are par yields, not zero-coupon spot rates.
The adapter requests the current and previous New York calendar years to retain
previous observations near January 1. Each returned history holds at most 260
available observations. `sourceUrl` points to the latest observation's annual
feed; a history can also include the preceding year's feed.

`NEW_DATE` becomes `observedOn`/history `date`. It is a date label, never a time
when information became available. `publishedAt` remains null: neither Atom
`updated` nor fetch time has verified source-publication semantics. `fetchedAt`
records successful ingestion and stays unchanged on failed refreshes. Historical
feeds can contain revisions; refreshes replace the cached annual-derived history.
This is not a point-in-time archive and must not be used to reconstruct what was
known at a historical instant.

The curve uses only dates with both nominal tenors. A missing latest tenor can
leave the curve's latest date earlier than another series. Previous/change refer
to the previous available observation, with its date in `previousOn`; they do not
assert a one-session change. Missing values stay null; there is no interpolation.
Duplicate dates are excluded entirely, malformed/nonfinite/future rows are
rejected, and negative yields remain valid. Present invalid numeric fields
invalidate the row; absent/blank tenor values remain missing. Nonfinite derived
arithmetic is withheld.

Successful observations are `delayed`; observations older than seven calendar
days or retained after failed refreshes are `stale`. A series without usable
observations is `unavailable`. The date-age rule is intentionally coarse: no
Treasury-specific holiday or publication calendar is implemented. It tolerates
weekends and short holiday gaps without claiming a precise expected release.

There are four annual requests per refresh at most. A successful source caches
for six hours; a failed source retries after one hour. Nominal and real sources
fail independently. Each response is limited to 2 MB decompressed, 370 entries,
five-second HTTP timeouts and an eight-second total bound per source refresh.
Redirects, DTDs and entities are rejected. Refreshes are serialized within one
service instance. SQLite table `macro_cache` holds two source records and
preserves last-good histories independently of the market service tables. No
cross-process refresh lock is provided; deploy one instance or add a shared
refresh coordinator before scaling workers.

## Integration

One `MacroService(store.path, client)` is instantiated during the FastAPI lifespan with the shared HTTP client. Its snapshot is gathered alongside prices and attached as `Snapshot.context.macro` by `GET /snapshot`. The service owns the refresh lock; it is not created per request. The Research and Sources screens retain dates, statuses and source URLs.

The snapshot has `generatedAt` and a four-element `series` list. Each series
includes its ID, label, unit, current/previous/change values, corresponding dates,
null `publishedAt`, last-successful `fetchedAt`, status, source URL, explanatory
detail and bounded dated history. Preserve these dates and status in the UI.

No DXY approximation, credit-spread feed, ETF price proxy or synthetic fallback
is supplied. Nominal and real yields are distinct series; the adapter does not
subtract them into an unrequested inflation measure.

## Validation

The official XML path was corrected after the earlier path failed. Successful live adapter requests verified both feed types on 19 October 2026. At 23:12 UTC the 19 October nominal 2Y/10Y yields were 4.77% and 5.15%, real 10Y was 2.80%, and the derived spread was 38 bp. Each series retained 260 observations.

A separate download of Treasury's nominal and real annual CSV files matched those values and the prior 16 October observations. This checks the XML field mapping against another official format, not an independent data vendor. Evidence remains in `.runtime/treasury-csv-verification.json` and `.runtime/connected-check.json` at the repository root.

Fifteen macro tests cover parsing, missing/invalid fields, chronology, annual rollover, stale fallback, response bounds and the official endpoint path:

```sh
cd services/market
.venv/bin/python -m pytest tests/test_macro.py -q
```

Publication time remains unknown and historical vintages are not reconstructed. Live connectivity does not establish future availability.
