"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { FeedRow } from "@/app/api/zenas/rules";

export function FeedBoard({ region, sources, note }: { region: string; sources: FeedRow[]; note: string }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function load(source: string) {
    setBusy(source);
    setError(null);
    const res = await fetch("/api/zenas/feeds/load", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ source }),
    });
    const data = await res.json().catch(() => ({}));
    setBusy(null);
    if (!res.ok) {
      setError(typeof data.error === "string" ? data.error : "The load did not start.");
      return;
    }
    router.refresh();
  }

  return (
    <section className="section zenas-feeds">
      <h2>Zenas feeds</h2>
      <p className="hint">Sources on this server ({region || "this region"}). A key is shown by name only.</p>
      {note && <p className="empty">{note}</p>}
      {error && <p className="form-error">{error}</p>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>Key</th>
              <th>Set</th>
              <th>Documents</th>
              <th>Last load</th>
              <th>Errors</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {sources.length === 0 && (
              <tr><td colSpan={7}>No sources reported yet.</td></tr>
            )}
            {sources.map((source) => (
              <tr key={source.id}>
                <td>{source.title}</td>
                <td className="num">{source.key_name}</td>
                <td>{source.key_set}</td>
                <td className="num">{source.documents}</td>
                <td className="num">{source.last_load ?? "never"}</td>
                <td>{source.errors.join("; ") || "—"}</td>
                <td>
                  <button type="button" className="save" disabled={busy === source.id} onClick={() => load(source.id)}>
                    {busy === source.id ? "Starting" : "Load now"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
