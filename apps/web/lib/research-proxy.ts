import { NextResponse } from "next/server";

function allowedOrigin(request: Request): string | null {
  const configured = process.env.DASHBOARD_ORIGIN;
  if (configured) {
    try {
      const url = new URL(configured);
      return ["http:", "https:"].includes(url.protocol) &&
        configured === url.origin
        ? configured
        : null;
    } catch {
      return null;
    }
  }
  // Next can construct request.url with its bind hostname (0.0.0.0).
  // Remote origins require explicit configuration, not forwarding headers.
  try {
    const url = new URL(request.url);
    const host = request.headers.get("host");
    if (host) {
      if (/[\s\/@#?]/.test(host)) return null;
      url.host = host;
    }
    return ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
      ? url.origin
      : null;
  } catch {
    return null;
  }
}

export async function researchProxy(
  request: Request,
  path: string,
  method: "GET" | "POST",
) {
  if (method === "POST") {
    const origin = request.headers.get("origin");
    if (
      !origin ||
      origin !== allowedOrigin(request) ||
      request.headers.get("sec-fetch-site") === "cross-site"
    ) {
      return NextResponse.json(
        { error: "This action must originate from this dashboard." },
        { status: 403 },
      );
    }
  }
  const base = process.env.MARKET_SERVICE_URL ?? "http://127.0.0.1:8000";
  try {
    let body: string | undefined;
    if (method === "POST") {
      if (!request.headers.get("content-type")?.startsWith("application/json"))
        return NextResponse.json(
          { error: "Use a JSON request." },
          { status: 415 },
        );
      const reader = request.body?.getReader();
      const chunks: Uint8Array[] = [];
      let size = 0;
      if (reader)
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > 1_000_000) {
            await reader.cancel();
            return NextResponse.json(
              { error: "File exceeds the one-megabyte limit." },
              { status: 413 },
            );
          }
          chunks.push(value);
        }
      body = Buffer.concat(chunks).toString("utf8");
    }
    const response = await fetch(`${base}${path}`, {
      method,
      body,
      headers:
        method === "POST" ? { "Content-Type": "application/json" } : undefined,
      cache: "no-store",
      signal: AbortSignal.timeout(12000),
    });
    const data = await response.json();
    return NextResponse.json(
      response.ok
        ? data
        : {
            error:
              typeof data.detail === "string"
                ? data.detail
                : "The research service rejected this request.",
          },
      { status: response.status, headers: { "Cache-Control": "no-store" } },
    );
  } catch {
    return NextResponse.json(
      {
        error:
          "The research service is unavailable. Saved reports have not been changed.",
      },
      { status: 503 },
    );
  }
}
