import { listTigerAlerts, listTigerObservations, listTigerWatches, type TigerAlert, type TigerObservation, type TigerWatch } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";
import { addWatch, stopWatch } from "./actions";
import { PriceChart } from "./chart";
import { alertText, everyLabel, EVERY_OPTIONS, lastPriced, latestObservation, money, oneParam, priceChange, pricedPoints, safeHref, statusNote, stockLabel, thresholdLabel, watchTitle, when } from "./format";
import { SubmitButton } from "./submit-button";

export const dynamic = "force-dynamic";

type History = { observations: TigerObservation[]; unavailable: boolean };

export default async function PricesPage({ searchParams }: { searchParams: Promise<{ notice?: string | string[] }> }) {
  const params = await searchParams;
  const note = oneParam(params.notice);
  let orgName = "";
  let loadError: string | null = null;
  let watches: TigerWatch[] = [];
  let alerts: TigerAlert[] = [];
  const history = new Map<string, History>();

  try {
    const tenant = await currentTenant();
    orgName = tenant.orgName;
    const [listed, alerted] = await Promise.all([
      listTigerWatches(tenant.tenantId),
      listTigerAlerts(tenant.tenantId),
    ]);
    watches = Array.isArray(listed.watches) ? listed.watches : [];
    alerts = Array.isArray(alerted.alerts) ? alerted.alerts : [];
    const rows = await Promise.all(watches.map(async (watch): Promise<[string, History]> => {
      try {
        const data = await listTigerObservations(tenant.tenantId, watch.url);
        const observations = Array.isArray(data.observations) ? data.observations : [];
        return [watch.url, { observations, unavailable: false }];
      } catch {
        return [watch.url, { observations: [], unavailable: true }];
      }
    }));
    for (const [url, row] of rows) history.set(url, row);
  } catch (err) {
    loadError = err instanceof Error && err.message.includes("not signed in")
      ? "Sign in to watch prices."
      : "Price checks are not reachable right now.";
  }

  const labels = new Map(watches.map((watch) => [watch.url, watchTitle(watch)]));

  return (
    <div className="prices">
      <div className="head">
        <picture>
          <source srcSet="/brand/tervory-logo-reversed.png" media="(prefers-color-scheme: dark)" />
          <img src="/brand/tervory-logo.png" alt="Tervory" />
        </picture>
        <h1>Prices{orgName ? ` · ${orgName}` : ""}</h1>
      </div>
      <p className="lede">Add a page to watch. Tervory checks the price on a schedule and keeps the history here.</p>
      {note && <p className="notice">{note}</p>}
      {loadError && <p className="notice">{loadError}</p>}

      <form action={addWatch} className="watch-form">
        <label className="wide">
          Page address
          <input name="url" type="url" required placeholder="https://example.com/product" maxLength={2000} />
        </label>
        <label>
          Label
          <input name="label" type="text" placeholder="Shop shelf price" maxLength={200} />
        </label>
        <label>
          How often
          <select name="every" defaultValue="1h">
            {EVERY_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        <label>
          Alert when the price moves more than
          <input name="threshold" type="number" min={0} step="0.1" defaultValue={5} />
        </label>
        <p className="hint wide">The number is a percent. 0 means any change. The first price shows up after the next check.</p>
        <div className="actions">
          <SubmitButton label="Watch this page" pending="Saving…" tone="primary" />
        </div>
      </form>

      <h2>Watches</h2>
      {watches.length === 0 && !loadError && <p className="empty">Nothing is being watched yet. Add a page above.</p>}
      {watches.map((watch) => {
        const row = history.get(watch.url);
        const observations = row?.observations ?? [];
        const points = pricedPoints(observations);
        const change = priceChange(points);
        const latest = latestObservation(observations);
        const priced = lastPriced(observations);
        const noteFor = statusNote(latest?.method);
        const href = safeHref(watch.url);
        const stock = stockLabel(priced?.availability ?? null);
        const lastPrice = priced ? money(priced.price, priced.currency) : "—";
        return (
          <article className="watch" key={watch.id}>
            <header>
              <h3>{watchTitle(watch)}</h3>
              {href ? <a className="page-link" href={href} target="_blank" rel="noreferrer">{watch.url}</a> : <span className="meta">{watch.url}</span>}
            </header>
            <p className="price-line">
              <b className="num">{lastPrice}</b>
              <span className={`num change ${change.direction}`}>{change.text}</span>
              {stock && <span className="meta">{stock}</span>}
            </p>
            <p className="meta num">
              {everyLabel(watch.every)} · {thresholdLabel(watch.threshold)}
              {!watch.enabled && " · Paused"}
            </p>
            {noteFor && <p className={latest?.method === "blocked" ? "blocked" : "notice"}>{noteFor}</p>}
            {row?.unavailable ? <p className="empty">Price history is not available right now.</p> : <PriceChart points={points} />}
            <form action={stopWatch} className="stop">
              <input type="hidden" name="id" value={watch.id} />
              <input type="hidden" name="back" value="/prices" />
              <SubmitButton label="Stop watching" pending="Removing…" tone="ghost" />
            </form>
          </article>
        );
      })}

      <h2>Alerts</h2>
      <p className="hint">A price move past the threshold, or a change in stock.</p>
      {alerts.length === 0 && !loadError && <p className="empty">No alerts yet.</p>}
      {alerts.length > 0 && (
        <table>
          <thead>
            <tr><th>When</th><th>Page</th><th>What changed</th></tr>
          </thead>
          <tbody>
            {alerts.map((alert) => (
              <tr key={alert.id}>
                <td className="num">{when(alert.ts)}</td>
                <td>{labels.get(alert.url) ?? alert.url}</td>
                <td>{alertText(alert)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
