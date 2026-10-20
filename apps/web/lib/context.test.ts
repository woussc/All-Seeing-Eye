import test from "node:test";
import assert from "node:assert/strict";
import { isContext, isReportSummary, dollars, yieldChange } from "./context";

test("yield and curve changes display in basis points without double conversion", () => {
  assert.equal(yieldChange(0.03, "percent"), "+3.00 bp / prior observation");
  assert.equal(yieldChange(3, "basis-points"), "+3.00 bp / prior observation");
  assert.equal(yieldChange(-0.06, "percent"), "-6.00 bp / prior observation");
  assert.equal(yieldChange(null, "percent"), "No comparison");
});
import type { MarketContext, FlowSeries } from "../../../contracts/context";

const time = "2026-10-19T12:00:00Z";
const series: FlowSeries = {
  id: "fixture",
  label: "Unit test only",
  category: "named-etf",
  scope: "Fixture",
  sourceName: "Fixture",
  sourceUrl: "https://example.com/test",
  rightsNote: "Synthetic test only",
  latestPublishedAt: time,
  importedAt: time,
  revision: 1,
  constituents: ["TEST"],
  windows: [1, 4, 13].map((weeks) => ({
    weeks: weeks as 1 | 4 | 13,
    periodStart:
      weeks === 1 ? "2026-10-10" : weeks === 4 ? "2026-09-19" : "2026-07-18",
    periodEnd: "2026-10-16",
    netFlowUsd: weeks === 1 ? 0 : null,
    normalizedPct: weeks === 1 ? 0 : null,
    coveredPeriods: 1,
    expectedPeriods: weeks,
    status: weeks === 1 ? "complete" : "incomplete",
    reason: "Fixture",
    recordIds: ["fixture-record"],
  })),
};
function fixture(): MarketContext {
  return {
    generatedAt: time,
    macro: {
      generatedAt: time,
      series: [
        {
          id: "treasury-2y",
          label: "2Y",
          unit: "percent",
          value: 4,
          previous: null,
          change: null,
          observedOn: "2026-10-16",
          previousOn: null,
          publishedAt: null,
          fetchedAt: time,
          status: "delayed",
          sourceUrl: "https://home.treasury.gov",
          detail: "Fixture",
          history: [{ date: "2026-10-16", value: 4 }],
        },
      ],
    },
    flows: { recordCount: 1, series: [structuredClone(series)] },
    alerts: [],
  };
}
test("context preserves true zero and accepts explicit incomplete windows", () => {
  assert.ok(isContext(fixture()));
  assert.equal(dollars(null), "—");
  assert.equal(dollars(0), "$0");
});
test("invalid context dates, history, coverage and duplicate windows are rejected", () => {
  const edits: ((v: MarketContext) => void)[] = [
    (v) => {
      v.macro.series[0].observedOn = "2026-02-31";
    },
    (v) => {
      v.macro.series[0].history.push({ ...v.macro.series[0].history[0] });
    },
    (v) => {
      v.macro.series[0].value = NaN;
    },
    (v) => {
      v.flows.series[0].windows[1].netFlowUsd = 0;
    },
    (v) => {
      v.flows.series[0].windows[0].coveredPeriods = 0;
    },
    (v) => {
      v.flows.series[0].windows[2] = v.flows.series[0].windows[1];
    },
    (v) => {
      v.flows.series[0].sourceUrl = "javascript:alert(1)";
    },
    (v) => {
      v.generatedAt = "2026-10-19T12:00:00";
    },
    (v) => {
      v.flows.series.push(v.flows.series[0]);
    },
  ];
  for (const edit of edits) {
    const v = fixture();
    edit(v);
    assert.equal(isContext(v), false);
  }
});
test("unavailable macro data cannot masquerade as an observed zero", () => {
  const v = fixture();
  v.macro.series[0].status = "unavailable";
  assert.equal(isContext(v), false);
  Object.assign(v.macro.series[0], {
    value: null,
    observedOn: null,
    fetchedAt: null,
    history: [],
  });
  assert.ok(isContext(v));
});
test("report index requires a valid identity, digest and timestamp", () => {
  const r = {
    id: "a".repeat(24),
    kind: "manual",
    reportDate: "2026-10-19",
    createdAt: time,
    snapshotId: "source",
    digest: "b".repeat(64),
    title: "Report",
    previousId: null,
  };
  assert.ok(isReportSummary(r));
  assert.equal(isReportSummary({ ...r, digest: "unknown" }), false);
  assert.equal(isReportSummary({ ...r, createdAt: null }), false);
  assert.equal(isReportSummary({ ...r, reportDate: "2026-02-31" }), false);
});
