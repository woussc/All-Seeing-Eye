import type { Snapshot } from "../../../contracts/market";

export const IS_PAGES = process.env.NEXT_PUBLIC_DEPLOYMENT === "pages";
export const sitePath = (path: string) =>
  `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}${path}`;
export const marketPath = () =>
  sitePath(IS_PAGES ? "/data/snapshot.json" : "/api/market");

export function researchPath(path: string, mutation = false) {
  if (!IS_PAGES) return sitePath(`/api/research/${path}`);
  if (mutation)
    throw Error(
      "Published snapshots are read-only. Save a copy in your browser instead.",
    );
  if (!/^reports(?:\/[a-f0-9]{24})?$/.test(path))
    throw Error("This action is unavailable on the published dashboard.");
  return sitePath(`/data/${path}.json`);
}

/** Publication age is separate from observation age; never upgrade a source. */
export function publicationOverdue(snapshot: Snapshot, now = Date.now()) {
  return now - Date.parse(snapshot.generatedAt) > 36 * 60 * 60 * 1000;
}

export function agePublishedSnapshot(
  snapshot: Snapshot,
  now = Date.now(),
): Snapshot {
  if (snapshot.mode !== "connected" || !publicationOverdue(snapshot, now))
    return snapshot;
  return {
    ...snapshot,
    assets: snapshot.assets.map((a) => ({
      ...a,
      status: a.price === null ? "unavailable" : "stale",
    })),
    sources: snapshot.sources.map((s) => ({
      ...s,
      status: s.lastObservation === null ? "unavailable" : "stale",
    })),
    context: snapshot.context && {
      ...snapshot.context,
      macro: {
        ...snapshot.context.macro,
        series: snapshot.context.macro.series.map((s) => ({
          ...s,
          status: s.value === null ? "unavailable" : "stale",
        })),
      },
      alerts: snapshot.context.alerts.map((a) => ({
        ...a,
        state: a.severity === "watch" ? "suppressed" : a.state,
        detail:
          a.severity === "watch"
            ? "The published snapshot is overdue. Refreshing this page does not refresh the source data."
            : a.detail,
      })),
    },
  };
}
