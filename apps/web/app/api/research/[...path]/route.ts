import { NextResponse } from "next/server";
import { researchProxy } from "@/lib/research-proxy";
export const dynamic = "force-dynamic";
type Context = { params: Promise<{ path: string[] }> };
async function handle(
  request: Request,
  context: Context,
  method: "GET" | "POST",
) {
  const { path } = await context.params;
  const joined = path.join("/");
  const allowed =
    method === "GET"
      ? /^reports(?:\/[a-f0-9]{24})?$/.test(joined)
      : joined === "flows/import" ||
        joined === "reports" ||
        /^alerts\/[a-z-]+\/acknowledge$/.test(joined);
  if (!allowed)
    return NextResponse.json(
      { error: "Unknown research action." },
      { status: 404 },
    );
  return researchProxy(request, "/" + joined, method);
}
export const GET = (r: Request, c: Context) => handle(r, c, "GET");
export const POST = (r: Request, c: Context) => handle(r, c, "POST");
