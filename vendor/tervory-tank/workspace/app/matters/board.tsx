"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { MatterRow } from "@/app/api/zenas/rules";

export function MatterBoard({ initial, note }: { initial: MatterRow[]; note: string }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function openMatter(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const dataIn = new FormData(form);
    setBusy(true);
    setError(null);
    const res = await fetch("/api/zenas/matters", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ title: dataIn.get("title"), jurisdiction: dataIn.get("jurisdiction") }),
    });
    const data = await res.json().catch(() => ({}));
    setBusy(false);
    if (!res.ok) {
      setError(typeof data.error === "string" ? data.error : "The matter was not opened.");
      return;
    }
    form.reset();
    router.refresh();
  }

  async function upload(event: React.FormEvent<HTMLFormElement>, matterId: string) {
    event.preventDefault();
    const form = event.currentTarget;
    const dataIn = new FormData(form);
    setBusy(true);
    setError(null);
    const res = await fetch(`/api/zenas/matters/${matterId}/documents`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ kind: dataIn.get("kind"), text: dataIn.get("text") }),
    });
    const data = await res.json().catch(() => ({}));
    setBusy(false);
    if (!res.ok) {
      setError(typeof data.error === "string" ? data.error : "The document was not saved.");
      return;
    }
    form.reset();
    router.refresh();
  }

  return (
    <>
      <form className="card" onSubmit={openMatter}>
        <h2>Open a matter</h2>
        <input name="title" required placeholder="What is this matter about?" />
        <select name="jurisdiction" defaultValue="us" aria-label="Law">
          <option value="us">United States law</option>
          <option value="za">South African law</option>
          <option value="both">Both</option>
        </select>
        <button className="primary" disabled={busy} type="submit">Open</button>
      </form>
      {error && <p className="error">{error}</p>}
      {note && <p className="note">{note}</p>}
      {initial.length === 0 && <p className="note">No matters yet.</p>}
      {initial.map((matter) => (
        <article className="card" key={matter.id}>
          <h2>{matter.title}</h2>
          <p className="meta">{matter.jurisdiction} · {matter.area || "area pending"} · {matter.status}</p>
          {matter.documents.length === 0 ? (
            <p className="note">Drafts and memos appear here after a lawyer releases them.</p>
          ) : (
            matter.documents.map((doc) => (
              <section key={doc.id}>
                <p className="meta">{doc.kind} · version {doc.version} · released</p>
                <p className="draft">{doc.body}</p>
              </section>
            ))
          )}
          <form onSubmit={(event) => upload(event, matter.id)}>
            <input name="kind" required placeholder="Kind, for example notes" />
            <textarea name="text" required rows={4} placeholder="Document text" />
            <button className="primary" disabled={busy} type="submit">Upload</button>
          </form>
        </article>
      ))}
    </>
  );
}
