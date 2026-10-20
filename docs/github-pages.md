# GitHub Pages deployment and operation

## Deployment prerequisites

Use the existing `woussc/All-Seeing-Eye` repository. A GitHub Free Pages deployment requires a public repository. The workflow only runs when the repository's visibility is explicitly `public`; it never changes visibility, purchases a plan or uses a private-repository Actions allowance. Review existing repository contents before any visibility change.

In the repository's **Settings → Pages**, select **GitHub Actions** as the build/deployment source. Under **Actions**, run **Publish Market Rotation Monitor** from `main`, or let a push to `main` trigger it. Once deployment succeeds, use the URL shown by the `github-pages` environment. Do not treat a predicted URL as evidence that deployment succeeded.

If Actions are disabled by account/organization policy, allow the required pinned GitHub actions. The workflow needs `contents: read` for the build and `pages: write` plus `id-token: write` only for deployment. It uses the job's built-in token; no PAT, market-data key or new service account is needed. Pages configuration and deployment permissions must be available to the repository owner or authorized integration.

## Daily publication

The schedule is `17 0 * * *` UTC: approximately 08:17 Singapore, every day. Manual runs and pushes to `main` also publish a capture. Scheduled workflows execute from the default branch; keep `main` as the default branch. GitHub can delay or skip scheduled jobs and may disable schedules on inactive public repositories. The interface shows the capture time and flags publications older than 36 hours.

Each job installs locked dependencies, runs backend/frontend tests, collects public sources, builds the static site and deploys the generated directory. Standard GitHub-hosted runners are used in a public repository. No large runners, paid services or externally billed inference are configured. Actions have 15-minute build and 10-minute deploy timeouts. Jobs are serialized so cache writes and publications cannot race.

## Data, privacy and storage

The scheduled publisher uses a separate `.runtime/pages-state/sources.sqlite3` containing public price/macro caches and research watch state. It never loads the private application database or imported flow files. Public flows remain explicitly unavailable. The only deployment artifact is `apps/pages/out`; database files are not published.

The cache contains up to 30 full JSON reports in its `public` directory, a latest snapshot, a report index, publication metadata and the current Markdown report. Every report contains its original source timestamps and a SHA-256 snapshot digest. Reusing a snapshot identity with changed content is rejected. Old report content is preserved until retention removes it. Cache eviction or loss resets history; this is not a durable backup service. Download reports to keep them independently.

Source failures are normal supported states. With cached data, failed sources retain their last observation and become stale. Without cache, they remain unavailable. A valid missing-data publication may replace an earlier site so users can see that collection failed. A failed build/deployment leaves the last deployed site in place; the browser's publication-age check eventually marks it overdue. No provider outage activates synthetic data.

Browser requests stay on the Pages origin. Public providers are contacted only by the scheduled collector, avoiding browser CORS dependencies. Refresh reloads the latest published JSON; it cannot initiate provider requests. Quotes and research watches describe the capture, not the current moment. Opening a historical report freezes its original evidence.

## Local verification

`npm run build:pages` creates an isolated generated copy at `apps/pages`, excludes `app/api`, and sets Next.js static export and the repository base path. It does not overwrite the local app's `.next` output. Generated content and source caches are ignored by Git.

`npm run test:pages` serves the export through a plain static server and checks subpath assets, browser-only interactions, report downloads, absent write controls, browser persistence, responsive layouts, stale-publication behavior and initial-load failures. Explicit demo mode verifies the chart library under the project path. All external browser requests and accidental `/api` calls fail the test.

Use `PAGES_BASE_PATH=''` for an account-root Pages site. A repository project site uses `/<repository-name>`. Rebuild after changing the base path; it is compiled into the assets. Custom domains are not configured.

## Current deployment status

Local static build and browser validation are separate from GitHub publication. At initial preparation, Git transport could read the empty repository but the environment proxy denied requests to `api.github.com`, preventing verification of visibility, Pages settings and deployment status. Network requirements for `api.github.com` and `woussc.github.io` were saved in the cloud environment draft; saving that draft does not apply it or deploy the site.

See GitHub's [Pages documentation](https://docs.github.com/en/pages) and [workflow schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule) for current account eligibility and scheduling limits.
