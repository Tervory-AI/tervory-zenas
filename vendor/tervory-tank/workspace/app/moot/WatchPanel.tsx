/** Presentational watch panel — used by the public page and viewport fixture tests. */

export type WatchAuthority = { name: string; source_url?: string };

export type WatchTurn = { role: string; text: string };

export type WatchRecord = {
  matchId: string;
  verificationStatus?: "unverified";
  published: boolean;
  title: string;
  fairnessReading: string;
  judgmentOrder: string;
  judgmentReasons?: string[];
  reveal: string;
  transcript?: WatchTurn[];
  authorities?: WatchAuthority[];
  costUsd?: number | null;
};

export function WatchPanel({ row }: { row: WatchRecord }) {
  return (
    <main className="moot-watch" aria-label="Moot watch">
      <h1>{row.title}</h1>
      <p>unverified</p>
      <p>Public watch page (text edition). Nothing spoken here — that is later work.</p>

      <section aria-label="Transcript">
        <h2>Transcript</h2>
        {(row.transcript ?? []).length === 0 ? (
          <p>No transcript.</p>
        ) : (
          (row.transcript ?? []).map((turn, i) => (
            <div className="moot-turn" key={`${turn.role}-${i}`}>
              <div className="moot-turn-role">{turn.role.split("_").join(" ")}</div>
              <p>{turn.text}</p>
            </div>
          ))
        )}
      </section>

      <section aria-label="Judgment">
        <h2>Judgment</h2>
        <p>Order: {row.judgmentOrder}</p>
        <ul>
          {(row.judgmentReasons ?? []).map((reason) => (
            <li key={reason.slice(0, 48)}>{reason}</li>
          ))}
        </ul>
      </section>

      <section aria-label="Fairness check">
        <h2>Fairness check</h2>
        <p>{row.fairnessReading || "Not an AI-against-AI match."}</p>
      </section>

      <section aria-label="Reveal">
        <h2>What the court actually held</h2>
        <p>{row.reveal}</p>
      </section>

      <section aria-label="Authorities">
        <h2>Authorities</h2>
        <ul>
          {(row.authorities ?? []).map((auth) => (
            <li key={auth.name}>
              {auth.source_url ? (
                <a href={auth.source_url}>{auth.name}</a>
              ) : (
                auth.name
              )}
            </li>
          ))}
        </ul>
      </section>

      {row.costUsd != null ? (
        <section aria-label="Cost">
          <h2>Cost</h2>
          <p>{row.costUsd}</p>
        </section>
      ) : null}
    </main>
  );
}
