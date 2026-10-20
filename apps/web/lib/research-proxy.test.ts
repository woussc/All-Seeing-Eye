import test from "node:test";
import assert from "node:assert/strict";
import { researchProxy } from "./research-proxy";

test("research mutations reject missing or cross-site origins before contacting the backend", async () => {
  for (const origin of [undefined, "https://unrelated.example"]) {
    const request = new Request("http://127.0.0.1:3000/api/research/reports", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(origin ? { origin } : {}),
      },
      body: "{}",
    });
    assert.equal(
      (await researchProxy(request, "/reports", "POST")).status,
      403,
    );
  }
});
test("research mutations reject unsupported or oversized bodies", async () => {
  const request = (body: string, type: string) =>
    new Request("http://127.0.0.1:3000/api/research/reports", {
      method: "POST",
      headers: { origin: "http://127.0.0.1:3000", "content-type": type },
      body,
    });
  assert.equal(
    (await researchProxy(request("{}", "text/plain"), "/reports", "POST"))
      .status,
    415,
  );
  assert.equal(
    (
      await researchProxy(
        request(" ".repeat(1_000_001), "application/json"),
        "/reports",
        "POST",
      )
    ).status,
    413,
  );
});
test("same-origin request forwards JSON and preserves backend validation errors", async (t) => {
  let sent: string | undefined;
  t.mock.method(
    globalThis,
    "fetch",
    async (_url: unknown, init?: RequestInit) => {
      sent = init?.body as string;
      return Response.json({ detail: "Incomplete period" }, { status: 422 });
    },
  );
  const request = new Request(
    "http://127.0.0.1:3000/api/research/flows/import",
    {
      method: "POST",
      headers: {
        origin: "http://127.0.0.1:3000",
        "content-type": "application/json",
      },
      body: '{"commit":false}',
    },
  );
  const response = await researchProxy(request, "/flows/import", "POST");
  assert.equal(sent, '{"commit":false}');
  assert.equal(response.status, 422);
  assert.equal((await response.json()).error, "Incomplete period");
});

test("bind-host mismatch accepts a loopback Host and rejects an arbitrary remote Host", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json({ ok: true }));
  const request = (host: string, origin: string) =>
    new Request("http://0.0.0.0:3000/api/research/reports", {
      method: "POST",
      headers: { host, origin, "content-type": "application/json" },
      body: "{}",
    });
  assert.equal(
    (
      await researchProxy(
        request("127.0.0.1:3000", "http://127.0.0.1:3000"),
        "/reports",
        "POST",
      )
    ).status,
    200,
  );
  assert.equal(
    (
      await researchProxy(
        request("remote.example", "http://remote.example"),
        "/reports",
        "POST",
      )
    ).status,
    403,
  );
});
test("configured private origin supports a TLS proxy and rejects other origins", async (t) => {
  const previous = process.env.DASHBOARD_ORIGIN;
  t.after(() => {
    if (previous === undefined) delete process.env.DASHBOARD_ORIGIN;
    else process.env.DASHBOARD_ORIGIN = previous;
  });
  process.env.DASHBOARD_ORIGIN = "https://private.example";
  t.mock.method(globalThis, "fetch", async () => Response.json({ ok: true }));
  const request = (origin: string) =>
    new Request("http://0.0.0.0:3000/api/research/reports", {
      method: "POST",
      headers: { origin, "content-type": "application/json" },
      body: "{}",
    });
  assert.equal(
    (
      await researchProxy(
        request("https://private.example"),
        "/reports",
        "POST",
      )
    ).status,
    200,
  );
  assert.equal(
    (
      await researchProxy(
        request("https://unrelated.example"),
        "/reports",
        "POST",
      )
    ).status,
    403,
  );
});
