import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";

const root = process.cwd();
const stage = path.join(root, "apps/pages");
const data = path.resolve(
  process.env.PAGES_DATA_DIR ?? ".runtime/pages-state/public",
);
const basePath = process.env.PAGES_BASE_PATH ?? "/All-Seeing-Eye";
if (basePath && !/^\/[A-Za-z0-9_.-]+$/.test(basePath))
  throw Error("PAGES_BASE_PATH must be empty or a single repository path.");
// Fail before replacing the generated app if no source publication exists.
const snapshot = JSON.parse(
  await readFile(path.join(data, "snapshot.json"), "utf8"),
);
if (snapshot.mode !== "connected" || snapshot.assets?.length !== 12)
  throw Error(
    "Generate a validated public snapshot with export_pages.py first.",
  );
await rm(stage, { recursive: true, force: true });
await mkdir(stage, { recursive: true });
for (const entry of [
  "app",
  "components",
  "lib",
  "public",
  "next.config.ts",
  "tsconfig.json",
  "next-env.d.ts",
])
  await cp(path.join(root, "apps/web", entry), path.join(stage, entry), {
    recursive: true,
    filter: (source) => source !== path.join(root, "apps/web/app/api"),
  });
await cp(data, path.join(stage, "public/data"), { recursive: true });
await writeFile(path.join(stage, "public/.nojekyll"), "");
const result = spawnSync(
  process.execPath,
  ["node_modules/next/dist/bin/next", "build", "apps/pages"],
  {
    cwd: root,
    stdio: "inherit",
    env: {
      ...process.env,
      NEXT_PUBLIC_DEPLOYMENT: "pages",
      NEXT_PUBLIC_BASE_PATH: basePath,
    },
  },
);
if (result.status !== 0) process.exit(result.status ?? 1);
console.log(
  `Static dashboard: apps/pages/out (base path: ${basePath || "/"}).`,
);
