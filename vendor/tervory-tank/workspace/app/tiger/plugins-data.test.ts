import assert from "node:assert/strict";
import { test } from "node:test";
import { mapVaultPayload, offPanel, VAULT_OFF_MESSAGE } from "./plugins-data.ts";

test("an off vault shows the exact message and no stored rows", () => {
  const panel = mapVaultPayload({
    enabled: false,
    message: "",
    secrets: [{ name: "PRICE", value: "super-secret" }],
  });
  assert.equal(panel.enabled, false);
  assert.equal(panel.message, VAULT_OFF_MESSAGE);
  assert.deepEqual(panel.secrets, []);
  assert.equal(offPanel().message, VAULT_OFF_MESSAGE);
});

test("a saved key reaches the browser only as dots", () => {
  const panel = mapVaultPayload({
    enabled: true,
    secrets: [{ name: "PRICE", purpose: "feed", mask: "", value: "super-secret", saved_at: "2026-10-17" }],
    files: [{ id: "1", name: "a.csv", content: "secret-bytes" }],
    connections: [{ id: "2", name: "tool", has_token: true, token: "hidden-token" }],
  });
  const text = JSON.stringify(panel);
  assert.equal(text.includes("super-secret"), false);
  assert.equal(text.includes("secret-bytes"), false);
  assert.equal(text.includes("hidden-token"), false);
  assert.equal(panel.secrets[0]?.mask, "••••••••");
  assert.equal(panel.connections[0]?.hasToken, true);
});
