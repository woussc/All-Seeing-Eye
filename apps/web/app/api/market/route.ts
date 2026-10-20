import { NextResponse } from "next/server";
import { isSnapshot } from "@/lib/market";
export const dynamic = "force-dynamic";
export async function GET() {
  const base = process.env.MARKET_SERVICE_URL ?? "http://127.0.0.1:8000";
  try {
    const response = await fetch(`${base}/snapshot`, {
      cache: "no-store",
      signal: AbortSignal.timeout(25000),
    });
    if (!response.ok)
      return NextResponse.json(
        { error: "The market service could not return a snapshot." },
        { status: 502 },
      );
    const data: unknown = await response.json();
    if (!isSnapshot(data))
      return NextResponse.json(
        { error: "The market snapshot failed validation." },
        { status: 502 },
      );
    return NextResponse.json(data, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch {
    return NextResponse.json(
      {
        error:
          "The market service is unavailable. The last displayed dataset has been preserved.",
      },
      { status: 503 },
    );
  }
}
