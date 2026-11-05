import type { MarketDocument } from "@/lib/tank";
import { WarningEvidence } from "./warning-evidence";
import { formatRefreshed } from "./format";

/** Shared by the signed-in route and the isolated local fixture proof. */
export function SourceDocumentView({ source }: { source: MarketDocument }) {
  if (source.kind === "uploaded_file") return <main className="ml ml-source"><div className="ml-body">
    <a href="/market-lab">Back to Market Lab</a><h1>{source.name}</h1>
    <p>Uploaded on {formatRefreshed(source.fetched_at)}. The text below was read from your file.</p>
    {source.reason ? <p>{source.reason}</p> : null}
    {(source.passage_count ?? 0) > source.page_size ? <nav className="ml-source-pages" aria-label="File passages">
      {source.offset > 0 ? <a href={`?page=${source.offset / source.page_size}`}>Previous</a> : <span>Previous</span>}
      <span>Page {source.offset / source.page_size + 1} of {Math.ceil((source.passage_count ?? 0) / source.page_size)}</span>
      {source.offset + source.page_size < (source.passage_count ?? 0) ? <a href={`?page=${source.offset / source.page_size + 2}`}>Next</a> : <span>Next</span>}
    </nav> : null}
    {source.passages?.map(passage => <article id={`passage-${passage.index}`} key={passage.index}>
      <h2>{passage.label}</h2><p style={{ whiteSpace: "pre-wrap" }}>{passage.text}</p>
      {!passage.usable ? <p>These figures did not pass the data checks and are not used in answers.</p> : null}
    </article>)}
  </div></main>;
  if (source.calculation) return <WarningEvidence source={source} />;
  const offset = source.offset;
  const page = Math.floor(offset / source.page_size) + 1;
  const pageCount = Math.max(1, Math.ceil(Math.max(source.point_count, source.event_count) / source.page_size));
  return <main className="ml ml-source"><div className="ml-body">
    <a href="/market-lab">Back to Market Lab</a>
    <h1>{source.name}</h1>
    <p>Stored on {formatRefreshed(source.fetched_at)}. These are the figures and notices saved during that refresh.</p>
    {source.presentation?.publisher ? <p>Source: {source.presentation.publisher_url ? <a href={source.presentation.publisher_url} rel="noreferrer" target="_blank">{source.presentation.publisher}</a> : source.presentation.publisher}.</p> : null}
    {source.presentation?.notice ? <p>{source.presentation.notice}{source.presentation.notice.includes("Creative Commons") ? <> <a href="https://creativecommons.org/licenses/by/4.0/" rel="noreferrer" target="_blank">Read the reuse licence</a>.</> : null}</p> : null}
    {source.presentation?.explanation ? <p>{source.presentation.explanation}</p> : null}
    {source.downloads.length ? <><h2>Source downloads</h2>
    <ul>{source.downloads.map((download, index) => <li key={`${download.url}-${index}`}><a href={download.url} rel="noreferrer" target="_blank">Download original data ({new URL(download.url).hostname})</a></li>)}</ul></> : null}
    {pageCount > 1 ? <nav className="ml-source-pages" aria-label="Source pages">
      {page > 1 ? <a href={`?page=${page - 1}`}>Previous</a> : <span aria-disabled="true">Previous</span>}
      <span>Page {page} of {pageCount}</span>
      {page < pageCount ? <a href={`?page=${page + 1}`}>Next</a> : <span aria-disabled="true">Next</span>}
    </nav> : null}
    {source.points.length ? <><h2>Stored observations</h2>
    <p>{source.point_count === 1 ? "1 stored observation." : `Showing ${(offset + 1).toLocaleString("en-US")}–${(offset + source.points.length).toLocaleString("en-US")} of ${source.point_count.toLocaleString("en-US")} observations. Newest first.`}</p>
    {source.points.some((point) => point.revision_label) ? <p>For each date, Current figure is the latest version in this saved document. Earlier figures are labelled with the source’s version.</p> : null}
    <div className="ml-source-table" role="region" aria-label="Stored observations" tabIndex={0}><table>
      <thead><tr><th scope="col">Observation</th><th scope="col">Area</th><th scope="col">Period</th><th scope="col">Value</th><th scope="col">Unit</th></tr></thead>
      <tbody>{source.points.map((point) => <tr id={`point-${point.index}`} key={point.index}>
        <th scope="row">{point.display_title}{point.withdrawn ? " — not used in answers" : ""}</th><td>{point.geo_name}</td><td>{point.date_label}</td>
        <td><span className="ml-source-value">{point.display_value}</span>{point.revision_label ? <small className="ml-source-revision">{point.revision_label}</small> : null}</td><td>{point.unit_label}</td>
      </tr>)}</tbody>
    </table></div></> : null}
    {source.events.length ? <><h2>Stored notices</h2>{source.events.map((event) => <article id={`event-${event.index}`} key={event.index}>
      <h3>{event.display_title}</h3><p>{event.date_label}</p>{event.kind === "changed" ? <p>The linked page was checked for changes. Its text is not stored.</p> : event.body ? <details><summary>Publisher’s original wording</summary><p>{event.body}</p></details> : null}
      {/^https?:\/\//.test(event.display_url) ? <a href={event.display_url} rel="noreferrer" target="_blank">{event.link_label}</a> : null}
    </article>)}</> : null}
  </div></main>;
}
