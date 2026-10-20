import type { MarketContext, ReportSummary } from "../../../contracts/context";

/** API yield changes use percentage points; the monitor displays basis points. */
export const yieldChange = (
  change: number | null,
  unit: "percent" | "basis-points",
) => {
  if (change === null) return "No comparison";
  const basisPoints = change * (unit === "percent" ? 100 : 1);
  return `${basisPoints > 0 ? "+" : ""}${basisPoints.toFixed(2)} bp / prior observation`;
};

const object = (x: unknown): x is Record<string, unknown> =>
  !!x && typeof x === "object" && !Array.isArray(x);
const text = (x: unknown): x is string => typeof x === "string";
const number = (x: unknown) =>
  x === null || (typeof x === "number" && Number.isFinite(x));
const time = (x: unknown) =>
  x === null ||
  (text(x) &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/.test(
      x,
    ) &&
    Number.isFinite(Date.parse(x)));
const day = (x: unknown) =>
  x === null ||
  (text(x) &&
    /^\d{4}-\d{2}-\d{2}$/.test(x) &&
    Number.isFinite(Date.parse(x)) &&
    new Date(x).toISOString().slice(0, 10) === x);
const integer = (x: unknown, min = 0, max = Number.MAX_SAFE_INTEGER) =>
  typeof x === "number" && Number.isInteger(x) && x >= min && x <= max;
const strings = (x: unknown) => Array.isArray(x) && x.every(text);
const source = (x: unknown) => {
  if (!text(x)) return false;
  try {
    const url = new URL(x);
    return (
      url.protocol === "https:" &&
      !!url.hostname &&
      !url.username &&
      !url.password
    );
  } catch {
    return false;
  }
};
export function isContext(value: unknown): value is MarketContext {
  if (
    !object(value) ||
    !text(value.generatedAt) ||
    !time(value.generatedAt) ||
    !object(value.macro) ||
    !object(value.flows)
  )
    return false;
  const { macro, flows } = value;
  return (
    text(macro.generatedAt) &&
    time(macro.generatedAt) &&
    Array.isArray(macro.series) &&
    macro.series.length <= 20 &&
    new Set(macro.series.filter(object).map((s) => s.id)).size ===
      macro.series.length &&
    macro.series.every(
      (s) =>
        object(s) &&
        text(s.id) &&
        text(s.label) &&
        ["percent", "basis-points"].includes(String(s.unit)) &&
        [s.value, s.previous, s.change].every(number) &&
        [s.observedOn, s.previousOn].every(day) &&
        [s.publishedAt, s.fetchedAt].every(time) &&
        ["delayed", "stale", "unavailable"].includes(String(s.status)) &&
        source(s.sourceUrl) &&
        text(s.detail) &&
        Array.isArray(s.history) &&
        s.history.length <= 260 &&
        s.history.every(
          (p, i, history) =>
            object(p) &&
            text(p.date) &&
            day(p.date) &&
            (i === 0 ||
              (object(history[i - 1]) &&
                String(history[i - 1].date) < p.date)) &&
            typeof p.value === "number" &&
            Number.isFinite(p.value),
        ) &&
        (s.value === null
          ? s.status === "unavailable" &&
            s.observedOn === null &&
            s.history.length === 0
          : s.status !== "unavailable" &&
            text(s.observedOn) &&
            text(s.fetchedAt) &&
            s.history.length > 0 &&
            s.history.at(-1)?.date === s.observedOn &&
            s.history.at(-1)?.value === s.value),
    ) &&
    typeof flows.recordCount === "number" &&
    Number.isInteger(flows.recordCount) &&
    flows.recordCount >= 0 &&
    Array.isArray(flows.series) &&
    flows.series.length <= 100 &&
    flows.recordCount >= flows.series.length &&
    new Set(flows.series.filter(object).map((s) => s.id)).size ===
      flows.series.length &&
    flows.series.every(
      (s) =>
        object(s) &&
        [s.id, s.label, s.scope, s.sourceName, s.rightsNote].every(text) &&
        source(s.sourceUrl) &&
        text(s.latestPublishedAt) &&
        time(s.latestPublishedAt) &&
        text(s.importedAt) &&
        time(s.importedAt) &&
        [
          "named-etf",
          "bitcoin-etfs",
          "ethereum-etfs",
          "gold-etfs",
          "fund-category",
        ].includes(String(s.category)) &&
        integer(s.revision, 1, 100000) &&
        strings(s.constituents) &&
        Array.isArray(s.windows) &&
        s.windows.length === 3 &&
        new Set(s.windows.filter(object).map((w) => w.weeks)).size === 3 &&
        s.windows.every(
          (w) =>
            object(w) &&
            [1, 4, 13].includes(w.weeks as number) &&
            day(w.periodStart) &&
            day(w.periodEnd) &&
            ((w.periodStart === null && w.periodEnd === null) ||
              (text(w.periodStart) &&
                text(w.periodEnd) &&
                (Date.parse(w.periodEnd) - Date.parse(w.periodStart)) /
                  86400000 ===
                  7 * (w.weeks as number) - 1)) &&
            number(w.netFlowUsd) &&
            number(w.normalizedPct) &&
            integer(w.coveredPeriods, 0, w.weeks as number) &&
            w.expectedPeriods === w.weeks &&
            ["complete", "incomplete", "stale", "unavailable"].includes(
              String(w.status),
            ) &&
            text(w.reason) &&
            strings(w.recordIds) &&
            w.recordIds.length <= (w.weeks as number) &&
            (["complete", "stale"].includes(String(w.status))
              ? w.netFlowUsd !== null &&
                w.coveredPeriods === w.weeks &&
                w.recordIds.length === w.weeks &&
                text(w.periodEnd)
              : w.netFlowUsd === null && w.normalizedPct === null),
        ),
    ) &&
    Array.isArray(value.alerts) &&
    value.alerts.length <= 30 &&
    new Set(value.alerts.filter(object).map((a) => a.id)).size ===
      value.alerts.length &&
    value.alerts.every(
      (a) =>
        object(a) &&
        [a.id, a.ruleId, a.title, a.detail].every(text) &&
        ["watch", "data"].includes(String(a.severity)) &&
        ["active", "resolved", "suppressed"].includes(String(a.state)) &&
        [a.firstSeen, a.lastSeen, a.changedAt].every(text) &&
        [a.firstSeen, a.lastSeen, a.changedAt, a.acknowledgedAt].every(time) &&
        integer(a.transitions, 1) &&
        Array.isArray(a.evidence) &&
        a.evidence.every(
          (e) => object(e) && [e.label, e.value, e.recordId].every(text),
        ),
    )
  );
}
export function isReportSummary(x: unknown): x is ReportSummary {
  return (
    object(x) &&
    [x.id, x.reportDate, x.snapshotId, x.digest, x.title].every(text) &&
    /^[a-f0-9]{24}$/.test(String(x.id)) &&
    /^[a-f0-9]{64}$/.test(String(x.digest)) &&
    day(x.reportDate) &&
    text(x.createdAt) &&
    time(x.createdAt) &&
    ["manual", "daily"].includes(String(x.kind)) &&
    (x.previousId === null ||
      (text(x.previousId) && /^[a-f0-9]{24}$/.test(x.previousId)))
  );
}
export const dollars = (v: number | null) =>
  v === null
    ? "—"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        notation: "compact",
        maximumFractionDigits: 2,
        signDisplay: "exceptZero",
      }).format(v);
export const dateOnly = (v: string | null) =>
  v === null
    ? "Unavailable"
    : new Intl.DateTimeFormat("en-GB", {
        day: "2-digit",
        month: "short",
        year: "numeric",
        timeZone: "UTC",
      }).format(new Date(v));
