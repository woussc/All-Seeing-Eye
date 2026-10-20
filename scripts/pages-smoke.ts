import { chromium } from "playwright";
import { createServer } from "node:http";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";
import { isSnapshot } from "../apps/web/lib/market";

async function run() {
  const root = path.resolve("apps/pages/out");
  const mount = process.env.PAGES_BASE_PATH ?? "/All-Seeing-Eye";
  const mime: Record<string, string> = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
  };
  const misses: string[] = [];
  const server = createServer(async (req, res) => {
    try {
      const pathname = decodeURIComponent(
        new URL(req.url!, "http://local").pathname,
      );
      if (req.method !== "GET" || !pathname.startsWith(mount + "/"))
        throw Error();
      const relative = pathname.slice(mount.length);
      const file = path.resolve(
        root,
        "." + relative + (relative.endsWith("/") ? "index.html" : ""),
      );
      if (!file.startsWith(root + path.sep)) throw Error();
      const data = await readFile(file);
      res.setHeader(
        "Content-Type",
        mime[path.extname(file)] ?? "application/octet-stream",
      );
      res.end(data);
    } catch {
      misses.push(req.url!);
      res.writeHead(404);
      res.end("Not found");
    }
  });
  server.listen(0, "127.0.0.1");
  await new Promise<void>((resolve) => server.once("listening", resolve));
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  const base = `http://127.0.0.1:${address.port}${mount}/`;
  const browser = await chromium.launch({
    executablePath:
      process.env.CHROME_PATH ?? path.resolve("scripts/chromium-local.sh"),
    headless: true,
  });
  const checks: string[] = [],
    errors: string[] = [],
    requests: string[] = [];
  try {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
      acceptDownloads: true,
    });
    const page = await context.newPage();
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("request", (r) => requests.push(r.url()));
    await page.goto(base, { waitUntil: "networkidle" });
    await page.getByText("Published snapshot", { exact: true }).waitFor();
    assert.equal(await page.locator(".asset-table tbody tr").count(), 12);
    assert.equal(
      await page
        .getByRole("button", { name: "Common close", exact: true })
        .getAttribute("aria-pressed"),
      "true",
    );
    checks.push(
      "Project-subpath static HTML, scripts, fonts and twelve-reference snapshot load",
    );
    await page.getByRole("textbox", { name: "Find an asset" }).fill("spy");
    assert.equal(await page.locator(".asset-table tbody tr").count(), 1);
    await page.getByRole("textbox", { name: "Find an asset" }).fill("");
    await page
      .getByRole("button", { name: "Inspect SPY", exact: true })
      .click();
    await page.getByRole("dialog").waitFor();
    await page.keyboard.press("Escape");
    checks.push("Search and evidence drawer work without a server API");
    await page.getByRole("button", { name: "Fund flows", exact: true }).click();
    assert.equal(
      await page.getByRole("button", { name: "Choose JSON file" }).count(),
      0,
    );
    await page.getByRole("button", { name: "Research", exact: true }).click();
    assert.equal(
      await page
        .getByRole("button", { name: "Acknowledge", exact: true })
        .count(),
      0,
    );
    checks.push(
      "Static publication removes server-only flow imports and acknowledgments",
    );
    await page
      .getByRole("button", { name: "Save snapshot", exact: true })
      .click();
    await page.getByRole("button", { name: "Journal", exact: true }).click();
    assert.equal(
      await page
        .getByRole("button", { name: "Archive source snapshot" })
        .count(),
      0,
    );
    await page.locator(".report-row").first().click();
    const downloadEvent = page.waitForEvent("download");
    await page.getByRole("button", { name: "Export monitor report" }).click();
    const download = await downloadEvent;
    const markdown = await readFile((await download.path())!, "utf8");
    assert.match(markdown, /Jev: disabled; connection approval pending/);
    assert.match(markdown, /Common price cutoff/);
    checks.push("Published report index, full report and Markdown export work");
    await page.reload({ waitUntil: "networkidle" });
    await page.getByRole("button", { name: "Journal", exact: true }).click();
    assert.equal(await page.locator(".journal-row").count(), 1);
    checks.push("Browser-local saved snapshot survives a reload");
    await mkdir(".runtime", { recursive: true });
    await page.getByRole("button", { name: "Overview", exact: true }).click();
    for (const width of [1440, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      assert.ok(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      );
      if (width !== 768)
        await page.screenshot({
          path: `.runtime/pages-${width}.png`,
          fullPage: true,
        });
    }
    checks.push("Published dashboard fits desktop, tablet and phone");
    const actual = JSON.parse(
      await readFile(path.join(root, "data/snapshot.json"), "utf8"),
    );
    assert.ok(isSnapshot(actual));
    const old = structuredClone(actual);
    // Keep a valid capture and advance the browser's clock, never rewrite history.
    await page.clock.install({
      time: new Date(Date.parse(old.generatedAt) + 40 * 3600000),
    });
    await page.reload({ waitUntil: "networkidle" });
    await page.getByText("Update overdue", { exact: false }).waitFor();
    assert.ok(
      !(await page.locator(".asset-table").innerText()).includes("Uptrend"),
    );
    checks.push(
      "Overdue publication is explicit and cannot imply current trend confirmation",
    );
    await page.clock.resume();
    await page.route("**/data/snapshot.json", (route) =>
      route.fulfill({ status: 503, body: "Unavailable" }),
    );
    await page.reload({ waitUntil: "networkidle" });
    await page
      .getByText("No sample prices are substituted.", { exact: false })
      .waitFor();
    assert.equal(await page.locator(".asset-table").count(), 0);
    checks.push(
      "Initial data failure never substitutes a synthetic market dashboard",
    );
    await page.unroute("**/data/snapshot.json");
    await page.goto(base + "?demo=1", { waitUntil: "networkidle" });
    await page.locator(".chart svg").waitFor();
    await page
      .getByText("Illustrative data for design evaluation.", { exact: false })
      .waitFor();
    checks.push(
      "Chart library loads under the project subpath in explicitly selected demo mode",
    );
    assert.deepEqual(errors, []);
    assert.deepEqual(misses, []);
    assert.ok(
      requests.every((url) => new URL(url).origin === new URL(base).origin),
    );
    assert.ok(
      requests.every((url) => !new URL(url).pathname.includes("/api/")),
    );
    checks.push(
      "No backend calls, third-party browser requests, missing assets or uncaught errors",
    );
    const result = {
      at: new Date().toISOString(),
      passed: checks.length,
      checks,
      errors,
    };
    await writeFile(
      ".runtime/pages-browser-checks.json",
      JSON.stringify(result, null, 2),
    );
    console.log(JSON.stringify(result, null, 2));
  } finally {
    await browser.close();
    server.close();
  }
}
run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
