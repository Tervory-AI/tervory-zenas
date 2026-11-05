import type { MarketDocument, WarningPoint } from "@/lib/tank";
import { formatDay, formatRefreshed } from "./format";

function valueLabel(raw: string) {
  const [whole, fraction] = raw.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const rest = fraction?.replace(/0+$/, "");
  return rest ? `${grouped}.${rest}` : grouped;
}
function Point({ point, label }: { point?: WarningPoint | null; label: string }) {
  if (!point) return <p>{label}: no saved observation.</p>;
  return <p>{label}: <a href={point.href}>{point.title}, {formatDay(point.date)} — {point.display_value ?? `${valueLabel(point.value)} ${point.unit}`}</a></p>;
}
export function WarningEvidence({ source }: { source: MarketDocument }) {
  const data = source.calculation!;
  return <main className="ml ml-source ml-warning-evidence"><div className="ml-body">
    <a href="/market-lab#warnings">Back to Warnings</a>
    <h1>{source.name}</h1>
    <p>Saved on {formatRefreshed(source.fetched_at)}. This record keeps the figures used at that time.</p>
    {data.kind === "resolution" ? <section id="resolution">
      <h2>{data.outcome === "hit" ? "Came true" : data.outcome === "miss" ? "Did not come true" : "Not measurable"}</h2>
      <p>Window: {formatDay(data.window_start ?? "")} to {formatDay(data.window_end ?? "")}.</p>
      {data.outcome === "not_measurable" ? <p>There were not enough saved observations to decide whether the change happened. Missing data is not counted as a failed warning.</p> : <p>{data.outcome === "hit" ? "A saved observation showed the expected change during its window." : "The saved observations covered the window, but none showed the expected change."}</p>}
      <Point point={data.baseline} label="Starting observation" />
      <Point point={data.selected} label={data.outcome === "not_measurable" ? "Last saved observation in the window" : "Observation used to score the warning"} />
      {data.coverage_complete ? <Point point={data.coverage_end} label="Check covering the end of the window" /> : null}
      <p><a href={data.original}>Read the warning as it was issued</a></p>
      {data.observations?.length ? <details><summary>Observations during the window</summary>{data.observations.map(point => <Point key={point.href} point={point} label="Saved observation" />)}</details> : <p>No observations were saved during the window.</p>}
    </section> : <>
      <section id="upstream"><h2>What changed first</h2>
        {data.current ? <><Point point={data.current.previous} label="Before" /><Point point={data.current.point} label="After" /><p>The percentage change is the difference between these figures divided by the earlier figure.</p></> : <p>No current change was found in the saved history.</p>}
      </section>
      <section id="history"><h2>What followed before</h2>
        <p>{data.hits ?? 0} of {data.episode_count ?? 0} past episodes showed the expected change. {data.hit_rate === null || data.hit_rate === undefined ? "There are no completed past episodes to compare yet." : `That is ${(data.hit_rate * 100).toFixed(0)}%.`}</p>
        <p>Warnings need at least {data.config?.min_episodes} completed past episodes before it can warn. Changes close together count as one past episode when their follow-up periods overlap. An episode with missing coverage is excluded from both the hit count and the total.</p>
        <p>These changes happened one after the other. That does not prove one caused the other or that it will happen again.</p>
        {data.episodes?.length ? data.episodes.map((episode, index) => <article key={episode.start}>
          <h3>Past episode {index + 1}: {formatDay(episode.start)}</h3>
          <p>{episode.hit ? `The expected change happened after ${episode.lag_days} days.` : "The expected change did not happen during the observation window."} The window ended {formatDay(episode.end)}.</p>
          <Point point={episode.previous} label={`${data.upstream_name} before`} /><Point point={episode.upstream} label={`${data.upstream_name} after`} />
          <Point point={episode.baseline} label={`${data.downstream_name} before`} /><Point point={episode.outcome} label={`${data.downstream_name} afterward`} />
          <details><summary>Saved observations for this episode</summary>{episode.observations.map(point => <Point key={point.href} point={point} label="Observation" />)}</details>
        </article>) : <p>No completed past episodes with enough saved observations yet.</p>}
      </section>
      <section id="window"><h2>How the window was chosen</h2>
        {data.lag_min !== null && data.lag_min !== undefined ? <p>Past changes that showed the expected change took {data.lag_min}–{data.lag_max} days. A new warning uses that range after the first change.</p> : <p>There is not enough evidence to choose a warning window.</p>}
        <p>Historical episodes are checked from {data.config?.min_lag_days} to {data.config?.max_lag_days} days after the first change. Coverage cannot have gaps longer than {data.config?.max_gap_days} days.</p>
      </section>
    </>}
  </div></main>;
}
