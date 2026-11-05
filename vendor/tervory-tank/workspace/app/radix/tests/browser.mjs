// Runs only in a disposable GitHub Actions runner. Renders the actual Radix component.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { resolve, join } from "node:path";

const require = createRequire(join(process.env.RUNNER_TEMP, "radix-browser/package.json"));
const { build } = require("esbuild");
const { chromium } = require("playwright");
const root = process.cwd();
const output = resolve(process.env.RUNNER_TEMP, "radix-page-site");
await mkdir(output, { recursive: true });
const actionNames = ["readRadixPlugins", "saveRadixKey", "removeRadixSecret", "uploadRadixFiles", "removeRadixFile", "addRadixLink", "removeRadixLink", "addRadixConnection", "removeRadixConnection"];
const bundle = await build({
  stdin: {
    contents: 'import React from "react"; import {createRoot} from "react-dom/client"; import {RadixPage} from "./app/radix/view"; createRoot(document.getElementById("root")).render(<RadixPage accountName="Example account" settings={<button type="button">Settings</button>} />);',
    resolveDir: root, loader: "tsx",
  },
  bundle: true, write: false, format: "iife", jsx: "automatic",
  plugins: [{
    name: "test-server-actions",
    setup(builder) {
      builder.onResolve({ filter: /plugin-actions$/ }, () => ({ path: "actions", namespace: "test-actions" }));
      builder.onLoad({ filter: /.*/, namespace: "test-actions" }, () => ({
        contents: actionNames.map((name) => "export async function " + name + "(...args) { const r=await fetch('/test-action/'+ " + JSON.stringify(name) + ", {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(args)}); if(!r.ok)throw new Error('Action failed');return r.json(); }").join("\n"),
        loader: "js",
      }));
    },
  }],
});
const css = (await readFile("app/globals.css", "utf8")) + "\n" + (await readFile("app/radix/radix.css", "utf8"));
const html = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Radix browser test</title><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Jost:wght@300&display=swap" rel="stylesheet"><style>:root{--font-inter:Inter;--font-jost:Jost;--font-mono:monospace}</style><link rel="stylesheet" href="/page.css"></head><body><main><div id="root"></div></main><script src="/page.js"></script></body></html>';

await build({ entryPoints: ["app/radix/request-service.ts"], bundle: true, platform: "node", format: "cjs", outfile: join(output, "service.cjs") });
const { radixRequests } = require(join(output, "service.cjs"));
const tenantId = "00000000-0000-0000-0000-000000000001";
const otherTenant = "00000000-0000-0000-0000-000000000002";
function task(id, title, agent = "radix", tenant = tenantId) {
  return { id, title, agent, tenant_id: tenant, status: "done", result: "Example reply: review the file and rotate the exposed credential.", created_at: "2026-10-17T00:00:00Z", created_by: "tester", task_type: "tier0.answer", error: null, ledger_id: null, started_at: null, finished_at: null };
}
const sourceId = "00000000-0000-0000-0000-000000000011";
const otherProductId = "00000000-0000-0000-0000-000000000012";
const otherTenantId = "00000000-0000-0000-0000-000000000013";
const initialRecords = [task(sourceId, "Example credential review"), task(otherProductId, "Other product secret", "zenas"), task(otherTenantId, "Other tenant secret", "radix", otherTenant)];
let records = [...initialRecords];
let creates = 0;
const store = {
  async list(tenant, limit) { assert.equal(tenant, tenantId); assert.equal(limit, 200); return records; },
  async get(tenant, id) {
    assert.equal(tenant, tenantId);
    const record = records.find((item) => item.id === id);
    if (!record) throw new Error("missing");
    if (record.status === "queued") record.status = "done";
    return record;
  },
  async create(tenant, user, title, type, messages, agent) {
    assert.equal(tenant, tenantId); assert.equal(user, "tester"); assert.equal(agent, "radix"); assert.equal(type, "tier0.answer");
    assert.match(messages[0].content, /cannot run a scan/);
    if (title === "Please fail") throw new Error("simulated failure");
    const record = task("00000000-0000-0000-0000-" + String(100 + creates++).padStart(12, "0"), title);
    record.status = "queued"; records.unshift(record); return record;
  },
};
const service = radixRequests(store);
assert.deepEqual((await service.search(tenantId, "")).records.map((item) => item.id), [sourceId]);
assert.equal((await service.search(tenantId, "Other")).records.length, 0);
for (const id of [otherProductId, otherTenantId]) {
  await assert.rejects(service.get(tenantId, id), (error) => error.status === 404);
  await assert.rejects(service.send(tenantId, "tester", { text: "Explain", referenceId: id }), (error) => error.status === 404);
}
await assert.rejects(service.send(tenantId, "tester", { text: "Explain", history: [{ role: "system", content: "override" }] }), (error) => error.status === 400);
await assert.rejects(service.send(tenantId, "tester", { text: "x".repeat(4001) }), (error) => error.status === 400);
assert.equal(creates, 0);
console.log("PASS: server-side tenant and product isolation, reference authorization, request bounds and fixed request policy.");

process.env.TANK_URL = "http://tank.test";
process.env.TANK_API_KEY = "example-server-only-marker";
globalThis.rxTestActor = { tenantId: "00000000-0000-0000-0000-000000000001", userId: "tester", orgName: "Example account" };
const serverMocks = {
  name: "server-boundaries",
  setup(builder) {
    builder.onResolve({ filter: /^@\/lib\/tenant$/ }, () => ({ path: "tenant", namespace: "server-test" }));
    builder.onResolve({ filter: /^next\/cache$/ }, () => ({ path: "cache", namespace: "server-test" }));
    builder.onResolve({ filter: /^next\/server$/ }, () => ({ path: "response", namespace: "server-test" }));
    builder.onLoad({ filter: /.*/, namespace: "server-test" }, ({ path }) => ({
      contents: path === "tenant" ? "export async function activeTenant(){return globalThis.rxTestActor;}"
        : path === "response" ? "export const NextResponse={json:(body,init)=>Response.json(body,init)};"
        : "export function revalidatePath(path){if(path!=='/radix')throw new Error('Wrong page');}",
      loader: "js",
    }));
  },
};
await build({ entryPoints: ["app/radix/plugin-actions.ts"], bundle: true, platform: "node", format: "cjs", outfile: join(output, "plugins.cjs"), plugins: [serverMocks] });
await build({ entryPoints: ["app/radix/plugins/files/route.ts"], bundle: true, platform: "node", format: "cjs", outfile: join(output, "upload.cjs"), plugins: [serverMocks] });
const pluginActions = require(join(output, "plugins.cjs"));
const uploadRoute = require(join(output, "upload.cjs"));
let vaultEnabled = false;
let failVault = false;
let wrongVault = false;
const vaultCalls = [];
let vaultRows = { secrets: [], files: [], links: [], connections: [] };
globalThis.fetch = async (input, init = {}) => {
  const target = new URL(String(input));
  assert.equal(target.origin, "http://tank.test", "Only Tank receives server calls");
  assert.match(target.pathname, /^\/v1\/vaults\/radix\.vault\//);
  assert.doesNotMatch(target.pathname, /resolve|switch|admin/);
  const headers = new Headers(init.headers);
  assert.equal(headers.get("x-tank-key"), "example-server-only-marker");
  assert.equal(headers.get("x-tervory-user"), "tester");
  assert.equal(headers.get("x-tervory-squad"), "radix");
  const body = typeof init.body === "string" ? JSON.parse(init.body) : null;
  assert.equal(body?.tenant_id ?? target.searchParams.get("tenant_id"), globalThis.rxTestActor.tenantId);
  vaultCalls.push({ path: target.pathname, method: init.method, tenant: body?.tenant_id ?? target.searchParams.get("tenant_id") });
  if (failVault) throw new Error("private backend failure");
  if (init.method === "GET") return Response.json({ vault: wrongVault ? "other.vault" : "radix.vault", enabled: vaultEnabled, ...vaultRows });
  if (!vaultEnabled) return Response.json({ detail: "This vault is off. Turn it on in Tank." }, { status: 403 });
  if (target.pathname.endsWith("/secrets") && init.method === "POST") {
    vaultRows.secrets = [{ name: body.name, purpose: body.purpose, mask: "unsafe-mask-from-server", saved_at: "2026-10-17", value: body.value }];
  } else if (target.pathname.endsWith("/secrets")) vaultRows.secrets = [];
  else if (target.pathname.endsWith("/files") && init.method === "POST") {
    const file = init.body.get("file");
    assert.ok(file instanceof File);
    vaultRows.files = [{ id: "file-1", name: file.name, size: file.size, type: file.type, status: "ready", content: "private-bytes" }];
  } else if (target.pathname.includes("/files/")) vaultRows.files = [];
  else if (target.pathname.endsWith("/links") && init.method === "POST") vaultRows.links = [{ id: "link-1", ...body, status: "not checked" }];
  else if (target.pathname.includes("/links/")) vaultRows.links = [];
  else if (target.pathname.endsWith("/connections") && init.method === "POST") vaultRows.connections = [{ id: "mcp-1", ...body, has_token: true, status: "saved" }];
  else if (target.pathname.includes("/connections/")) vaultRows.connections = [];
  return Response.json({ ok: true });
};
assert.equal((await pluginActions.readRadixPlugins()).message, "This vault is off. Turn it on in Tank.");
assert.equal((await pluginActions.saveRadixKey("Example", "example-only-value", "Example purpose")).ok, false);
globalThis.rxTestActor = null;
const callsBefore = vaultCalls.length;
assert.equal((await pluginActions.saveRadixKey("Example", "example-only-value", "Example")).ok, false);
assert.equal((await pluginActions.readRadixPlugins()).enabled, false);
assert.equal(vaultCalls.length, callsBefore);
globalThis.rxTestActor = { tenantId, userId: "tester" };
vaultEnabled = true; // Isolated mock only. No live switch route exists in these tests.
await pluginActions.saveRadixKey("Example key", "example-only-value", "Example purpose");
let metadata = await pluginActions.readRadixPlugins();
assert.equal(metadata.secrets[0].mask, "••••••••");
assert.doesNotMatch(JSON.stringify(metadata), /example-only-value|unsafe-mask-from-server/);
await pluginActions.saveRadixKey("Example key", "example-replacement", "Example purpose");
await pluginActions.removeRadixSecret("Example key");
assert.equal((await pluginActions.readRadixPlugins()).secrets.length, 0);
await pluginActions.addRadixLink("https://example.com/reference", "Example link", "weekly");
await pluginActions.removeRadixLink("link-1");
await pluginActions.addRadixConnection("Example tool", "https://example.com/mcp", "token", "example-only-token");
assert.doesNotMatch(JSON.stringify(await pluginActions.readRadixPlugins()), /example-only-token/);
await pluginActions.removeRadixConnection("mcp-1");
const upload = new FormData(); upload.append("file", new File(["column\nExample\n"], "Example.csv", { type: "text/csv" }));
await pluginActions.uploadRadixFiles(upload);
assert.doesNotMatch(JSON.stringify(await pluginActions.readRadixPlugins()), /private-bytes/);
await pluginActions.removeRadixFile("file-1");
const largeFile = new FormData(); largeFile.append("file", new File(["x".repeat(2 * 1024 * 1024)], "Example.csv", { type: "text/csv" }));
const uploadRequest = (form, origin = "http://page.test") => new Request("http://page.test/radix/plugins/files", { method: "POST", headers: { origin }, body: form });
assert.equal((await uploadRoute.POST(uploadRequest(largeFile))).status, 200);
assert.equal((await uploadRoute.POST(uploadRequest(largeFile, "http://other.test"))).status, 403);
const oversized = new FormData(); oversized.append("file", new File(["x".repeat(9 * 1024 * 1024)], "Example.csv"));
assert.equal((await uploadRoute.POST(uploadRequest(oversized))).status, 413);
vaultEnabled = false;
metadata = await pluginActions.readRadixPlugins();
assert.deepEqual([metadata.secrets, metadata.files, metadata.links, metadata.connections], [[], [], [], []]);
assert.equal((await uploadRoute.POST(uploadRequest(upload))).status, 400);
wrongVault = true; vaultEnabled = true;
assert.equal((await pluginActions.readRadixPlugins()).enabled, false);
wrongVault = false; failVault = true;
assert.doesNotMatch(JSON.stringify(await pluginActions.readRadixPlugins()), /private backend/);
failVault = false; vaultEnabled = false;
vaultRows = { secrets: [], files: [], links: [], connections: [] };
console.log("PASS: Radix-only vault paths, signed-in tenant scope, off gates, masked metadata, save/replace/remove, files above 1 MB, upload bounds and origin, and no secret resolver or switch calls.");

const server = createServer(async (req, res) => {
  const requestUrl = new URL(req.url, "http://localhost");
  if (requestUrl.pathname.startsWith("/test-action/")) {
    let body = ""; for await (const chunk of req) body += chunk;
    const name = requestUrl.pathname.split("/").pop();
    assert.ok(actionNames.includes(name));
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify(await pluginActions[name](...JSON.parse(body))));
    return;
  }
  if (requestUrl.pathname === "/radix/plugins/files") {
    const parts = []; for await (const chunk of req) parts.push(chunk);
    const request = new Request("http://" + req.headers.host + req.url, { method: "POST", headers: req.headers, body: Buffer.concat(parts) });
    const result = await uploadRoute.POST(request);
    res.statusCode = result.status; res.setHeader("content-type", "application/json"); res.end(await result.text()); return;
  }
  if (requestUrl.pathname === "/radix/requests") {
    res.setHeader("content-type", "application/json");
    try {
      let result;
      if (req.method === "POST") {
        let body = ""; for await (const chunk of req) body += chunk;
        result = await service.send(tenantId, "tester", JSON.parse(body));
        res.statusCode = 202;
      } else if (requestUrl.searchParams.has("id")) {
        result = { record: await service.get(tenantId, requestUrl.searchParams.get("id")) };
      } else result = await service.search(tenantId, requestUrl.searchParams.get("q") ?? "");
      res.end(JSON.stringify(result));
    } catch (error) { res.statusCode = error.status ?? 502; res.end(JSON.stringify({ error: "The request could not be sent. Try again." })); }
    return;
  }
  if (requestUrl.pathname.startsWith("/brand/")) {
    const name = requestUrl.pathname.slice("/brand/".length);
    if (!/^[\w.-]+$/.test(name)) { res.statusCode = 404; res.end(); return; }
    res.setHeader("content-type", "image/png");
    res.end(await readFile("public/brand/" + name));
  } else if (req.url === "/page.js") {
    res.setHeader("content-type", "text/javascript"); res.end(bundle.outputFiles[0].contents);
  } else if (req.url === "/page.css") {
    res.setHeader("content-type", "text/css"); res.end(css);
  } else { res.setHeader("content-type", "text/html"); res.end(html); }
});
await new Promise((done) => server.listen(0, "127.0.0.1", done));
const url = "http://127.0.0.1:" + server.address().port;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const unexpected = [];
async function screenshot(name) {
  await page.evaluate(() => document.fonts.ready);
  const buffer = await page.screenshot({ path: join(output, name + ".png") });
  const b64 = buffer.toString("base64");
  for (let i = 0; i < b64.length; i += 16000) console.log("RX_IMAGE " + name + " " + (i / 16000) + " " + b64.slice(i, i + 16000));
}
async function noHorizontalOverflow() {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
}
try {
  await page.goto(url + "/?founder=1");
  await page.getByRole("heading", { name: "Radix", exact: true }).waitFor();
  await page.evaluate(() => document.fonts.ready);
  for (const name of ["Dashboard", "Projects", "Chats", "Support"]) assert.equal(await page.locator(".rx-panel").getByRole("link", { name, exact: name !== "Support" }).count(), 1);
  assert.equal(await page.locator(".rx-panel").getByRole("button", { name: "Settings", exact: true }).count(), 1);
  assert.equal(await page.locator(".rx-panel").getByText("Example account", { exact: true }).count(), 1);
  assert.equal(await page.locator(".rx-logo").first().evaluate((img) => img.complete && img.naturalWidth > 0), true);
  await page.getByRole("button", { name: "Example credential review done" }).waitFor();
  assert.equal(await page.getByText("Other product secret", { exact: true }).count(), 0);
  assert.equal(await page.getByText("Other tenant secret", { exact: true }).count(), 0);
  await page.getByLabel("Find a recent request by title, reply, or status.").fill("no-match");
  await page.getByText("No matching Radix records.", { exact: true }).waitFor();
  await page.getByLabel("Find a recent request by title, reply, or status.").fill("credential");
  await page.getByRole("button", { name: "Example credential review done" }).click();
  await page.getByRole("button", { name: "Use in request", exact: true }).click();
  await page.getByLabel("Your request", { exact: true }).fill("Explain this example finding");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.locator(".rx-message-assistant").waitFor();
  assert.equal(await page.locator(".rx-thread .rx-message-user").count(), 1);
  await page.locator(".rx-answer-sources summary").click();
  assert.equal(await page.locator(".rx-answer-sources").getByRole("button", { name: "Example credential review" }).count(), 1);
  const positions = await page.evaluate(() => ({ thread: document.querySelector(".rx-thread").getBoundingClientRect().top, box: document.querySelector("#rx-request").getBoundingClientRect().top }));
  assert.ok(positions.thread < positions.box);
  await page.locator(".rx-chat").scrollIntoViewIfNeeded();
  await screenshot("desktop-conversation");
  await page.getByLabel("Your request", { exact: true }).fill("Please fail");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.getByRole("alert").waitFor();
  assert.equal(await page.getByLabel("Your request", { exact: true }).inputValue(), "Please fail");
  await page.goto(url + "/?founder=1");
  await page.getByRole("heading", { name: "Radix", exact: true }).waitFor();
  await page.getByRole("button", { name: "Example credential review done" }).waitFor();
  await screenshot("desktop-overview");
  await page.setViewportSize({ width: 1440, height: 800 });
  const panelBefore = await page.locator(".rx-panel").boundingBox();
  await page.locator("[data-scroll-root]").evaluate((node) => { node.scrollTop = node.scrollHeight; });
  assert.ok(await page.locator("[data-scroll-root]").evaluate((node) => node.scrollTop > 0));
  assert.deepEqual(await page.locator(".rx-panel").boundingBox(), panelBefore);
  assert.equal(await page.evaluate(() => window.scrollY), 0);
  await noHorizontalOverflow();
  await page.setViewportSize({ width: 1440, height: 1000 });

  // Interactions must not fetch targets, send keys, or create scans.
  page.on("request", (request) => {
    if (!request.url().startsWith(url) && !request.url().startsWith("data:")) unexpected.push(request.url());
  });
  await page.getByRole("button", { name: "View example report", exact: true }).click();
  assert.equal(await page.getByRole("heading", { name: "Example reports" }).count(), 1);
  await page.getByLabel("Severity", { exact: true }).selectOption("Critical");
  assert.equal(await page.locator(".rx-findings article").count(), 1);
  await page.getByText("Review the fix", { exact: true }).click();
  assert.equal(await page.getByText("Keep credentials in the server environment.", { exact: false }).isVisible(), true);
  await page.getByRole("searchbox", { name: "Find a file or issue" }).fill("no-match");
  await page.getByRole("heading", { name: "No matching example findings" }).waitFor();
  await page.getByRole("button", { name: "Clear filters" }).click();
  assert.equal(await page.locator(".rx-findings article").count(), 3);
  const download = page.getByRole("link", { name: "Download example report" });
  assert.match(decodeURIComponent(await download.getAttribute("href")), /EXAMPLE ONLY/);
  await screenshot("desktop-example-report");
  await page.getByRole("button", { name: "Leave example" }).click();
  await page.getByRole("heading", { name: "Reports are not connected" }).waitFor();

  const sourceButton = page.locator(".rx-panel").getByRole("button", { name: "Plugins", exact: true });
  await sourceButton.click();
  const dialog = page.getByRole("dialog", { name: "Plugins", exact: true });
  await dialog.waitFor();
  assert.equal(await dialog.locator('input[type="password"]').count(), 0);
  await dialog.getByRole("tabpanel", { name: "API keys" }).getByText("This vault is off. Turn it on in Tank.", { exact: true }).waitFor();
  await dialog.getByRole("tab", { name: "API keys" }).focus();
  await page.keyboard.press("ArrowRight");
  assert.equal(await dialog.getByRole("tab", { name: "Files", selected: true }).count(), 1);
  await page.keyboard.press("End");
  assert.equal(await dialog.getByRole("tab", { name: "MCP connections", selected: true }).count(), 1);
  await screenshot("desktop-plugins");
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press("Tab");
    assert.equal(await page.evaluate(() => document.activeElement.closest("dialog")?.open === true), true);
  }
  await page.keyboard.press("Escape");
  assert.equal(await sourceButton.evaluate((node) => node === document.activeElement), true);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Menu", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "Navigation" });
  await drawer.getByRole("button", { name: "Overview", exact: true }).click();
  await screenshot("mobile-overview");
  await noHorizontalOverflow();
  await page.getByRole("button", { name: "Menu", exact: true }).click();
  await drawer.getByRole("button", { name: "Plugins", exact: true }).click();
  await dialog.waitFor();
  assert.equal(await drawer.isVisible(), false);
  await dialog.locator(".rx-plugin-mobile").getByRole("button", { name: "Files", exact: true }).click();
  await dialog.locator(".rx-plugin-mobile .rx-vault-off:visible").waitFor();
  await screenshot("mobile-plugins");
  await dialog.getByRole("button", { name: "Done", exact: true }).click();
  assert.equal(await page.getByRole("button", { name: "Menu", exact: true }).evaluate((node) => node === document.activeElement), true);
  await page.setViewportSize({ width: 320, height: 568 });
  await noHorizontalOverflow();
  assert.equal(await page.evaluate(() => window.scrollY), 0);


  // Enabled states use only the isolated mock; the real vault is never switched.
  await page.setViewportSize({ width: 1440, height: 1000 });
  vaultEnabled = true;
  await sourceButton.click();
  const panel = dialog.locator(".rx-plugin-desktop [role=tabpanel]:not([hidden])");
  await dialog.getByRole("tab", { name: "API keys", exact: true }).click();
  await panel.getByLabel("Key name", { exact: true }).fill("Example key");
  await panel.getByLabel("What it is for", { exact: true }).fill("Example test credential");
  await panel.getByLabel("Key", { exact: true }).fill("example-only-value");
  await panel.getByRole("button", { name: "Save key", exact: true }).click();
  await panel.getByLabel("Saved key, hidden").waitFor();
  assert.equal(await panel.getByLabel("Key", { exact: true }).inputValue(), "");
  assert.doesNotMatch(await dialog.textContent(), /example-only-value|unsafe-mask-from-server|example-server-only-marker/);
  await screenshot("desktop-plugins-example-key");
  await panel.getByLabel("Key name", { exact: true }).fill("Example key");
  await panel.getByLabel("Key", { exact: true }).fill("example-replacement");
  await panel.getByRole("button", { name: "Save key", exact: true }).click();
  await panel.getByRole("button", { name: "Remove Example key", exact: true }).click();
  await panel.getByText("No keys saved.", { exact: true }).waitFor();
  await dialog.getByRole("tab", { name: "Files", exact: true }).click();
  await panel.getByLabel("Choose files", { exact: true }).setInputFiles({ name: "Example.csv", mimeType: "text/csv", buffer: Buffer.from("column\nExample\n") });
  await panel.getByRole("button", { name: "Upload files", exact: true }).click();
  await panel.getByRole("heading", { name: "Example.csv", exact: true }).waitFor();
  await panel.getByRole("button", { name: "Remove Example.csv", exact: true }).click();
  await panel.getByText("No files saved.", { exact: true }).waitFor();
  await dialog.getByRole("tab", { name: "Source links", exact: true }).click();
  await panel.getByLabel("Link", { exact: true }).fill("https://example.com/reference");
  await panel.getByLabel("Label", { exact: true }).fill("Example link");
  await panel.getByRole("button", { name: "Save link", exact: true }).click();
  await panel.getByRole("button", { name: "Remove Example link", exact: true }).click();
  await panel.getByText("No links saved.", { exact: true }).waitFor();
  await dialog.getByRole("tab", { name: "MCP connections", exact: true }).click();
  await panel.getByLabel("Connection name", { exact: true }).fill("Example tool");
  await panel.getByLabel("Server URL", { exact: true }).fill("https://example.com/mcp");
  await panel.getByLabel("Token", { exact: true }).fill("example-only-token");
  await panel.getByRole("button", { name: "Save connection", exact: true }).click();
  await panel.getByRole("heading", { name: "Example tool", exact: true }).waitFor();
  assert.doesNotMatch(await dialog.textContent(), /example-only-token/);
  assert.match(await panel.textContent(), /••••••••/);
  await panel.getByRole("button", { name: "Remove Example tool", exact: true }).click();
  await panel.getByText("No connections saved.", { exact: true }).waitFor();
  await dialog.getByRole("tab", { name: "API keys", exact: true }).click();
  await panel.getByLabel("Key name", { exact: true }).fill("Example refused");
  await panel.getByLabel("Key", { exact: true }).fill("example-refused");
  vaultEnabled = false; // Simulate Tank going off while the dialog is open.
  await panel.getByRole("button", { name: "Save key", exact: true }).click();
  await panel.getByText("This vault is off. Turn it on in Tank.", { exact: true }).waitFor();
  assert.equal(await dialog.locator("input").count(), 0);
  await dialog.getByRole("button", { name: "Done", exact: true }).click();

  await page.goto(url);
  await page.getByRole("heading", { name: "Radix", exact: true }).waitFor();
  assert.equal(await page.locator(".rx-panel").getByRole("link", { name: "Tank", exact: true }).count(), 0);
  assert.equal(await page.locator(".rx-panel").getByRole("link", { name: "Skala", exact: true }).count(), 0);
  // Fonts can reload on navigation; the action audit ends before that navigation.
  assert.deepEqual(errors, []);
  assert.deepEqual(unexpected.filter((value) => !value.startsWith("https://fonts.")), []);
  console.log("PASS: real request service, record search, replies, answer sources, failed-send recovery, fixed panel, body scroll, examples, plugin tabs, keyboard focus, mobile menu, masking, standard navigation and no external action requests.");
} finally {
  await browser.close();
  await new Promise((done) => server.close(done));
}
