"use client";

import { useState } from "react";
import { chosenHome } from "@/app/api/zenas/rules";

export type OrgHome = { orgId: string; name: string; tenantId: string };

export function HomeBoard({ orgs, note }: { orgs: OrgHome[]; note: string }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);

  async function save(event: React.FormEvent<HTMLFormElement>, org: OrgHome) {
    event.preventDefault();
    const home = chosenHome(new FormData(event.currentTarget).get("home_region"));
    setError(null);
    setConfirm(null);
    if (!home) {
      setError("Choose za or us-east.");
      return;
    }
    setBusy(org.orgId);
    const res = await fetch("/api/zenas/tenants", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ tenant_id: org.tenantId, home_region: home }),
    });
    const data = await res.json().catch(() => ({}));
    setBusy(null);
    if (!res.ok) {
      setError(typeof data.error === "string" ? data.error : "The home region was not set.");
      return;
    }
    setConfirm(data.created === true ? `${org.name}: Home region set.` : `${org.name}: Home region was already set.`);
  }

  return (
    <section className="section zenas-homes">
      <h2>Home region</h2>
      <p className="hint">Choose za or us-east. Nothing is selected until you choose. A home can be set once.</p>
      {note && <p className="empty">{note}</p>}
      {confirm && <p className="note">{confirm}</p>}
      {error && <p className="form-error">{error}</p>}
      {orgs.length === 0 && !note && <p className="empty">No organisations yet.</p>}
      {orgs.map((org) => (
        <article className="chart-card" key={org.orgId}>
          <p className="focus-name">{org.name}</p>
          <p className="num evidence">{org.tenantId}</p>
          <form onSubmit={(event) => save(event, org)}>
            <div className="choices">
              <label>
                <input type="radio" name="home_region" value="za" required />
                za
              </label>
              <label>
                <input type="radio" name="home_region" value="us-east" />
                us-east
              </label>
            </div>
            <button className="save" type="submit" disabled={busy === org.orgId}>
              {busy === org.orgId ? "Saving" : "Set home"}
            </button>
          </form>
        </article>
      ))}
    </section>
  );
}
