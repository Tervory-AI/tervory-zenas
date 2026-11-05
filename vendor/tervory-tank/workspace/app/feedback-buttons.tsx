"use client";

import { useState } from "react";

type Verdict = "accepted" | "rejected";

// Thumbs up / down on one answer. Writes the verdict to the ledger row through Tank.
// This is the "outcome" the pack bills on and the graduation loop learns from.
export function FeedbackButtons({ ledgerId, initial }: { ledgerId: string; initial?: Verdict | null }) {
  const [verdict, setVerdict] = useState<Verdict | null>(initial ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(next: Verdict) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/feedback", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ledger_id: ledgerId, feedback: next }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? `HTTP ${res.status}`);
      setVerdict(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="fb">
      <button
        type="button"
        className={`fb-btn ${verdict === "accepted" ? "on" : ""}`}
        onClick={() => send("accepted")}
        disabled={busy}
        title="This answer was useful"
        aria-label="Accept"
      >
        👍
      </button>
      <button
        type="button"
        className={`fb-btn ${verdict === "rejected" ? "on" : ""}`}
        onClick={() => send("rejected")}
        disabled={busy}
        title="This answer was not useful"
        aria-label="Reject"
      >
        👎
      </button>
      {verdict && <span className="fb-note">{verdict}</span>}
      {error && <span className="err"> {error}</span>}
    </span>
  );
}
