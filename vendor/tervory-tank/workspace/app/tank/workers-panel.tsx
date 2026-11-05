import { WORKERS_LINE, workerRows, type WorkersFeed } from "./workers";

export function WorkersPanel({ feed }: { feed: WorkersFeed }) {
  const rows = workerRows(feed);
  return (
    <section className="tk-section" aria-label="Workers">
      <h2>Workers</h2>
      <p className="tk-lead">{WORKERS_LINE}</p>
      {!feed.available ? (
        <p className="tk-none">Tank could not reach the workers.</p>
      ) : rows.length ? (
        <div className="tk-workers">
          <table>
            <thead>
              <tr>
                <th scope="col">Issue</th>
                <th scope="col">Status</th>
                <th scope="col">Pull request</th>
                <th scope="col">Minutes</th>
                <th scope="col">Model cost</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key}>
                  <td>
                    <span className="tk-wk-issue">{row.issue}</span>
                    {row.brief ? <span className="tk-wk-brief">{row.brief}</span> : null}
                  </td>
                  <td>{row.status}</td>
                  <td>
                    {row.pr ? (
                      <a href={row.pr.href} target="_blank" rel="noreferrer">
                        {row.pr.label}
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="tk-wk-num">{row.minutes}</td>
                  <td className="tk-wk-num">{row.cost}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="tk-none">No worker tasks yet.</p>
      )}
    </section>
  );
}
