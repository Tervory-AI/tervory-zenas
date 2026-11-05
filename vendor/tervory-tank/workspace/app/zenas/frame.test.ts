import assert from "node:assert/strict";
import { test } from "node:test";
import { AREAS, DEADLINE_ALERTS, DEADLINE_NOTE, DEADLINE_ROWS, hitsFor } from "./frame";
import { VAULT_OFF_MESSAGE, mapVaultPayload, offPanel } from "./plugins";

test("areas of law lists the forty-nine civil areas", () => {
  assert.equal(AREAS.length, 49);
  assert.equal(AREAS[0], "All areas (civil)");
  assert.equal(AREAS[3], "Animal welfare");
  assert.equal(AREAS.includes("Banking and finance"), true);
  assert.equal(new Set(AREAS).size, 49);
});

test("an off vault shows the plain sentence and drops any secret that arrived with it", () => {
  const secret = "vault-secret-value-9f3a";
  const panel = mapVaultPayload({
    enabled: false,
    message: VAULT_OFF_MESSAGE,
    secrets: [{ name: "KEY_NAME", purpose: "What this key unlocks", mask: "••••••••", value: secret }],
    files: [{ id: "f1", name: "File name.pdf", content: secret }],
    connections: [{ id: "c1", name: "Connection name", token: secret }],
  });
  assert.deepEqual(panel, offPanel());
  assert.equal(JSON.stringify(panel).includes(secret), false);
  assert.equal(panel.message, "This vault is off. Turn it on in Tank.");
});

test("an on vault keeps names and masks and still drops the value", () => {
  const secret = "vault-secret-value-9f3a";
  const panel = mapVaultPayload({
    enabled: true,
    secrets: [{ name: "KEY_NAME", purpose: "Reports", mask: "••••••••", saved_at: "2026-10-18T00:00:00.000Z", value: secret }],
    files: [{ id: "f1", name: "File name.pdf", type: "pdf", size: 1200, pages: 4, uploaded_at: "2026-10-18T00:00:00.000Z", content: secret }],
    links: [{ id: "l1", url: "https://example.test/act", label: "Act", frequency: "weekly", status: "not checked" }],
    connections: [{ id: "c1", name: "Tool", server_url: "https://example.test/mcp", has_token: true, token: secret }],
  });
  assert.equal(panel.enabled, true);
  assert.equal(panel.secrets[0]?.mask, "••••••••");
  assert.equal(panel.secrets[0]?.name, "KEY_NAME");
  assert.equal(panel.files[0]?.name, "File name.pdf");
  assert.equal(panel.links[0]?.url, "https://example.test/act");
  assert.equal(panel.connections[0]?.hasToken, true);
  assert.equal(JSON.stringify(panel).includes(secret), false);
});

test("the deadlines panel keeps the drawn blanks and does not add matters", () => {
  const text = [
    ...DEADLINE_ROWS.flatMap((row) => [row.title, row.meta]),
    ...DEADLINE_ALERTS.flatMap((alert) => [alert.title, alert.detail]),
    DEADLINE_NOTE,
  ].join(" ");
  assert.equal(DEADLINE_ROWS.length, 0);
  assert.equal(DEADLINE_ALERTS.length, 3);
  assert.equal(text.includes("_"), false);
  assert.equal(/Kanos|Mokoena|Ndlovu|Sunfield|Highveld/i.test(text), false);
  assert.match(DEADLINE_NOTE, /lawyer must confirm/);
});

test("search stays inside the drawn record kinds", () => {
  assert.equal(hitsFor("All", "").length, 0);
  assert.equal(hitsFor("Citations", "").length, 0);
  assert.equal(hitsFor("Documents", "annexure").length, 0);
  assert.equal(hitsFor("Matters", "no such record").length, 0);
});
