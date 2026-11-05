import { releaseBatch, rejectBatch, removeBatch } from "./quarantine-actions";
import { quarantineActions, type QuarantineBatch } from "./quarantine";

import { formatRefreshed } from "./format";

const ACTIONS = {
  Release: releaseBatch,
  Reject: rejectBatch,
  Remove: removeBatch,
} as const;

function BatchTable({ rows }: { rows: QuarantineBatch[] }) {
  if (!rows.length) return <p>None.</p>;
  return (
    <div className="ml-quarantine-scroll">
      <table>
        <thead>
          <tr>
            {["Series", "Source", "Region", "Currency", "Unit", "Fetched", "Why it is here", "Review"].map((heading) => (
              <th key={heading}>{heading}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((batch) => (
            <tr key={batch.id}>
              <td>{batch.seriesName}</td>
              <td>{batch.source}</td>
              <td>{batch.region}</td>
              <td>{batch.currency}</td>
              <td>{batch.unit}</td>
              <td className="ml-mono">{formatRefreshed(batch.fetchedAt)}</td>
              <td>{batch.reason || "Waiting for review"}</td>
              <td>
                {quarantineActions(batch.status).map((label) => (
                  <form key={label} action={ACTIONS[label]}>
                    <input type="hidden" name="batch" value={batch.id} />
                    <button className={label === "Release" ? "ml-primary" : ""} type="submit">
                      {label}
                    </button>
                  </form>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function QuarantinePanel({
  batches,
  error,
  listFailed = false,
}: {
  batches: QuarantineBatch[];
  error?: string | null;
  listFailed?: boolean;
}) {
  const held = batches.filter((batch) => batch.status === "held");
  const pooled = batches.filter((batch) => batch.status === "pooled");
  const closed = batches.filter((batch) => batch.status === "rejected" || batch.status === "removed");
  return (
    <section id="quarantine" className="ml-quarantine" aria-labelledby="quarantine-title">
      <h2 id="quarantine-title">Feed review</h2>
      <p>Review records that differ from earlier data before allowing them into answers. Rejected records remain excluded.</p>
      {error ? (
        <p className="ml-quarantine-error" role="alert">
          {error}
        </p>
      ) : null}
      {listFailed ? null : (
        <>
          <h3>Waiting</h3>
          <BatchTable rows={held} />
          <h3>In use</h3>
          <p>Remove stops these records and their related examples from being used.</p>
          <BatchTable rows={pooled} />
          {closed.length ? (
            <>
              <h3>Excluded</h3>
              <BatchTable rows={closed} />
            </>
          ) : null}
        </>
      )}
    </section>
  );
}
