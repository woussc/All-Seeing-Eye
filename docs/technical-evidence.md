# Completed-bar technical evidence

`Asset.technicals` is optional and versioned as `trend-v1`. Older snapshots omit it and remain readable without retrospective enrichment. The calculation uses the same verified, completed, contiguous daily bars as the existing indicators. ETF OHLC is provider-adjusted; Coinbase OHLC is venue-specific and unadjusted. The API preserves an explicit `asOf`, basis and window unit.

## Returns, slopes and risk

- Four/thirteen weeks mean **20/65 NYSE sessions for ETFs**, **28/91 calendar days for crypto**. Returns are `(last close / window-start close − 1) × 100`; both endpoints and all intermediate expected bars must exist. A gap resets the usable suffix. Crypto UTC daily bars do not establish marks at the US close.
- Existing ETF 1d/1w quote fields are raw-price returns excluding distributions. The new 4w/13w context uses provider-adjusted bars. Both bases are explicitly labeled; the new values must not be treated as an unexplained continuation of the raw-return series or as an independently verified reinvested total-return index.
- A five-bar EMA slope is the **percentage change in that EMA over five completed bars**, not an annualized rate or a daily average. Both EMA endpoints require five periods of warmup, so EMA20/50/200 slopes need 105/255/1005 contiguous bars. SMA seeds and exponential decay match the existing EMA implementation.
- `atr14Pct = ATR14 / latest completed close × 100`, with numerator and denominator on the same adjusted or venue-specific basis. It never divides an adjusted ATR by a raw intraday quote. Existing 150-bar Wilder warmup applies. Missing/nonfinite results remain null.

## Confirmed structure

Inspect at most the latest 126 contiguous bars; at least 20 are required. A high pivot must be strictly higher than each of two bars before and two bars after it. A low pivot must be strictly lower. Ties do not produce pivots. The most recent two bars cannot yet be confirmed pivots.

Keep the latest two confirmed highs and lows, with the pivot time, price and the second later bar's `confirmedAt` timestamp. Both rising pairs produce `higher-highs-higher-lows`; both falling pairs produce `lower-highs-lower-lows`; otherwise the result is mixed. Insufficient pairs are unavailable. A window whose highs are all equal and lows are all equal is explicitly flat. This fixed-window definition is a documented research convention, not a universal definition of market structure.

## Descriptive classification

| State     | Required evidence                                                                                                               |
| --------- | ------------------------------------------------------------------------------------------------------------------------------- |
| Uptrend   | Completed close > EMA20 > EMA50 > EMA200; all three five-bar EMA slopes > 0; ADX14 ≥ 20; confirmed higher highs and higher lows |
| Downtrend | Completed close < EMA20 < EMA50 < EMA200; all three slopes < 0; ADX14 ≥ 20; confirmed lower highs and lower lows                |
| Range     | ADX14 < 20; every five-bar EMA move within ±0.25%; max-minus-min EMA spread ≤ 1% of close; mixed or flat confirmed structure    |
| Unclear   | Missing warmup, missing structure, disagreement or a history that does not reach the latest expected completed daily close      |

These thresholds are deterministic conventions without validated predictive edge. A smooth trend with too few confirmed pivots can remain unclear. They do not generate entries, allocations or trades.

The service withholds classification when history misses the latest expected NYSE close or UTC daily close. The interface and report additionally withhold confirmation for stale/unavailable feeds. A common-close view cannot borrow evidence from a different closing timestamp; the drawer hides later indicators and newer technical evidence. The latest view labels the completed-bar timestamp separately from the quote timestamp.

## Verification

Tests cover analytic EMA slopes and endpoint warmup, exact equity/crypto windows, missing-bar resets, flat-market ATR/range, strict pivot ties, two-bar confirmation delay, evidence disagreement, stale sources, future/cutoff mismatches and legacy archives. Stored SPY and BTC data were also recalculated locally and inspected; this does not establish current API access. Public history may be revised, so these checks are not a historical-vintage or bias-free backtest guarantee.
