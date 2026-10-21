import { chromium } from "playwright";
import assert from "node:assert/strict";
import { mkdir, writeFile, readFile } from "node:fs/promises";
import { isSnapshot, technicalsFor, type View } from "../apps/web/lib/market";
import type { FlowSeries } from "../contracts/context";

async function run() {
  const base = process.env.DASHBOARD_URL ?? "http://127.0.0.1:3000";
  const browser = await chromium.launch({
    executablePath:
      process.env.CHROME_PATH ??
      "/workspace/All-Seeing-Eye/scripts/chromium-local.sh",
    headless: true,
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    acceptDownloads: true,
  });
  const page = await context.newPage();
  const checks: string[] = [],
    errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const response = await context.request.get(`${base}/api/market`, {
      timeout: 35000,
    });
    assert.equal(response.status(), 200);
    const real = await response.json();
    assert.ok(isSnapshot(real));
    assert.ok(real.context);
    await mkdir(".runtime", { recursive: true });
    await page.goto(base, { waitUntil: "networkidle" });
    await page
      .getByText("Connected sources", { exact: true })
      .waitFor({ timeout: 35000 });
    await page.getByRole("button", { name: "Research", exact: true }).click();
    assert.equal(await page.locator(".macro-item").count(), 4);
    await page.screenshot({
      path: ".runtime/research-desktop.png",
      fullPage: true,
    });
    await page.locator(".macro-item summary").first().click();
    assert.match(
      await page.locator(".macro-item").first().innerText(),
      /Publication time:/,
    );
    checks.push("Actual context loads with four dated macro evidence rows");
    await page.getByRole("button", { name: "Overview", exact: true }).click();
    await page
      .getByRole("button", { name: "Inspect SPY", exact: true })
      .click();
    await page.getByRole("dialog").waitFor();
    assert.match(await page.getByRole("dialog").innerText(), /Trend evidence/);
    const spy = real.assets.find((asset) => asset.symbol === "SPY")!;
    const checkTechnicalView = async (view: View) => {
      const text = await page.getByRole("dialog").innerText();
      if (technicalsFor(spy, view)) {
        assert.match(text, /EMA200 \/ 5-bar move/);
        assert.match(text, /ATR14 \/ close/);
        await page
          .getByText("Confirmed price structure:", { exact: false })
          .click();
      } else {
        assert.match(text, /Detailed trend evidence is unavailable/);
        assert.doesNotMatch(text, /EMA200 \/ 5-bar move/);
      }
    };
    await checkTechnicalView("close");
    await page.keyboard.press("Escape");
    await page
      .getByRole("button", { name: "Latest quotes", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Inspect SPY", exact: true })
      .click();
    await checkTechnicalView("latest");
    await page.screenshot({
      path: ".runtime/technical-evidence-desktop.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    );
    await page.screenshot({
      path: ".runtime/technical-evidence-mobile.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.keyboard.press("Escape");
    checks.push(
      "Technical evidence respects each selected cutoff; retained latest-view evidence fits desktop and phone",
    );
    await page
      .getByRole("button", { name: "Common close", exact: true })
      .click();
    await page.screenshot({
      path: ".runtime/connected-overview.png",
      fullPage: true,
    });

    const blocked = await context.request.post(`${base}/api/research/reports`, {
      headers: { origin: "https://unrelated.example" },
      data: { snapshotId: real.id },
    });
    assert.equal(blocked.status(), 403);
    checks.push("Production proxy blocks cross-site mutations");

    // This saves a real provider snapshot to the durable archive. No synthetic record is stored.
    await page.getByRole("button", { name: "Journal", exact: true }).click();
    await page
      .getByRole("button", { name: "Archive source snapshot", exact: true })
      .click();
    await page.locator(".saved-briefing").waitFor({ timeout: 15000 });
    assert.match(
      await page.locator(".saved-briefing").innerText(),
      /Market evidence briefing/,
    );
    const download = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "Export report", exact: true })
      .click();
    assert.match(
      (await download).suggestedFilename(),
      /rotation-report-[a-f0-9]{24}\.json/,
    );
    const monitorDownload = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "Export monitor report", exact: true })
      .click();
    const monitorFile = await monitorDownload;
    const monitorPath = await monitorFile.path();
    assert.ok(monitorPath);
    const monitorText = await readFile(monitorPath, "utf8");
    assert.match(
      monitorFile.suggestedFilename(),
      /rotation-monitor-[a-f0-9]{24}\.md/,
    );
    assert.match(monitorText, /Jev: disabled; connection approval pending/);
    assert.match(monitorText, /Common price cutoff:/);
    assert.match(monitorText, /## Three research priorities/);
    checks.push(
      "Monitor export contains the source-based mandate, common cutoff and disabled Jev status",
    );
    await page.reload({ waitUntil: "networkidle" });
    await page
      .getByText("Connected sources", { exact: true })
      .waitFor({ timeout: 35000 });
    await page.getByRole("button", { name: "Journal", exact: true }).click();
    await page.locator(".report-row").first().waitFor();
    await page.locator(".report-row").first().click();
    await page.locator(".saved-briefing").waitFor();
    checks.push(
      "Actual source report archives, exports and survives browser reload",
    );

    // Start a refresh, then open the archived report before that request finishes.
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let pending = false;
    await page.route("**/api/market", async (route) => {
      pending = true;
      await gate;
      try {
        await route.fulfill({ json: real });
      } catch {
        /* The UI correctly aborts this request when opening the archive. */
      }
    });
    await page
      .getByRole("button", { name: "Refresh sources", exact: true })
      .click();
    await page.waitForFunction(() =>
      document
        .querySelector('[aria-label="Refresh sources"]')
        ?.hasAttribute("disabled"),
    );
    assert.ok(pending);
    await page
      .getByRole("button", { name: "Open frozen snapshot", exact: true })
      .click();
    release();
    await page.getByText("Saved snapshot", { exact: true }).waitFor();
    await page.waitForTimeout(100);
    assert.ok(
      await page.getByText("Saved snapshot", { exact: true }).isVisible(),
    );
    await page.unroute("**/api/market");
    checks.push("Opening an archive cancels an in-flight refresh");

    // All fixture imports below are intercepted in-browser; the server store is untouched.
    const fixture = structuredClone(real);
    fixture.id = "browser-test-only";
    fixture.context!.flows = { series: [], recordCount: 0 };
    const weekly: FlowSeries = {
      id: "browser-fixture",
      label: "Browser test fixture",
      category: "named-etf",
      scope: "Synthetic single fund; test only",
      sourceName: "Browser fixture",
      sourceUrl: "https://example.com/test",
      rightsNote: "Synthetic browser test only",
      latestPublishedAt: "2026-10-17T09:00:00Z",
      importedAt: real.generatedAt,
      revision: 1,
      constituents: ["TEST"],
      windows: ([1, 4, 13] as const).map((weeks) => ({
        weeks,
        periodStart:
          weeks === 1
            ? "2026-10-10"
            : weeks === 4
              ? "2026-09-19"
              : "2026-07-18",
        periodEnd: "2026-10-16",
        netFlowUsd: weeks === 1 ? 120 : null,
        normalizedPct: weeks === 1 ? 12 : null,
        coveredPeriods: 1,
        expectedPeriods: weeks,
        status: weeks === 1 ? "complete" : "incomplete",
        reason:
          weeks === 1 ? "Complete fixture" : "Missing weeks; totals withheld",
        recordIds: ["fixture-record"],
      })),
    };
    let commits = 0;
    await page.route("**/api/market", (route) =>
      route.fulfill({ json: fixture }),
    );
    await page.route("**/api/research/flows/import", async (route) => {
      const data = route.request().postDataJSON();
      if (data.commit) {
        commits++;
        fixture.context!.flows = { recordCount: 1, series: [weekly] };
      }
      await route.fulfill({
        json: {
          committed: data.commit,
          inserted: 1,
          unchanged: 0,
          series: [weekly.id],
          incomplete: 0,
        },
      });
    });
    await page
      .getByRole("button", { name: "Refresh sources", exact: true })
      .click();
    await page.getByText("Connected sources", { exact: true }).waitFor();
    await page.getByRole("button", { name: "Fund flows", exact: true }).click();
    await page.getByLabel("Flow import file").setInputFiles({
      name: "fixture.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify({ schemaVersion: 1, records: [] })),
    });
    await page
      .getByRole("heading", { name: "Ready to review", exact: true })
      .waitFor();
    assert.equal(commits, 0);
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    assert.equal(commits, 0);
    await page.getByLabel("Flow import file").setInputFiles({
      name: "fixture.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify({ schemaVersion: 1, records: [] })),
    });
    await page
      .getByRole("button", { name: "Import 1 records", exact: true })
      .click();
    await page
      .getByText("1 records imported; 0 unchanged. Revisions are retained.", {
        exact: true,
      })
      .waitFor();
    assert.equal(commits, 1);
    await page
      .locator(".flow-table tbody tr")
      .filter({ hasText: "Browser test fixture" })
      .waitFor();
    assert.match(
      await page.locator(".flow-table tbody tr").first().innerText(),
      /\+\$120/,
    );
    await page.getByRole("button", { name: "4 weeks", exact: true }).click();
    assert.match(
      await page.locator(".flow-table tbody tr").first().innerText(),
      /incomplete/,
    );
    assert.equal(
      await page
        .locator(".flow-table tbody tr")
        .first()
        .locator("td.numeric")
        .filter({ hasText: "—" })
        .count(),
      2,
    );
    checks.push(
      "Flow preview and cancel do not commit; confirmed import refreshes the panel",
    );
    checks.push("Incomplete multiweek flow and AUM totals remain unavailable");

    for (const width of [1440, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        ),
        false,
        `flows overflow at ${width}`,
      );
      await page.getByRole("button", { name: "Journal", exact: true }).click();
      await page.locator(".report-row").first().waitFor();
      await page.locator(".report-row").first().click();
      await page.locator(".saved-briefing").waitFor();
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        ),
        false,
        `journal overflow at ${width}`,
      );
      if (width === 390)
        await page.screenshot({
          path: ".runtime/journal-mobile.png",
          fullPage: true,
        });
      await page.getByRole("button", { name: "Research", exact: true }).click();
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        ),
        false,
        `research overflow at ${width}`,
      );
      if (width === 390)
        await page.screenshot({
          path: ".runtime/research-mobile.png",
          fullPage: true,
        });
      await page
        .getByRole("button", { name: "Fund flows", exact: true })
        .click();
    }
    checks.push(
      "Flow workspace, research view and report briefing fit desktop, tablet and phone",
    );
    await page.unroute("**/api/market");
    await page.unroute("**/api/research/flows/import");
    assert.deepEqual(errors, []);
    checks.push("No uncaught browser errors in new workflows");
    await mkdir(".runtime", { recursive: true });
    await writeFile(
      ".runtime/research-browser-checks.json",
      JSON.stringify({ passed: checks.length, checks, errors }, null, 2),
    );
    console.log(
      JSON.stringify({ passed: checks.length, checks, errors }, null, 2),
    );
  } finally {
    await browser.close();
  }
}
run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
