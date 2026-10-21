import type {
  FeedManifest,
  Publication,
  QuotaStatus,
} from "../../../contracts/publication";
import { isSnapshot, type Snapshot } from "./market";
import { sitePath } from "./deployment";

const obj = (x: unknown): x is Record<string, unknown> =>
  !!x && typeof x === "object" && !Array.isArray(x);
const integer = (x: unknown, min = 0, max = 1000000): x is number =>
  typeof x === "number" && Number.isInteger(x) && x >= min && x <= max;
const time = (x: unknown): x is string =>
  typeof x === "string" &&
  /^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(x) &&
  Number.isFinite(Date.parse(x));
const nullableTime = (x: unknown) => x === null || time(x);
const hash = (x: unknown): x is string =>
  typeof x === "string" && /^[a-f0-9]{64}$/.test(x);
const text = (x: unknown): x is string =>
  typeof x === "string" && x.length <= 2000;

export function isManifest(x: unknown): x is FeedManifest {
  return (
    obj(x) &&
    x.version === 1 &&
    hash(x.generation) &&
    time(x.generatedAt) &&
    x.snapshotPath === `generations/${x.generation}/snapshot.json` &&
    x.publicationPath === `generations/${x.generation}/publication.json` &&
    hash(x.snapshotSha256) &&
    hash(x.publicationSha256) &&
    integer(x.pollAfterSeconds, 60, 300)
  );
}

function isQuota(x: unknown): x is QuotaStatus {
  if (
    !obj(x) ||
    !["available", "unavailable"].includes(String(x.state)) ||
    !time(x.asOf) ||
    !integer(x.globalLimit, 1, 100000) ||
    x.windowSeconds !== 86400 ||
    !(x.globalUsed === null || integer(x.globalUsed)) ||
    !Array.isArray(x.sources) ||
    x.sources.length !== 6
  )
    return false;
  const providers = ["yahoo", "coinbase", "treasury", "bls", "fred", "cftc"];
  return (
    new Set(x.sources.map((s) => (obj(s) ? s.id : null))).size === 6 &&
    (x.state !== "available" ||
      x.globalUsed ===
        x.sources.reduce(
          (n, s) => n + (obj(s) && typeof s.used === "number" ? s.used : 0),
          0,
        )) &&
    x.sources.every(
      (s) =>
        obj(s) &&
        providers.includes(String(s.id)) &&
        typeof s.enabled === "boolean" &&
        typeof s.verified === "boolean" &&
        typeof s.verificationRequired === "boolean" &&
        integer(s.limit, 1, 100000) &&
        (s.used === null || integer(s.used)) &&
        (s.remaining === null || integer(s.remaining)) &&
        (s.used === null
          ? s.remaining === null
          : s.remaining === Math.max(0, s.limit - s.used)) &&
        (x.state === "unavailable"
          ? x.globalUsed === null && s.used === null
          : x.globalUsed !== null && s.used !== null) &&
        nullableTime(s.nextCapacityAt) &&
        nullableTime(s.blockedUntil),
    )
  );
}

export function isPublication(x: unknown): x is Publication {
  return (
    obj(x) &&
    x.version === 2 &&
    time(x.generatedAt) &&
    integer(x.cadenceSeconds, 60, 86400 * 7) &&
    integer(x.overdueAfterSeconds, x.cadenceSeconds as number, 86400 * 14) &&
    text(x.schedule) &&
    text(x.expectedCadence) &&
    integer(x.availableAssets, 0, 12) &&
    integer(x.freshAssetsAtCapture, 0, x.availableAssets as number) &&
    integer(x.reportCount, 0, 30) &&
    x.retentionLimit === 30 &&
    (x.quota === null || isQuota(x.quota)) &&
    Array.isArray(x.changes) &&
    x.changes.length <= 30 &&
    x.changes.every(
      (c) =>
        obj(c) &&
        ["price", "health", "macro"].includes(String(c.kind)) &&
        text(c.label) &&
        text(c.before) &&
        text(c.after) &&
        time(c.asOf) &&
        Date.parse(c.asOf) <= Date.parse(x.generatedAt as string),
    )
  );
}

async function readBounded(response: Response, max: number) {
  if (!response.ok || !response.body)
    throw Error("Published data is unavailable.");
  const reader = response.body.getReader(),
    parts: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > max) throw Error("Published data exceeds its size limit.");
      parts.push(value);
    }
  } finally {
    await reader.cancel();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const part of parts) {
    bytes.set(part, offset);
    offset += part.byteLength;
  }
  return bytes;
}
async function verifiedJson(response: Response, expected: string, max: number) {
  const bytes = await readBounded(response, max);
  const digest = Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
  )
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  if (digest !== expected)
    throw Error(
      "Published generation failed its integrity check. The previous view is retained.",
    );
  return JSON.parse(new TextDecoder().decode(bytes)) as unknown;
}

export async function fetchPublication(
  known: Pick<FeedManifest, "generation" | "generatedAt"> | null,
  signal: AbortSignal,
  fetcher = fetch,
): Promise<{
  manifest: FeedManifest;
  snapshot?: Snapshot;
  publication?: Publication;
}> {
  const options: RequestInit = { signal, cache: "no-cache" };
  const raw: unknown = JSON.parse(
    new TextDecoder().decode(
      await readBounded(
        await fetcher(sitePath("/data/feed.json"), options),
        16000,
      ),
    ),
  );
  if (!isManifest(raw) || Date.parse(raw.generatedAt) > Date.now() + 5000)
    throw Error("The publication manifest could not be validated.");
  if (
    known &&
    (Date.parse(raw.generatedAt) < Date.parse(known.generatedAt) ||
      (raw.generation === known.generation &&
        raw.generatedAt !== known.generatedAt))
  )
    throw Error(
      "An older or inconsistent publication was received. The newer view is retained.",
    );
  if (raw.generation === known?.generation) return { manifest: raw };
  const [snapshot, publication] = await Promise.all([
    fetcher(sitePath(`/data/${raw.snapshotPath}`), options).then((r) =>
      verifiedJson(r, raw.snapshotSha256, 5000000),
    ),
    fetcher(sitePath(`/data/${raw.publicationPath}`), options).then((r) =>
      verifiedJson(r, raw.publicationSha256, 100000),
    ),
  ]);
  if (
    !isSnapshot(snapshot) ||
    !isPublication(publication) ||
    snapshot.generatedAt !== raw.generatedAt ||
    publication.generatedAt !== raw.generatedAt
  )
    throw Error("Published files do not describe one valid generation.");
  return { manifest: raw, snapshot, publication };
}

/** Manual and automatic checks share one cooldown; only success resets backoff. */
export class RefreshGate {
  private next = 0;
  private failures = 0;
  private busy = false;
  begin(now: number, visible: boolean, online: boolean) {
    if (this.busy || !visible || !online || now < this.next) return false;
    this.busy = true;
    this.next = now + 60000;
    return true;
  }
  finish(now: number, success: boolean) {
    this.busy = false;
    this.failures = success ? 0 : Math.min(this.failures + 1, 3);
    this.next = now + Math.min(60000 * 2 ** this.failures, 300000);
  }
  cancel() {
    this.busy = false;
  }
  nextAt() {
    return this.next;
  }
}
