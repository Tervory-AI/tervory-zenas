import assert from "node:assert/strict";
import { test } from "node:test";
import { CATALOG, emptyFlags } from "./catalog";
import { formatDay, formatRefreshed } from "./format";
import { readFlags, scrubSecrets } from "./load";
import { buildPage, feedsToCall } from "./model";
import {
  REFERENCE_LABEL,
  NEED_FILE,
  NEED_KEY,
  NEED_LINK,
  NEED_MCP,
  VAULT_OFF_MESSAGE,
  clientPluginState,
  flagsWithVault,
  groundQuestion,
  mapVaultPayload,
  searchMarketLab,
} from "./plugins";
import { exactNumber, parseAlerts, parseEia, parseFred, parseWorldBank } from "./parse";

test("keys never count as fetched feeds", () => {
  const flags = emptyFlags();
  flags.EIA_API_KEY = true;
  flags.FRED_API_KEY = true;
  const page = buildPage(flags, { eia_diesel: { headline: "$4.102" } }, {});
  assert.equal(page.onCount, 0);
  assert.equal(page.offCount, 16);
  assert.equal(page.cards.length + page.featured.length, 0);
  assert.equal(feedsToCall(emptyFlags()).includes("eia_diesel"), false);
});

test("a successful stored feed counts once and opens its document", () => {
  const page = buildPage(emptyFlags(), {
    eia_diesel: { rows: 25, documentId: "stored-document", headline: "25 stored observations", checkedAt: "2026-10-14" },
  }, {});
  assert.equal(page.onCount, 1);
  assert.equal(page.offCount, 15);
  assert.equal(page.featured[0].sourceHref, "/market-lab/sources/stored-document");
  assert.equal(page.off.some((feed) => feed.id === "eia_diesel"), false);
});

test("a failed or empty last fetch cannot retain an On badge", () => {
  for (const reading of [
    { rows: 0, documentId: "old-document", note: "Off, source could not be read" },
    { rows: 20, documentId: "old-document", failed: true },
  ]) {
    const page = buildPage(emptyFlags(), { eia_diesel: reading }, {});
    assert.equal(page.onCount, 0);
    assert.equal(page.featured.length, 0);
    assert.equal(page.off.some((feed) => feed.id === "eia_diesel"), true);
  }
});

test("no readings leave the on section empty, and a store error is its own state", () => {
  const empty = buildPage(emptyFlags(), {}, {}, {});
  assert.equal(empty.onEmpty, true);
  assert.equal(empty.loadError, false);
  assert.equal(empty.refreshedLabel, "Not refreshed yet");
  const dumped = JSON.stringify(empty);
  assert.equal(dumped.includes("3" + ".71"), false);
  assert.equal(dumped.includes("52" + "418"), false);

  const broken = buildPage(emptyFlags(), { noaa_alerts: { headline: "12" } }, {}, { error: true });
  assert.equal(broken.loadError, true);
  assert.equal(broken.onEmpty, false);
});

test("linked pages count checks without inventing a change", () => {
  const page = buildPage(
    emptyFlags(),
    {},
    {
      ups_hazmat: { events: 0 },
      fedex_hazmat: { events: 1, latest: "2026-10-14" },
      usps_pub52: { events: 2, latest: "2026-10-02" },
    },
    {},
  );
  const carrier = page.groups.find((group) => group.id === "carrier");
  assert.equal(carrier?.pages.length, 4);
  assert.equal(carrier?.pages[0]?.note, "Not checked yet");
  assert.equal(carrier?.pages[1]?.note, "Checked 14 Oct 2026");
  assert.equal(carrier?.pages[2]?.note, "Checked 2 Oct 2026");
  const ports = page.groups.find((group) => group.id === "ports");
  assert.equal(ports?.pages[0]?.note, "Opens polb.com");
  assert.equal(ports?.pages.length, 1);
  assert.equal(page.groups.find((group) => group.id === "season")?.pages[0]?.label, "California");
});

test("tenant source metadata uses the existing cards and exact stored document", () => {
  const source = { id: "rss_tenant_feed", title: "Federal Reserve", summary: "", sourceLabel: "www.federalreserve.gov",
    cadence: "weekly" as const, keyName: "", kind: "copy" as const };
  const reading = { rows: 20, documentId: "tenant-document", headline: "20 stored observations", checkedAt: "2026-10-28T12:00:00Z" };
  const page = buildPage(emptyFlags(), { [source.id]: reading }, {}, { sources: [source] });
  assert.equal(page.cards.length, 1);
  assert.equal(page.cards[0].title, source.title);
  assert.equal(page.cards[0].cadence, "Weekly");
  assert.equal(page.cards[0].sourceHref, "/market-lab/sources/tenant-document");
  assert.equal(page.cards[0].headline, "20 stored observations");
  assert.equal(buildPage(emptyFlags(), {}, {}, { sources: [source] }).cards.length, 0);
  assert.equal(buildPage(emptyFlags(), { [source.id]: reading }, {}).cards.length, 0);
});

test("new official feeds open stored documents in the existing cards", () => {
  const page = buildPage(emptyFlags(), {
    sec_edgar: { rows: 2000, documentId: "sec-document", checkedAt: "2026-10-28" },
    port_nynj: { rows: 384, documentId: "port-document", checkedAt: "2026-10-28" },
  }, {});
  assert.equal(page.onCount, 2);
  assert.equal(page.cards.find((card) => card.id === "sec_edgar")?.sourceHref, "/market-lab/sources/sec-document");
  assert.equal(page.cards.find((card) => card.id === "port_nynj")?.sourceHref, "/market-lab/sources/port-document");
  assert.equal(page.groups.some((group) => group.pages.some((entry) => entry.id === "port_nynj")), false);
});

test("numbers stay exact and blank FRED values are skipped", () => {
  assert.equal(exactNumber("1.23450"), "1.23450");
  assert.equal(exactNumber("."), null);
  assert.equal(exactNumber(""), null);
  assert.equal(exactNumber(12), "12");
  assert.deepEqual(parseEia({ response: { data: [{ period: "2026-10-12", value: "4.102" }] } }), {
    value: "4.102",
    period: "2026-10-12",
  });
  assert.equal(parseFred({ observations: [{ date: "2026-09-01", value: "." }, { date: "2026-08-01", value: "120.5" }] })?.value, "120.5");
  assert.equal(parseWorldBank([{ page: 1 }, [{ date: "2024", value: "8100.5" }]], "PCOPPUSDM")?.value, "8100.5");
  assert.deepEqual(parseAlerts({ features: [{ properties: { event: "Small Craft Advisory" } }, { properties: {} }] }), {
    count: 2,
    example: "Small Craft Advisory",
  });
});

test("dates do not shift with the timezone", () => {
  assert.equal(formatDay("2026-10-01"), "1 Oct 2026");
  assert.equal(formatRefreshed("not-a-date"), "Not refreshed yet");
  assert.equal(formatRefreshed("2026-10-17T12:40:00.000Z"), "17 Oct 2026, 5:40 AM PT");
});

test("search hits feeds and linked pages, and skips a question with no reading", () => {
  const page = buildPage(
    emptyFlags(),
    { noaa_alerts: { rows: 12, documentId: "weather-document", headline: "12", detail: "active alerts", goodAt: "2026-10-03" } },
    {},
    {},
  );
  const weather = searchMarketLab(page, "weather alerts");
  assert.equal(weather.onIds.includes("noaa_alerts"), true);
  assert.equal(weather.offIds.includes("eia_diesel"), false);
  const diesel = searchMarketLab(page, "diesel");
  assert.deepEqual(diesel.offIds, ["eia_diesel"]);
  const seasons = searchMarketLab(page, "California");
  assert.equal(seasons.linkIds.includes("season_ca"), true);
  const grounded = groundQuestion(page, "severe weather");
  assert.equal(grounded.sources.length, 1);
  assert.equal(grounded.sources[0]?.name.includes("Severe-weather alerts"), true);
  assert.equal(grounded.sources[0]?.date, "3 Oct 2026");
  assert.equal(groundQuestion(page, "zzzz-no-such-feed").sources.length, 0);
  assert.equal(REFERENCE_LABEL, "Sources");
});

test("plugin state sent to the browser is a set of booleans and never a saved secret", () => {
  const flags = readFlags({ EIA_API_KEY: "example-key" } as unknown as NodeJS.ProcessEnv);
  const state = clientPluginState(flags);
  assert.equal(state.title, "Plugins");
  assert.equal(state.keys.find((row) => row.name === "EIA_API_KEY")?.connected, true);
  assert.equal(state.keys.find((row) => row.name === "FRED_API_KEY")?.connected, false);
  assert.equal(state.files.length, 0);
  assert.equal(state.links.length, 0);
  assert.equal(state.connections.length, 0);
  const dumped = JSON.stringify(state);
  assert.equal(dumped.includes("example-key"), false);
  assert.match(NEED_KEY, /Nothing was stored/);
  assert.match(NEED_FILE, /Nothing was saved/);
  assert.match(NEED_LINK, /Nothing was saved/);
  assert.match(NEED_MCP, /Nothing was saved/);
  assert.equal(NEED_KEY.includes("saved 17"), false);
});

test("a vault key turns a feed on only while that vault is on", () => {
  const flags = emptyFlags();
  const off = flagsWithVault(flags, { enabled: false, names: ["FRED_API_KEY", "EIA_API_KEY"] });
  assert.equal(off.FRED_API_KEY, false);
  assert.equal(off.EIA_API_KEY, false);
  assert.equal(feedsToCall(off).some((id) => id.startsWith("fred_")), false);
  const on = flagsWithVault(flags, { enabled: true, names: ["FRED_API_KEY"] });
  assert.equal(on.FRED_API_KEY, true);
  assert.equal(on.EIA_API_KEY, false);
  assert.equal(feedsToCall(on).filter((id) => id.startsWith("fred_")).length, 7);
  const env = emptyFlags();
  env.EIA_API_KEY = true;
  const both = flagsWithVault(env, { enabled: false, names: [] });
  assert.equal(both.EIA_API_KEY, true);
});

test("an off vault shows the plain sentence and drops any secret that arrived with it", () => {
  const secret = "vault-secret-value-9f3a";
  const panel = mapVaultPayload({
    enabled: false,
    message: VAULT_OFF_MESSAGE,
    secrets: [{ name: "FRED_API_KEY", purpose: "FRED", mask: "••••••••", saved_at: "2026-10-17", value: secret }],
    files: [{ id: "1", name: "secret.csv", content: secret }],
  });
  assert.equal(panel.enabled, false);
  assert.equal(panel.message, VAULT_OFF_MESSAGE);
  assert.equal(panel.secrets.length, 0);
  assert.equal(panel.files.length, 0);
  assert.equal(JSON.stringify(panel).includes(secret), false);
  const open = mapVaultPayload({
    enabled: true,
    secrets: [{ name: "FRED_API_KEY", purpose: "FRED", mask: "••••••••", saved_at: "2026-10-17T00:00:00Z", value: secret }],
    connections: [{ id: "c1", name: "Catalogue", server_url: "https://example.com", auth_type: "token", has_token: true, token: secret }],
  });
  assert.equal(open.secrets[0]?.mask, "••••••••");
  assert.equal(open.connections[0]?.hasToken, true);
  assert.equal(JSON.stringify(open).includes(secret), false);
});

test("key flags are booleans and secret text is stripped before it can leave the server", () => {
  const flags = readFlags({ EIA_API_KEY: "example-key" } as unknown as NodeJS.ProcessEnv);
  assert.equal(flags.EIA_API_KEY, true);
  assert.equal(flags.FRED_API_KEY, false);
  assert.equal(JSON.stringify(flags).includes("example-key"), false);
  const clean = scrubSecrets({ headline: "value example-key tail", note: "api_key=example-key" }, ["example-key"]);
  assert.equal(clean.headline.includes("example-key"), false);
  assert.equal(clean.note.includes("example-key"), false);
});

test("plugin errors and statuses use client words", () => {
  const unavailable = mapVaultPayload({ enabled: false, message: "This vault is off. Turn it on in Tank." });
  assert.equal(unavailable.message, "Plugins are not available yet. Ask your workspace administrator to enable them.");
  const panel = mapVaultPayload({
    enabled: true,
    files: [{ id: "file", name: "inventory.csv", status: "ready" }],
    links: [{ id: "link", url: "https://example.com", status: "not_checked" }],
    connections: [{ id: "connection", name: "Supplier", status: "needs_auth" }],
  });
  assert.equal(panel.files[0].status, "Saved");
  assert.equal(panel.links[0].status, "Not checked yet");
  assert.equal(panel.connections[0].status, "Sign-in needed");
});

test("a blocked linked page does not reuse its previous successful status", () => {
  const page = buildPage(emptyFlags(), {}, {
    ups_hazmat: { events: 0, latest: "2026-10-26", note: "Off, blocked" },
    fedex_hazmat: { events: 1, latest: "2026-10-25", note: "" },
  });
  const carrier = page.groups.find(group => group.id === "carrier")!;
  assert.equal(carrier.pages[0].note, "Not checked: blocked");
  assert.equal(carrier.pages[1].note, "Checked 25 Oct 2026");
});


test("stored freight and linked freight have distinct client titles", () => {
  const stored = CATALOG.find(item => item.id === "bts_freight")!;
  const linked = CATALOG.find(item => item.id === "fred_tsifrght")!;
  assert.notEqual(stored.title, linked.title);
  assert.match(stored.title, /stored figures/);
  assert.match(linked.title, /Federal Reserve Bank of St. Louis/);
});
