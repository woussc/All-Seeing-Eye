import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { demo } from "./demo";
import {
  fetchPublication,
  isManifest,
  isPublication,
  RefreshGate,
} from "./publication";
import type { Publication } from "../../../contracts/publication";

const sha = (s: string) => createHash("sha256").update(s).digest("hex");
function fixture() {
  const snapshot = { ...structuredClone(demo), mode: "connected" };
  const publication: Publication = {
    version: 2,
    generatedAt: demo.generatedAt,
    cadenceSeconds: 86400,
    overdueAfterSeconds: 129600,
    schedule: "17 0 * * *",
    expectedCadence: "Daily",
    availableAssets: 12,
    freshAssetsAtCapture: 0,
    reportCount: 1,
    retentionLimit: 30,
    quota: null,
    changes: [],
  };
  const snap = JSON.stringify(snapshot),
    pub = JSON.stringify(publication),
    generation = sha(snap + pub);
  return {
    snapshot,
    publication,
    snap,
    pub,
    manifest: {
      version: 1,
      generatedAt: demo.generatedAt,
      generation,
      snapshotPath: `generations/${generation}/snapshot.json`,
      snapshotSha256: sha(snap),
      publicationPath: `generations/${generation}/publication.json`,
      publicationSha256: sha(pub),
      pollAfterSeconds: 60,
    },
  };
}

test("manifest accepts only bound generation paths and bounded poll intervals", () => {
  const { manifest } = fixture();
  assert.ok(isManifest(manifest));
  for (const change of [
    { snapshotPath: "../private.json" },
    { publicationPath: "https://other.example/data.json" },
    { pollAfterSeconds: 1 },
    { generation: "X".repeat(64) },
  ])
    assert.equal(isManifest({ ...manifest, ...change }), false);
});

test("publication rejects impossible coverage, future changes and fake zero accounting", () => {
  const { publication } = fixture();
  assert.ok(isPublication(publication));
  assert.equal(
    isPublication({ ...publication, freshAssetsAtCapture: 13 }),
    false,
  );
  assert.equal(
    isPublication({
      ...publication,
      quota: { state: "unavailable", globalUsed: 0 },
    }),
    false,
  );
  assert.equal(
    isPublication({
      ...publication,
      changes: [
        {
          kind: "price",
          label: "Fixture",
          before: "0",
          after: "1",
          asOf: "2099-01-01T00:00:00Z",
        },
      ],
    }),
    false,
  );
});

test("new generation validates both payload hashes and unchanged generation costs one manifest request", async () => {
  const f = fixture(),
    requests: string[] = [];
  const fetcher = (async (url: string | URL | Request) => {
    const path = String(url);
    requests.push(path);
    return new Response(
      path.endsWith("feed.json")
        ? JSON.stringify(f.manifest)
        : path.endsWith("snapshot.json")
          ? f.snap
          : f.pub,
    );
  }) as typeof fetch;
  const result = await fetchPublication(
    null,
    new AbortController().signal,
    fetcher,
  );
  assert.deepEqual(result.snapshot, f.snapshot);
  assert.deepEqual(result.publication, f.publication);
  assert.equal(requests.length, 3);
  requests.length = 0;
  const unchanged = await fetchPublication(
    f.manifest,
    new AbortController().signal,
    fetcher,
  );
  assert.equal(unchanged.snapshot, undefined);
  assert.deepEqual(requests, ["/data/feed.json"]);
});

test("tampered files and mixed capture times fail before being displayed", async () => {
  for (const tamper of [true, false]) {
    const f = fixture();
    const wrong = JSON.stringify({
      ...f.publication,
      generatedAt: "2026-10-15T20:00:00Z",
    });
    if (!tamper) f.manifest.publicationSha256 = sha(wrong);
    const fetcher = (async (url: string | URL | Request) =>
      new Response(
        String(url).endsWith("feed.json")
          ? JSON.stringify(f.manifest)
          : String(url).endsWith("snapshot.json")
            ? f.snap
            : wrong,
      )) as typeof fetch;
    await assert.rejects(
      fetchPublication(null, new AbortController().signal, fetcher),
      tamper ? /integrity/ : /one valid generation/,
    );
  }
});

test("oversized manifest and missing generation fail with no fallback", async () => {
  const large = (async () => new Response(" ".repeat(16001))) as typeof fetch;
  await assert.rejects(
    fetchPublication(null, new AbortController().signal, large),
    /size limit/,
  );
  const f = fixture();
  const missing = (async (url: string | URL | Request) =>
    String(url).endsWith("feed.json")
      ? new Response(JSON.stringify(f.manifest))
      : new Response("Missing", { status: 404 })) as typeof fetch;
  await assert.rejects(
    fetchPublication(null, new AbortController().signal, missing),
    /unavailable/,
  );
});

test("manual refresh cannot bypass active requests, hidden/offline checks or cooldown", () => {
  const gate = new RefreshGate();
  assert.equal(gate.begin(0, false, true), false);
  assert.equal(gate.begin(0, true, false), false);
  assert.equal(gate.begin(0, true, true), true);
  assert.equal(gate.begin(60001, true, true), false); // still in-flight
  gate.finish(1000, true);
  assert.equal(gate.begin(60000, true, true), false);
  assert.equal(gate.begin(61000, true, true), true);
  gate.cancel();
  assert.equal(gate.begin(61001, true, true), false);
});

test("failure backoff is bounded and only success restores normal cadence", () => {
  const gate = new RefreshGate();
  assert.ok(gate.begin(0, true, true));
  gate.finish(1, false);
  assert.equal(gate.nextAt(), 120001);
  assert.ok(gate.begin(120001, true, true));
  gate.finish(120002, false);
  assert.equal(gate.nextAt(), 360002);
  assert.ok(gate.begin(360002, true, true));
  gate.finish(360003, false);
  assert.equal(gate.nextAt(), 660003);
  assert.ok(gate.begin(660003, true, true));
  gate.finish(660004, true);
  assert.equal(gate.nextAt(), 720004);
});

test("an older CDN manifest cannot replace a newer capture or rewrite an unchanged generation time", async () => {
  const f = fixture();
  const fetcher = (async () =>
    new Response(JSON.stringify(f.manifest))) as typeof fetch;
  await assert.rejects(
    fetchPublication(
      { generation: "a".repeat(64), generatedAt: "2026-10-17T20:00:00Z" },
      new AbortController().signal,
      fetcher,
    ),
    /older or inconsistent/,
  );
  await assert.rejects(
    fetchPublication(
      {
        generation: f.manifest.generation,
        generatedAt: "2026-10-15T20:00:00Z",
      },
      new AbortController().signal,
      fetcher,
    ),
    /older or inconsistent/,
  );
});
