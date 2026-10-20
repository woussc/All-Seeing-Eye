import type {
  Asset,
  AssetGroup,
  Quote,
  Snapshot,
  TechnicalEvidence,
} from "../../../contracts/market";
import { isContext } from "./context";
export type {
  Asset,
  AssetGroup,
  Quote,
  Snapshot,
  Source,
} from "../../../contracts/market";
export type View = "latest" | "close";
export const GROUPS: AssetGroup[] = [
  "Equities",
  "Rates & credit",
  "Gold",
  "Crypto",
  "Cash",
];
export const SYMBOLS = [
  "SPY",
  "QQQ",
  "IWM",
  "RSP",
  "IEF",
  "TLT",
  "LQD",
  "HYG",
  "GLD",
  "BTC",
  "ETH",
  "SGOV",
];
export function quoteFor(asset: Asset, view: View): Quote {
  return view === "latest"
    ? asset
    : (asset.commonClose ?? {
        price: null,
        change1d: null,
        change1w: null,
        asOf: null,
      });
}
export const percent = (v: number | null, digits = 2) =>
  v == null
    ? "—"
    : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(digits)}%`;
export const price = (v: number | null) =>
  v == null
    ? "—"
    : new Intl.NumberFormat("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(v);
export const stamp = (v: string | null, zone = "Asia/Singapore") =>
  v == null
    ? "Not available"
    : new Intl.DateTimeFormat("en-GB", {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: zone,
        hour12: false,
      }).format(new Date(v));
export function condition(a: Asset, view: View): string {
  const q = quoteFor(a, view);
  if (q.price == null) return "Unavailable";
  const completed = a.history.filter(
    (p) => view === "latest" || (q.asOf != null && p.time <= q.asOf),
  );
  const basis = completed.at(-1)?.value;
  if (basis == null) return "Warming up";
  // EMAs and the compared close share the adjusted, completed-bar price basis.
  // A historical view must not borrow indicators calculated after its cutoff.
  if (completed.length !== a.history.length) return "Snapshot";
  if (a.status === "stale" || a.status === "unavailable") return "Unclear";
  const technicals = technicalsFor(a, view);
  return technicals
    ? {
        uptrend: "Uptrend",
        downtrend: "Downtrend",
        range: "Range",
        unclear: "Unclear",
      }[technicals.classification]
    : "Unclear";
}
export function technicalsFor(a: Asset, view: View): TechnicalEvidence | null {
  const t = a.technicals;
  if (!t || t.asOf !== a.history.at(-1)?.time) return null;
  if (view === "close" && t.asOf !== a.commonClose?.asOf) return null;
  return t;
}

function isTechnicals(
  t: TechnicalEvidence | null | undefined,
  a: Asset,
  generatedAt: string,
): boolean {
  if (t == null) return true; // Legacy archives remain readable.
  const finite = (v: unknown) =>
    v === null || (typeof v === "number" && Number.isFinite(v));
  const time = (v: unknown): v is string =>
    typeof v === "string" &&
    /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(v) &&
    Number.isFinite(Date.parse(v));
  const crypto = a.group === "Crypto";
  const s = t.structure;
  return (
    t.methodVersion === "trend-v1" &&
    time(t.asOf) &&
    t.asOf === a.history.at(-1)?.time &&
    Date.parse(t.asOf) <= Date.parse(generatedAt) &&
    t.basis ===
      (crypto ? "venue-daily-bars" : "provider-adjusted-daily-bars") &&
    t.windowUnit === (crypto ? "calendar-days" : "sessions") &&
    [
      t.return4w,
      t.return13w,
      t.ema20Slope5Pct,
      t.ema50Slope5Pct,
      t.ema200Slope5Pct,
      t.atr14Pct,
    ].every(finite) &&
    (t.atr14Pct === null || t.atr14Pct >= 0) &&
    ["uptrend", "downtrend", "range", "unclear"].includes(t.classification) &&
    typeof t.reason === "string" &&
    !!s &&
    [
      "higher-highs-higher-lows",
      "lower-highs-lower-lows",
      "mixed",
      "flat",
      "unavailable",
    ].includes(s.state) &&
    Number.isInteger(s.lookbackBars) &&
    s.lookbackBars >= 1 &&
    s.lookbackBars <= 126 &&
    s.confirmationBars === 2 &&
    [s.highs, s.lows].every(
      (points) =>
        Array.isArray(points) &&
        points.length <= 2 &&
        points.every(
          (p, i) =>
            !!p &&
            typeof p === "object" &&
            time(p.time) &&
            time(p.confirmedAt) &&
            typeof p.value === "number" &&
            Number.isFinite(p.value) &&
            p.value > 0 &&
            Date.parse(p.time) < Date.parse(p.confirmedAt) &&
            Date.parse(p.confirmedAt) <= Date.parse(t.asOf) &&
            (i === 0 || Date.parse(points[i - 1].time) < Date.parse(p.time)),
        ),
    )
  );
}
export function usableAssets(snapshot: Snapshot, view: View) {
  return snapshot.assets.filter((a) => quoteFor(a, view).price != null);
}
export function headline(snapshot: Snapshot, view: View) {
  const assets = usableAssets(snapshot, view).filter(
    (a) => quoteFor(a, view).change1d !== null,
  );
  if (!assets.length) return "Waiting for verified market observations.";
  const advancing = assets.filter(
    (a) => quoteFor(a, view).change1d! > 0,
  ).length;
  return `${advancing} of ${assets.length} available assets advanced in their reported daily window.`;
}
/** Align on actual timestamps, not row index; gaps remain absent. */
export function ratioHistory(a: Asset | undefined, b: Asset | undefined) {
  if (!a || !b) return [];
  const denominator = new Map(b.history.map((p) => [p.time, p.value]));
  return a.history.flatMap((p) => {
    const d = denominator.get(p.time);
    return d && d > 0 ? [{ time: p.time, value: p.value / d }] : [];
  });
}
export function isSnapshot(v: unknown): v is Snapshot {
  if (!v || typeof v !== "object") return false;
  const s = v as Snapshot;
  const object = (x: unknown) =>
    x !== null && typeof x === "object" && !Array.isArray(x);
  const time = (x: unknown) =>
    typeof x === "string" && Number.isFinite(Date.parse(x));
  const num = (x: unknown) =>
    x === null || (typeof x === "number" && Number.isFinite(x));
  const quote = (q: Quote) =>
    q &&
    num(q.price) &&
    (q.price === null || q.price > 0) &&
    num(q.change1d) &&
    num(q.change1w) &&
    (q.asOf === null || time(q.asOf)) &&
    (q.price === null || time(q.asOf));
  const status = (x: string) =>
    ["demo", "live", "delayed", "stale", "unavailable"].includes(x);
  return (
    (s.context === undefined || s.context === null || isContext(s.context)) &&
    typeof s.id === "string" &&
    ["demo", "connected"].includes(s.mode) &&
    time(s.generatedAt) &&
    (s.commonCloseAt === null || time(s.commonCloseAt)) &&
    Array.isArray(s.assets) &&
    s.assets.length === 12 &&
    s.assets.every(object) &&
    new Set(s.assets.map((a) => a.symbol)).size === 12 &&
    s.assets.every(
      (a) =>
        SYMBOLS.includes(a.symbol) &&
        GROUPS.includes(a.group) &&
        typeof a.name === "string" &&
        typeof a.note === "string" &&
        typeof a.sourceId === "string" &&
        status(a.status) &&
        quote(a) &&
        (a.commonClose === null || quote(a.commonClose)) &&
        Array.isArray(a.history) &&
        a.history.every(
          (p, i, points) =>
            object(p) &&
            time(p.time) &&
            (i === 0 || Date.parse(p.time) > Date.parse(points[i - 1].time)) &&
            typeof p.value === "number" &&
            Number.isFinite(p.value) &&
            p.value > 0,
        ) &&
        a.indicators &&
        [
          "ema20",
          "ema50",
          "ema200",
          "rsi14",
          "adx14",
          "atr14",
          "relativeVolume",
          "volatility",
        ].every((k) => num(a.indicators[k as keyof Asset["indicators"]])) &&
        isTechnicals(a.technicals, a, s.generatedAt),
    ) &&
    Array.isArray(s.sources) &&
    s.sources.every(object) &&
    s.assets.every((a) =>
      s.sources.some((source) => source.id === a.sourceId),
    ) &&
    s.sources.every(
      (s) =>
        typeof s.id === "string" &&
        typeof s.name === "string" &&
        status(s.status) &&
        typeof s.detail === "string" &&
        typeof s.cadence === "string" &&
        typeof s.url === "string" &&
        (s.lastObservation === null || time(s.lastObservation)) &&
        (s.lastFetch === null || time(s.lastFetch)),
    )
  );
}
