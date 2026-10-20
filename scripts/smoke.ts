import { chromium } from "playwright";
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { demo } from "../apps/web/lib/demo";
import { isSnapshot } from "../apps/web/lib/market";

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
  const errors: string[] = [];
  const checks: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    await page.goto(`${base}/?demo=1`, { waitUntil: "networkidle" });
    await page.locator(".chart svg").waitFor();
    assert.match(
      (await page
        .getByRole("button", { name: "Common close", exact: true })
        .getAttribute("class")) ?? "",
      /selected/,
    );
    await page
      .getByRole("button", { name: "Latest quotes", exact: true })
      .click();
    assert.equal(await page.locator(".asset-table tbody tr").count(), 12);
    checks.push("Demo: twelve assets, explicit label, chart hydration");
    assert.ok(
      await page
        .getByText("Illustrative data for design evaluation.", { exact: false })
        .isVisible(),
    );
    await page
      .getByRole("combobox", { name: "Asset class" })
      .selectOption("Crypto");
    assert.equal(await page.locator(".asset-table tbody tr").count(), 2);
    await page.getByRole("textbox", { name: "Find an asset" }).fill("btc");
    assert.equal(await page.locator(".asset-table tbody tr").count(), 1);
    await page
      .getByRole("textbox", { name: "Find an asset" })
      .fill("unmatched");
    await page.getByRole("button", { name: "Clear filters" }).click();
    assert.equal(await page.locator(".asset-table tbody tr").count(), 12);
    checks.push("Search, class filter and empty-state recovery");
    await page.getByRole("button", { name: "Sort by daily change" }).click();
    assert.match(
      await page.locator(".asset-table tbody tr").first().innerText(),
      /BTC/,
    );
    await page.getByRole("button", { name: "Sort by daily change" }).click();
    assert.match(
      await page.locator(".asset-table tbody tr").first().innerText(),
      /ETH/,
    );
    checks.push("Daily-return sort in both directions");
    await page
      .getByRole("button", { name: "Inspect ETH", exact: true })
      .click();
    await page.getByRole("dialog").waitFor();
    assert.match(
      await page.getByRole("dialog").innerText(),
      /Illustrative prices/,
    );
    await page.keyboard.press("Escape");
    await page.getByRole("dialog").waitFor({ state: "hidden" });
    checks.push("Evidence drawer and keyboard dismissal");
    await page.getByRole("button", { name: "ETH / BTC", exact: true }).click();
    await page.getByRole("button", { name: "20D", exact: true }).click();
    assert.ok(
      await page
        .getByText("20 aligned observations", { exact: true })
        .isVisible(),
    );
    checks.push("Relative pair and history-range controls");
    await page
      .getByRole("button", { name: "Save snapshot", exact: true })
      .click();
    await page.getByRole("button", { name: "Journal", exact: true }).click();
    assert.equal(await page.locator(".journal-row").count(), 1);
    await page.reload({ waitUntil: "networkidle" });
    await page.getByRole("button", { name: "Journal", exact: true }).click();
    assert.equal(await page.locator(".journal-row").count(), 1);
    checks.push("Journal persists across reload");
    await page.getByRole("button", { name: "Open", exact: true }).click();
    assert.ok(
      await page.getByText("Saved snapshot", { exact: true }).isVisible(),
    );
    const exported = page.waitForEvent("download");
    await page.getByRole("button", { name: "Export", exact: true }).click();
    assert.match((await exported).suggestedFilename(), /rotation-illustrative/);
    checks.push("Saved snapshot opens frozen and exports");
    await page.getByRole("button", { name: "Fund flows", exact: true }).click();
    await page.getByRole("button", { name: "13 weeks", exact: true }).click();
    assert.equal(await page.locator(".flow-table tbody tr").count(), 5);
    assert.equal(
      await page
        .locator(".flow-table td.numeric")
        .filter({ hasText: "—" })
        .count(),
      10,
    );
    checks.push("Flow gaps remain unavailable for every selected window");
    await page.getByRole("button", { name: "Sources", exact: true }).click();
    assert.ok(
      await page
        .getByRole("heading", { name: "A timestamp is evidence" })
        .isVisible(),
    );
    await page.getByRole("button", { name: "Overview", exact: true }).click();
    for (const width of [1440, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.locator(".chart svg").waitFor();
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        ),
        false,
        `page overflow at ${width}`,
      );
    }
    checks.push("Desktop, tablet and phone have no page overflow");
    await page
      .getByRole("button", { name: "Inspect SPY", exact: true })
      .click();
    assert.ok((await page.getByRole("dialog").boundingBox())!.width <= 390);
    await page
      .getByRole("button", { name: "Close details", exact: true })
      .click();
    checks.push("Mobile evidence drawer fits viewport");
    await page.setViewportSize({ width: 1440, height: 1000 });

    // Deterministic connected-state tests; synthetic data is used only by this test route.
    const fixture = structuredClone(demo);
    fixture.mode = "connected";
    fixture.id = "test-connected";
    for (const a of fixture.assets) a.status = "delayed";
    for (const s of fixture.sources) s.status = "delayed";
    let calls = 0;
    await page.route("**/api/market", async (route) => {
      calls++;
      await route.fulfill({ json: fixture });
    });
    await page
      .getByRole("button", { name: "Use connected data", exact: false })
      .click();
    await page.getByText("Connected sources", { exact: true }).waitFor();
    await page
      .getByRole("button", { name: "Save snapshot", exact: true })
      .click();
    await page.getByRole("button", { name: "Journal", exact: true }).click();
    await page
      .locator(".journal-row")
      .first()
      .getByRole("button", { name: "Open", exact: true })
      .click();
    const before = calls;
    await page.clock.install();
    await page.clock.fastForward(61000);
    assert.equal(calls, before);
    checks.push("Archived connected snapshot does not auto-refresh");
    await page.unroute("**/api/market");
    await page.route("**/api/market", (route) =>
      route.fulfill({
        status: 503,
        json: { error: "Test: market service unavailable" },
      }),
    );
    await page
      .getByRole("button", { name: "Refresh sources", exact: true })
      .click();
    await page.locator(".error-banner").waitFor();
    assert.ok(
      (await page.locator(".error-banner").innerText()).includes("unavailable"),
    );
    assert.equal(await page.locator(".asset-table tbody tr").count(), 12);
    checks.push("A failed refresh preserves existing observations");
    await page.unroute("**/api/market");
    assert.deepEqual(errors, []);
    checks.push("No uncaught browser exceptions");

    // Actual provider route: validate the real integration separately from mocked interactions.
    const response = await context.request.get(`${base}/api/market`);
    assert.equal(response.status(), 200);
    const snapshot = await response.json();
    assert.ok(isSnapshot(snapshot));
    assert.equal(snapshot.mode, "connected");
    checks.push(
      `Real API contract: ${snapshot.assets.filter((a) => a.price !== null).length}/12 observations available`,
    );
    await mkdir(".runtime", { recursive: true });
    await writeFile(
      ".runtime/smoke-report.json",
      JSON.stringify({ at: new Date().toISOString(), checks, errors }, null, 2),
    );
    console.log(
      JSON.stringify({ passed: checks.length, checks, errors }, null, 2),
    );
  } finally {
    await browser.close();
  }
}
run().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
