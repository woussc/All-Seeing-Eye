import test from "node:test";
import assert from "node:assert/strict";
import { agePublishedSnapshot, publicationOverdue } from "./deployment";
import { demo } from "./demo";
import type { Snapshot } from "./market";

test("publication age never refreshes prices or upgrades stale observations", () => {
  const s: Snapshot = structuredClone(demo);
  s.mode = "connected";
  s.assets.forEach((a) => {
    a.status = "delayed";
  });
  s.assets[0].status = "stale";
  const at = Date.parse(s.generatedAt);
  assert.equal(publicationOverdue(s, at + 36 * 3600000), false);
  assert.equal(agePublishedSnapshot(s, at + 3600000), s);
  const aged = agePublishedSnapshot(s, at + 37 * 3600000);
  assert.ok(aged.assets.every((a) => a.status === "stale"));
  assert.equal(aged.assets[1].price, s.assets[1].price);
  assert.equal(aged.generatedAt, s.generatedAt);
  assert.equal(s.assets[1].status, "delayed");
  assert.equal(agePublishedSnapshot(demo, at + 100 * 3600000), demo);
});

test("overdue publications suppress analytical watches without inventing transitions", () => {
  const s: Snapshot = {
    ...structuredClone(demo),
    mode: "connected",
    context: {
      generatedAt: demo.generatedAt,
      macro: { generatedAt: demo.generatedAt, series: [] },
      flows: { series: [], recordCount: 0 },
      alerts: [
        {
          id: "test",
          ruleId: "test",
          title: "Fixture",
          detail: "Previous observation",
          severity: "watch",
          state: "active",
          firstSeen: demo.generatedAt,
          lastSeen: demo.generatedAt,
          changedAt: demo.generatedAt,
          acknowledgedAt: null,
          evidence: [],
          transitions: 1,
        },
      ],
    },
  };
  const aged = agePublishedSnapshot(
    s,
    Date.parse(s.generatedAt) + 37 * 3600000,
  );
  assert.equal(aged.context?.alerts[0].state, "suppressed");
  assert.equal(aged.context?.alerts[0].transitions, 1);
  assert.equal(s.context?.alerts[0].state, "active");
});

test("a shorter verified publication cadence cannot inherit the legacy 36-hour grace", () => {
  const s: Snapshot = { ...structuredClone(demo), mode: "connected" };
  s.assets.forEach((a) => {
    a.status = "delayed";
  });
  const now = Date.parse(s.generatedAt) + 2 * 3600000;
  assert.equal(publicationOverdue(s, now), false);
  assert.equal(publicationOverdue(s, now, 5400), true);
  assert.ok(
    agePublishedSnapshot(s, now, 5400).assets.every(
      (a) => a.status === "stale",
    ),
  );
});
