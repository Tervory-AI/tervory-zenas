import assert from "node:assert/strict";
import test from "node:test";
import { BASE_SQUAD, BASE_VAULT, LOAD_ERROR_MESSAGE, mapVaultPayload, offPanel, safeMessage, VAULT_OFF_MESSAGE } from "./plugin-panel";

test("Base uses base.vault and the base squad only", () => {
  assert.equal(BASE_VAULT, "base.vault");
  assert.equal(BASE_SQUAD, "base");
});

test("an off vault shows the off message and no items", () => {
  const panel = mapVaultPayload({
    vault: "base.vault",
    enabled: false,
    message: VAULT_OFF_MESSAGE,
    secrets: [{ name: "LEAK", value: "should-not-pass", mask: "••••" }],
    files: [{ id: "f1", name: "a.csv" }],
  });
  assert.equal(panel.enabled, false);
  assert.equal(panel.message, "This vault is off. Turn it on in Tank.");
  assert.deepEqual([panel.secrets, panel.files, panel.links, panel.connections], [[], [], [], []]);
  assert.equal(offPanel().message, "This vault is off. Turn it on in Tank.");
  assert.equal(mapVaultPayload({}).message, "This vault is off. Turn it on in Tank.");
});

test("an on vault passes names and masks, never values, tokens or bytes", () => {
  const panel = mapVaultPayload({
    vault: "base.vault",
    enabled: true,
    secrets: [{ name: "LICENCE_LIST_KEY", purpose: "Licence list", mask: "••••••••", saved_at: "2026-10-17T15:00:00Z", value: "secret-value" }],
    files: [{ id: "f1", name: "hi.csv", type: "csv", size: 2048, rows: 106, uploaded_at: "2026-10-17T15:00:00Z", status: "ready", content: "AAAA" }],
    links: [{ id: "l1", url: "https://example.com/list.csv", frequency: "daily" }],
    connections: [{ id: "c1", name: "CRM", server_url: "https://crm.example.com", auth_type: "token", has_token: true, token: "tok" }],
  });
  assert.equal(panel.enabled, true);
  const text = JSON.stringify(panel);
  for (const leak of ["secret-value", "AAAA", "\"tok\""]) assert.ok(!text.includes(leak), leak);
  assert.equal(panel.secrets[0]?.mask, "••••••••");
  assert.equal(panel.connections[0]?.hasToken, true);
  assert.equal(panel.links[0]?.label, "https://example.com/list.csv");
});

test("a payload for any other vault is refused", () => {
  for (const vault of ["tank.vault", "lab.vault", "tiger.vault", "zenas.vault"]) {
    const panel = mapVaultPayload({ vault, enabled: true, secrets: [{ name: "X", mask: "••••" }] });
    assert.equal(panel.enabled, false, vault);
    assert.equal(panel.message, LOAD_ERROR_MESSAGE);
    assert.deepEqual(panel.secrets, []);
  }
});

test("Tank errors become plain words", () => {
  assert.equal(safeMessage(new Error(`Tank /v1/vaults/base.vault/secrets 409: ${VAULT_OFF_MESSAGE}`), "x"), VAULT_OFF_MESSAGE);
  assert.equal(safeMessage(new Error("Tank 500: stack trace"), "The key was not saved."), "The key was not saved.");
});
