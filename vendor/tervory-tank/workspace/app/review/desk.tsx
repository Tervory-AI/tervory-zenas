"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { ReviewItem } from "@/app/api/zenas/rules";
import { usesMatterRelease } from "./release-path";

export function ReviewDesk({ items, note }: { items: ReviewItem[]; note: string }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function sign(event: React.FormEvent<HTMLFormElement>, item: ReviewItem) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const decision = form.get("decision") === "rejected" ? "rejected" : "approved";
    const changes = String(form.get("changes") ?? "");
    setBusy(true);
    setError(null);
    setConfirm(null);

    if (usesMatterRelease(decision, item.matterId)) {
      const res = await fetch(`/api/zenas/matters/${item.matterId}/release`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          document_id: item.documentId,
          jurisdiction: item.jurisdiction,
          changes,
          memo: item.body,
          area: item.area,
          outcome: item.matches[0]?.outcome ?? "",
        }),
      });
      const data = await res.json().catch(() => ({}));
      setBusy(false);
      if (!res.ok) {
        setError(typeof data.error === "string" ? data.error : "The draft was not released.");
        return;
      }
      setConfirm(data.shared === false ? "Released to client (not shared for training)." : "Released to client");
      router.refresh();
      return;
    }

    const res = await fetch(`/api/zenas/documents/${item.documentId}/reviews`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        decision,
        changes,
        jurisdiction: item.jurisdiction,
      }),
    });
    const data = await res.json().catch(() => ({}));
    setBusy(false);
    if (!res.ok) {
      setError(typeof data.error === "string" ? data.error : "The sign-off was not recorded.");
      return;
    }
    setConfirm(decision === "approved" ? "Released to client" : "Sent back");
    router.refresh();
  }

  return (
    <section className="zr-card" aria-labelledby="waiting-title">
      <h2 id="waiting-title">Waiting for review</h2>
      {note && <p className="note">{note}</p>}
      {confirm && <p className="confirm">{confirm}</p>}
      {error && <p className="error">{error}</p>}
      {items.length === 0 && !note && <p className="note">Nothing is waiting in your jurisdictions.</p>}
      {items.map((item) => (
        <article className="zr-item-card" key={item.documentId}>
          <h3>{item.title}</h3>
          <p className="meta">{item.jurisdiction} · {item.area} · {item.state}</p>
          <div className="split">
            <section>
              <h3>Draft</h3>
              <p className="draft">{item.body}</p>
            </section>
            <section>
              <h3>Sources</h3>
              {item.sources.length === 0 && <p className="note">No sources were attached.</p>}
              {item.sources.map((source) => (
                <p key={source.citation}>
                  <span className="meta">{source.citation}</span><br />{source.quote}
                </p>
              ))}
              <h3>Worked matters</h3>
              {item.matches.length === 0 && <p className="note">No worked matter was matched.</p>}
              {item.matches.map((match) => (
                <div key={match.id}>
                  <p className="meta">{match.outcome || "outcome pending"} · {match.jurisdiction}</p>
                  {match.findings.map((finding) => <p key={finding}>{finding}</p>)}
                </div>
              ))}
            </section>
          </div>
          <form onSubmit={(event) => sign(event, item)}>
            <textarea name="changes" rows={4} placeholder="What you changed. Write none if you changed nothing." />
            <div className="actions">
              <button className="primary" disabled={busy} name="decision" value="approved" type="submit">
                {item.matterId ? "Release to client" : "Approve"}
              </button>
              <button className="secondary" disabled={busy} name="decision" value="rejected" type="submit">Reject</button>
            </div>
          </form>
        </article>
      ))}
    </section>
  );
}
