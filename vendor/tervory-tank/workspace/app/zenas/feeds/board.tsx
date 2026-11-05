"use client";

import { useEffect, useState } from "react";
import { emptyConvert, type PublicConvert, type PublicScore, type PublicSource } from "@/app/api/zenas/feeds/public";

type Region = "us-east" | "za";

function statusLabel(status: string): string {
  if (!status) return "—";
  return status.replace(/_/g, " ");
}

export function FeedSwitch({
  initialRegion,
  initialSources,
  initialScores,
  initialReview,
  initialConvert,
  note,
}: {
  initialRegion: Region;
  initialSources: PublicSource[];
  initialScores: PublicScore[];
  initialReview: "on" | "off";
  initialConvert: PublicConvert;
  note: string;
}) {
  const [region, setRegion] = useState<Region>(initialRegion);
  const [sources, setSources] = useState(initialSources);
  const [scores, setScores] = useState(initialScores);
  const [review, setReview] = useState<"on" | "off">(initialReview);
  const [counts, setCounts] = useState<PublicConvert>(initialConvert);
  const [message, setMessage] = useState(note);
  const [busy, setBusy] = useState("");
  const [watching, setWatching] = useState(initialConvert.running);
  const [why, setWhy] = useState("");
  const anyRunning = sources.some((row) => row.last_status === "running");

  useEffect(() => {
    if (!anyRunning) return;
    let stop = false;
    const timer = setInterval(() => {
      void refreshSources();
    }, 3000);

    async function refreshSources() {
      const res = await fetch(`/api/zenas/feeds/region?region=${region}`);
      if (!res.ok || stop) return;
      const body = (await res.json()) as { sources?: PublicSource[] };
      if (stop) return;
      setSources(body.sources ?? []);
    }

    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, [anyRunning, region]);

  useEffect(() => {
    if (!watching) return;
    let stop = false;
    const timer = setInterval(() => {
      void refresh();
    }, 3000);

    async function refresh() {
      const res = await fetch(`/api/zenas/feeds/region/convert?region=${region}`);
      if (!res.ok || stop) return;
      const body = (await res.json()) as PublicConvert;
      if (stop) return;
      setCounts(body);
      if (!body.running) {
        setWatching(false);
        setMessage("");
      }
    }

    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, [watching, region]);

  async function choose(next: Region) {
    setRegion(next);
    setWhy("");
    setMessage("");
    const [feeds, scored, switched, converted] = await Promise.all([
      fetch(`/api/zenas/feeds/region?region=${next}`),
      fetch(`/api/zenas/selftest?region=${next}`),
      fetch(`/api/zenas/review-switch?region=${next}`),
      fetch(`/api/zenas/feeds/region/convert?region=${next}`),
    ]);
    if (!feeds.ok) {
      setSources([]);
      setMessage("Feeds could not be loaded.");
    } else {
      const body = (await feeds.json()) as { sources?: PublicSource[]; note?: string };
      setSources(body.sources ?? []);
      setMessage(body.note ?? "");
    }
    if (scored.ok) {
      const body = (await scored.json()) as { runs?: PublicScore[] };
      setScores(body.runs ?? []);
    } else {
      setScores([]);
    }
    if (switched.ok) {
      const body = (await switched.json()) as { review?: string };
      setReview(body.review === "on" ? "on" : "off");
    }
    if (converted.ok) {
      const body = (await converted.json()) as PublicConvert;
      setCounts(body);
      setWatching(body.running);
    } else {
      setCounts(emptyConvert(next));
      setWatching(false);
    }
  }

  async function convertCases() {
    setBusy("convert");
    setMessage("");
    const res = await fetch("/api/zenas/feeds/region/convert", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ region }),
    });
    setBusy("");
    if (!res.ok) {
      const body = (await res.json().catch(() => null)) as { error?: string } | null;
      setMessage(body?.error || "Cases could not be converted.");
      return;
    }
    const body = (await res.json()) as PublicConvert;
    setCounts(body);
    if (body.status === "running" || body.status === "already running") {
      setMessage(body.status);
      setWatching(true);
      return;
    }
    setMessage("Cases could not be converted.");
  }

  async function flipReview() {
    const next = review === "on" ? "off" : "on";
    setBusy("review");
    const res = await fetch("/api/zenas/review-switch", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ region, review: next }),
    });
    setBusy("");
    if (!res.ok) {
      const body = (await res.json().catch(() => null)) as { error?: string } | null;
      setMessage(body?.error || "The switch did not change.");
      return;
    }
    setReview(next);
  }

  async function load(source: string) {
    setBusy(source);
    setMessage("");
    const res = await fetch("/api/zenas/feeds/region/load", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ source, region }),
    });
    setBusy("");
    if (!res.ok) {
      const body = (await res.json().catch(() => null)) as { error?: string } | null;
      setMessage(body?.error || "The load did not start.");
      return;
    }
    const body = (await res.json().catch(() => null)) as { status?: string } | null;
    const status = body?.status === "already running" ? "already running" : "running";
    await choose(region);
    setMessage(status);
  }

  return (
    <>
      <div className="switch" role="group" aria-label="Region">
        <button type="button" className={region === "us-east" ? "on" : ""} onClick={() => choose("us-east")}>
          US
        </button>
        <button type="button" className={region === "za" ? "on" : ""} onClick={() => choose("za")}>
          South Africa
        </button>
      </div>
      {message ? <p className="note">{message}</p> : null}
      <h2>Decided cases</h2>
      <p className="note">
        <span className="meta">{counts.decided}</span> decided cases loaded.{" "}
        <span className="meta">{counts.converted}</span> converted.{" "}
        <span className="meta">{counts.held_out}</span> held out.{" "}
        <span className="meta">{counts.failed}</span> failed.
      </p>
      {counts.failures.length > 0 ? (
        <ul>
          {counts.failures.map((row) => (
            <li key={row.reason}>
              <span className="meta">{row.count}</span> {row.reason}
            </li>
          ))}
        </ul>
      ) : null}
      {counts.cases.length > 0 ? (
        <ul className="fails">
          {counts.cases.map((row, index) => {
            const anchor = `why-${index}`;
            const open = why === row.id;
            return (
              <li key={row.id}>
                <span className="meta">{row.citation || row.id}</span> {row.reason}{" "}
                <a
                  className="why"
                  href={`#${anchor}`}
                  aria-expanded={open}
                  onClick={(event) => {
                    event.preventDefault();
                    setWhy(open ? "" : row.id);
                  }}
                >
                  Why
                </a>
                {open ? (
                  <div className="why-panel" id={anchor}>
                    <p className="meta">Finish reason: {row.finish_reason || "—"}</p>
                    <p className="meta">
                      Tokens in {row.tokens_in}. Tokens out {row.tokens_out}.
                    </p>
                    {row.quote_part ? (
                      <p className="meta">
                        {row.quote_part}: {row.quote_excerpt || "—"}
                      </p>
                    ) : null}
                    <pre>{row.answer || "No answer was stored."}</pre>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
      <button type="button" disabled={busy === "convert"} onClick={convertCases}>
        {busy === "convert" || counts.running ? "running" : "Convert cases"}
      </button>
      <p className="note">
        Lawyer review is {review}.{" "}
        <button type="button" disabled={busy === "review"} onClick={flipReview}>
          {review === "on" ? "Turn lawyer review off" : "Turn lawyer review on"}
        </button>
      </p>
      <h2>Test scores</h2>
      <p className="note">LegalBench rows are a code baseline, not a model score.</p>
      {scores.length === 0 ? <p>No scores yet.</p> : (
        <div className="zf-scroll">
        <table>
          <thead>
            <tr>
              <th>Area</th>
              <th>Kind</th>
              <th>Cases</th>
              <th>Outcome</th>
              <th>Elements</th>
              <th>Citations</th>
              <th>Cost</th>
            </tr>
          </thead>
          <tbody>
            {scores.map((row) => (
              <tr key={`${row.kind}-${row.area}-${row.set}`}>
                <td>{row.area || row.jurisdiction}</td>
                <td>{row.error ? "error" : row.label || row.set || row.kind}</td>
                <td className="meta">{row.cases}</td>
                {row.error ? (
                  <td colSpan={4}>{row.reason}</td>
                ) : (
                  <>
                    <td className="meta">{row.outcome_agreement}%</td>
                    <td className="meta">{row.element_agreement}%</td>
                    <td className="meta">{row.citations_valid}%</td>
                    <td className="meta">{row.cost_per_case}</td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
      <h2>Sources</h2>
      <div className="zf-scroll">
      <table>
        <thead>
          <tr>
            <th>Source</th>
            <th>Documents</th>
            <th>Status</th>
            <th>Detail</th>
            <th>Last load</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {sources.map((row) => (
            <tr key={row.id}>
              <td>{row.title}</td>
              <td className="meta">{row.documents}</td>
              <td className="meta">{statusLabel(row.last_status)}</td>
              <td>
                {row.id === "courtlistener" && row.detail.includes("snapshot ") ? (
                  <span className="meta">{row.detail}</span>
                ) : (
                  row.detail || "—"
                )}
              </td>
              <td className="meta">{row.last_load ?? "—"}</td>
              <td>
                <button type="button" disabled={busy === row.id} onClick={() => load(row.id)}>
                  {busy === row.id || row.last_status === "running" ? "running" : "Load now"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </>
  );
}
