import test from "node:test";
import assert from "node:assert/strict";
import { demo } from "./demo";
import type { TechnicalEvidence } from "../../../contracts/market";
import {
  condition,
  isSnapshot,
  percent,
  quoteFor,
  ratioHistory,
  technicalsFor,
} from "./market";

const technicals = (asOf: string): TechnicalEvidence => ({
  asOf,
  basis: "provider-adjusted-daily-bars",
  windowUnit: "sessions",
  return4w: 4,
  return13w: 13,
  ema20Slope5Pct: 1,
  ema50Slope5Pct: 0.5,
  ema200Slope5Pct: 0.2,
  atr14Pct: 2,
  structure: {
    state: "unavailable",
    highs: [],
    lows: [],
    lookbackBars: 1,
    confirmationBars: 2,
  },
  classification: "unclear",
  reason: "Test fixture",
  methodVersion: "trend-v1",
});

test("the demo is explicitly identified and validates against the live response contract", () => {
  assert.ok(isSnapshot(demo));
  assert.equal(demo.mode, "demo");
  assert.ok(
    demo.assets.every(
      (a) => a.status === "demo" && a.note.includes("Illustrative"),
    ),
  );
});
test("missing, zero and negative changes have distinct representations", () => {
  assert.equal(percent(null), "—");
  assert.equal(percent(0), "0.00%");
  assert.equal(percent(-1.25), "−1.25%");
  assert.equal(percent(1.25), "+1.25%");
});
test("a missing common-close mark never borrows the latest quote", () => {
  const a = { ...demo.assets[0], commonClose: null };
  assert.equal(quoteFor(a, "close").price, null);
  assert.equal(quoteFor(a, "latest").price, a.price);
});
test("EMA alignment alone cannot establish trend without the new evidence", () => {
  const a = {
    ...demo.assets[0],
    price: 1,
    history: [{ time: demo.generatedAt, value: 200 }],
    indicators: {
      ...demo.assets[0].indicators,
      ema20: 190,
      ema50: 180,
      ema200: 170,
    },
  };
  assert.equal(condition(a, "latest"), "Unclear");
  const withEvidence = {
    ...a,
    technicals: {
      ...technicals(demo.generatedAt),
      classification: "uptrend" as const,
    },
  };
  assert.equal(condition(withEvidence, "latest"), "Uptrend");
  assert.equal(
    condition({ ...withEvidence, status: "stale" }, "latest"),
    "Unclear",
  );
});

test("technical evidence respects the selected cutoff and older archives remain valid", () => {
  const a = {
    ...demo.assets[0],
    technicals: technicals(demo.assets[0].history.at(-1)!.time),
  };
  assert.equal(technicalsFor(a, "latest"), a.technicals);
  assert.equal(technicalsFor({ ...a, commonClose: null }, "close"), null);
  assert.equal(
    technicalsFor(
      { ...a, technicals: { ...a.technicals, asOf: demo.generatedAt } },
      "close",
    ),
    null,
  );
  assert.ok(isSnapshot(demo));
});

test("malformed technical units, future pivots and invalid numbers are rejected", () => {
  const value = structuredClone(demo);
  value.assets[0].technicals = technicals(value.assets[0].history.at(-1)!.time);
  assert.ok(isSnapshot(value));
  for (const change of [
    { return4w: NaN },
    { atr14Pct: -1 },
    { windowUnit: "calendar-days" },
    { methodVersion: "unverified" },
    {
      structure: {
        ...value.assets[0].technicals.structure,
        highs: [
          {
            time: "2026-01-01T00:00:00Z",
            value: 100,
            confirmedAt: "2099-01-01T00:00:00Z",
          },
        ],
      },
    },
  ]) {
    const invalid = structuredClone(value);
    Object.assign(invalid.assets[0].technicals!, change);
    assert.equal(isSnapshot(invalid), false);
  }
});
test("a historical close never borrows later computed indicators", () => {
  const a = {
    ...demo.assets[0],
    commonClose: { ...demo.assets[0], asOf: "2026-08-03T20:00:00Z" },
  };
  assert.equal(condition(a, "close"), "Snapshot");
});
test("ratio joins exact observations and excludes unmatched dates and zero denominators", () => {
  const a = {
    ...demo.assets[0],
    history: [
      { time: "2026-10-14T20:00:00Z", value: 5 },
      { time: "2026-10-15T20:00:00Z", value: 8 },
      { time: "2026-10-16T20:00:00Z", value: 12 },
    ],
  };
  const b = {
    ...demo.assets[1],
    history: [
      { time: "2026-10-14T00:00:00Z", value: 2 },
      { time: "2026-10-15T20:00:00Z", value: 4 },
      { time: "2026-10-16T20:00:00Z", value: 0 },
    ],
  };
  assert.deepEqual(ratioHistory(a, b), [
    { time: "2026-10-15T20:00:00Z", value: 2 },
  ]);
});
test("invalid API snapshots are rejected before rendering", () => {
  const cases = [
    { ...demo, assets: demo.assets.slice(1) },
    { ...demo, generatedAt: "invalid" },
    {
      ...demo,
      assets: demo.assets.map((a, i) => (i ? a : { ...a, price: NaN })),
    },
    {
      ...demo,
      assets: demo.assets.map((a, i) => (i ? a : { ...a, indicators: {} })),
    },
  ];
  for (const candidate of cases) assert.equal(isSnapshot(candidate), false);
});
test("demo daily and weekly endpoints reconcile to the displayed returns", () => {
  for (const a of demo.assets) {
    const end = a.history.at(-1)!.value;
    assert.ok(
      Math.abs((end / a.history.at(-2)!.value - 1) * 100 - a.change1d!) < 1e-9,
    );
    assert.ok(
      Math.abs((end / a.history.at(-6)!.value - 1) * 100 - a.change1w!) < 1e-9,
    );
  }
});
