import { DEADLINE_ALERTS, DEADLINE_NOTE, DEADLINE_ROWS } from "./frame";

/** Deadlines column: Upcoming, then Alerts. Dates appear only when a matter has them. */
export function DeadlinesPanel() {
  return (
    <section className="zn-deadlines" aria-label="Deadlines">
      <header className="zn-head">
        <div>
          <h1>Deadlines</h1>
          <p>Court deadlines computed from the rules with Catala. Every date shows the rule it comes from.</p>
        </div>
      </header>
      <aside className="zn-right" aria-label="Upcoming and alerts">
        <article className="zn-card zn-upcoming">
          <header>
            <h2>Upcoming</h2>
            <p>Next 30 days</p>
          </header>
          <ul>
            {DEADLINE_ROWS.length === 0 ? <li>No matters yet.</li> : DEADLINE_ROWS.map((row, index) => (
              <li className={index === 0 ? "soon" : ""} key={`${row.title}-${index}`}>
                <strong>{row.title}</strong>
                <span>{row.meta}</span>
                <span className="zn-chip">Computed by Catala</span>
              </li>
            ))}
          </ul>
        </article>
        <article className="zn-card zn-alerts">
          <h2>Alerts</h2>
          <ul>
            {DEADLINE_ALERTS.map((alert) => (
              <li key={alert.title}>
                <strong>{alert.title}</strong>
                <span>{alert.detail}</span>
              </li>
            ))}
          </ul>
        </article>
        <p className="zn-fine">{DEADLINE_NOTE}</p>
      </aside>
    </section>
  );
}
