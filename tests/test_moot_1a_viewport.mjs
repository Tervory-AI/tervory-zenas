import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// Prefer a workspace tree that already has react/react-dom (local symlink or sibling tank).
const requireBases = [
  path.join(root, "workspace/package.json"),
  "/workspace/rig-5f-forge-reviews/tank-247/workspace/package.json",
];
let require;
for (const base of requireBases) {
  try {
    require = createRequire(base);
    require.resolve("react");
    require.resolve("react-dom/client");
    break;
  } catch {
    require = null;
  }
}
if (!require) throw new Error("react/react-dom not found; symlink workspace/node_modules from tank-247");

function loadPlaywright() {
  const candidates = [
    process.env.TRAIN1_PLAYWRIGHT_PATH,
    path.join(root, "node_modules/playwright"),
    path.join(root, "workspace/node_modules/playwright"),
    "/workspace/b-review-rig7/node_modules/playwright",
    "/workspace/rig-5f-forge-reviews/tank-247/workspace/node_modules/playwright",
  ].filter(Boolean);
  for (const c of candidates) {
    try {
      return require(c);
    } catch {
      /* try next */
    }
  }
  throw new Error("playwright not found; set TRAIN1_PLAYWRIGHT_PATH");
}

function loadEsbuild() {
  const candidates = [
    process.env.TRAIN1_ESBUILD_PATH,
    "esbuild",
    "/workspace/rig-5f-forge-reviews/rig-browser/node_modules/esbuild",
    "/workspace/b-review-rig7/node_modules/esbuild",
  ].filter(Boolean);
  for (const c of candidates) {
    try {
      return require(c);
    } catch {
      /* try next */
    }
  }
  throw new Error("esbuild not found");
}

const { chromium } = loadPlaywright();
const { build } = loadEsbuild();
const out = process.env.MOOT_SCREENSHOT_DIR || "/tmp/moot-1a-shots";
await fs.mkdir(out, { recursive: true });

const reactRoot = path.dirname(require.resolve("react/package.json"));
const nodePaths = [path.join(reactRoot, "..")];

const bundle = await build({
  stdin: {
    contents: `
import React from 'react';
import {createRoot} from 'react-dom/client';
import {WatchPanel} from './app/moot/WatchPanel';
const row = {
  matchId: 'fixture-published',
  published: true,
  title: 'Fixture Landlord v Tenant (SCA) [TEST]',
  fairnessReading: 'problem_may_be_lopsided',
  judgmentOrder: 'appeal_dismissed',
  judgmentReasons: ['1. Notice was required.', '2. Cancellation could not stand.'],
  reveal: 'Appeal dismissed. Notice was required before cancellation.',
  transcript: [
    {role:'counsel_appellant', text:'The cancellation clause is clear.'},
    {role:'judge', text:'My Lord, where is notice dispensed with?'},
    {role:'counsel_respondent', text:'Notice was required.'},
    {role:'judgment', text:'Order: appeal_dismissed.'},
    {role:'reveal', text:'Appeal dismissed.'},
  ],
  authorities: [
    {name:'Fixture Pacta Sunt Servanda [TEST] ZASCA 1', source_url:'https://example.test/a'},
    {name:'Fixture Good Faith Lease [TEST] ZASCA 3', source_url:'https://example.test/b'},
  ],
  costUsd: 0.012,
};
createRoot(document.getElementById('root')).render(<WatchPanel row={row}/>);
`,
    resolveDir: path.join(root, "workspace"),
    loader: "tsx",
  },
  bundle: true,
  write: false,
  jsx: "automatic",
  nodePaths,
  define: { "process.env.NODE_ENV": '"production"' },
});

const css = await fs.readFile(path.join(root, "workspace/app/moot/moot-watch.css"), "utf8");
const js = bundle.outputFiles[0].text;
const server = createServer((req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  if (url.pathname === "/bundle.js") {
    res.setHeader("content-type", "text/javascript");
    res.end(js);
    return;
  }
  res.setHeader("content-type", "text/html; charset=utf-8");
  res.end(
    `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0}${css}</style></head><body><div id="root"></div><script src="/bundle.js"></script></body></html>`,
  );
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const port = server.address().port;
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${port}`);
  await page.getByRole("heading", { name: "Fixture Landlord v Tenant (SCA) [TEST]" }).waitFor();
  for (const name of ["Transcript", "Judgment", "Fairness check", "What the court actually held", "Authorities"]) {
    assert.equal(await page.getByRole("heading", { name, exact: true }).count(), 1, name);
  }
  for (const width of [1440, 375]) {
    await page.setViewportSize({ width, height: 900 });
    await page.screenshot({ path: path.join(out, `moot-watch-${width}.png`), fullPage: true });
    const ok = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
    assert.equal(ok, true, `overflow at ${width}`);
  }
  console.log("MOOT-1a watch viewport: 1440 and 375 no horizontal overflow; sections present");
} finally {
  await browser.close();
  server.close();
}
