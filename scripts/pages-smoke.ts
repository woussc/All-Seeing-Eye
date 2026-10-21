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
    await context.tracing.start({ screenshots: true, snapshots: true });
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
    const manifestCalls = () =>
      requests.filter((url) => url.endsWith("/data/feed.json")).length;
    const initialChecks = manifestCalls();
    for (let i = 0; i < 3; i++)
      await page
        .getByRole("button", { name: "Check published update", exact: true })
        .click();
    assert.equal(manifestCalls(), initialChecks);
    await page.getByRole("button", { name: "Sources", exact: true }).click();
    await page
      .getByRole("heading", { name: "Collection budget", exact: true })
      .waitFor();
    assert.equal(await page.locator(".budget-source").count(), 6);
    assert.match(await page.locator(".feed-evidence").innerText(), /Unknown/);
    await page.getByRole("button", { name: "Overview", exact: true }).click();
    checks.push(
      "Manual refresh shares a cooldown; captured source budgets preserve unknown accounting",
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
    const exportButton = page.getByRole("button", {
      name: "Export monitor report",
      exact: true,
    });
    await exportButton.waitFor({ state: "visible" });
    let download;
    try {
      [download] = await Promise.all([
        page.waitForEvent("download"),
        exportButton.click({ timeout: 10000 }),
      ]);
    } catch (error) {
      await page.screenshot({
        path: ".runtime/pages-export-failure.png",
        fullPage: true,
      });
      await context.tracing.stop({ path: ".runtime/pages-export-failure.zip" });
      throw error;
    }
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
    await page.route("**/generations/*/snapshot.json", (route) =>
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
    await page.unroute("**/generations/*/snapshot.json");
    await page.goto(base + "?demo=1", { waitUntil: "networkidle" });
    await page.locator(".chart svg").waitFor();
    await page
      .getByText("Illustrative data for design evaluation.", { exact: false })
      .waitFor();
    checks.push(
      "Chart library loads under the project subpath in explicitly selected demo mode",
    );
    const pollContext = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
    });
    const pollPage = await pollContext.newPage();
    const pollRequests: string[] = [];
    pollPage.on("request", (r) => {
      requests.push(r.url());
      pollRequests.push(r.url());
    });
    pollPage.on("pageerror", (e) => errors.push(e.message));
    await pollPage.clock.install({ time: new Date() });
    await pollPage.goto(base, { waitUntil: "networkidle" });
    await pollPage.getByText("Published snapshot", { exact: true }).waitFor();
    const pollCount = () =>
      pollRequests.filter((url) => url.endsWith("/data/feed.json")).length;
    const payloadCount = () =>
      pollRequests.filter((url) => url.includes("/generations/")).length;
    const initialPolls = pollCount(),
      initialPayloads = payloadCount();
    await pollPage.evaluate(
      'Object.defineProperty(document, "visibilityState", { configurable: true, get() { return "hidden"; } })',
    );
    await pollPage.clock.fastForward(61000);
    assert.equal(pollCount(), initialPolls);
    await Promise.all([
      pollPage.waitForResponse((r) => r.url().endsWith("/data/feed.json")),
      pollPage.evaluate(
        'Object.defineProperty(document, "visibilityState", { configurable: true, get() { return "visible"; } }); document.dispatchEvent(new Event("visibilitychange"));',
      ),
    ]);
    await pollPage.waitForLoadState("networkidle");
    assert.equal(pollCount(), initialPolls + 1);
    assert.equal(payloadCount(), initialPayloads);
    checks.push(
      "Hidden tabs spend no checks; visibility resumes manifest-only polling for unchanged data",
    );
    await pollContext.setOffline(true);
    await pollPage
      .getByText("Offline · retained view", { exact: true })
      .waitFor();
    await pollPage.clock.fastForward(61000);
    assert.equal(pollCount(), initialPolls + 1);
    await Promise.all([
      pollPage.waitForResponse((r) => r.url().endsWith("/data/feed.json")),
      pollContext.setOffline(false),
    ]);
    await pollPage.waitForLoadState("networkidle");
    assert.equal(pollCount(), initialPolls + 2);
    checks.push(
      "Offline viewing preserves evidence and resumes within the same refresh budget",
    );
    const originalManifest = JSON.parse(
      await readFile(path.join(root, "data/feed.json"), "utf8"),
    );
    const originalPublication = await readFile(
      path.join(root, "data", originalManifest.publicationPath),
      "utf8",
    );
    const originalSnapshot = await readFile(
      path.join(root, "data/snapshot.json"),
      "utf8",
    );
    const falseGeneration = "f".repeat(64);
    const mixed = {
      ...originalManifest,
      generation: falseGeneration,
      snapshotPath: `generations/${falseGeneration}/snapshot.json`,
      publicationPath: `generations/${falseGeneration}/publication.json`,
    };
    await pollPage.route("**/data/feed.json", (route) =>
      route.fulfill({ json: mixed }),
    );
    await pollPage.route(`**/generations/${falseGeneration}/*`, (route) =>
      route.fulfill({
        body: route.request().url().endsWith("snapshot.json")
          ? originalSnapshot
          : originalPublication + " ",
        contentType: "application/json",
      }),
    );
    await pollPage.clock.fastForward(61000);
    await pollPage
      .getByText("Published generation failed its integrity check.", {
        exact: false,
      })
      .waitFor();
    await pollPage.getByText("Published snapshot", { exact: true }).waitFor();
    assert.equal(await pollPage.locator(".asset-table tbody tr").count(), 12);
    checks.push(
      "A corrupt update keeps the previous validated generation visible",
    );
    await pollContext.close();
    await context.tracing.stop();
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
