"use client";

import { fetchWithDeadline as fetch } from "../../lib/request";
import { useState } from "react";

export function MobileAccount({ name }: { name: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function signOut() {
    setBusy(true); setError("");
    try {
      const response = await fetch("/api/mobile/session", { method: "DELETE", headers: { "X-Tervory-Mobile": "1" }, credentials: "same-origin", redirect: "error" });
      if (!response.ok) throw new Error();
      window.location.replace("/mobile/signed-out");
    } catch { setError("Sign-out could not be confirmed. Please try again."); setBusy(false); }
  }
  return <div aria-label="Mobile account"><span>{name}</span> <button onClick={() => void signOut()} disabled={busy}>{busy ? "Signing out…" : "Sign out"}</button>{error && <p role="alert">{error}</p>}</div>;
}
