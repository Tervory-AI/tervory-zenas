import type { RegionSignIns } from "@/lib/cli-sign-ins";

const REGION_LABEL = { "us-east": "US East", za: "South Africa (ZA)" } as const;

/** UTC, minute precision: the same instant on every viewer's screen. */
export function when(value: string | null): string {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown";
  return date.toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit",
                                        hour12: false, timeZone: "UTC" }) + " UTC";
}

/** The CLI sign-ins list (review of #506, B3). Presentational; the page passes the revoke server action. */
export function SignInList({ regions, action, notice }: { regions: RegionSignIns[]; action: (form: FormData) => Promise<void>;
                                                          notice?: string }) {
  const items = regions.flatMap(r => (r.ok ? r.items : []));
  return (
    <main className="access-page">
      <header className="access-heading"><div>
        <p className="access-eyebrow">YOUR WORKSPACE / CLI SIGN-INS</p>
        <h1>CLI sign-ins<span>.</span></h1>
        <p>Every computer where <code>tervory login</code> was approved for you in this organization. Revoke any you do not recognise: it stops working at once.</p>
      </div></header>
      {notice && <p className="access-message" role="status">{notice}</p>}
      {regions.filter(r => !r.ok).map(r => (
        <p className="access-message" role="alert" key={r.region}>{REGION_LABEL[r.region]}: {r.ok ? "" : r.message}</p>
      ))}
      <section className="access-card">
        {items.length === 0 ? (
          <div className="access-empty"><h3>No active CLI sign-ins</h3><p>Sign-ins appear here after you approve <code>tervory login</code>.</p></div>
        ) : items.map(item => (
          <article className="access-connection" key={`${item.region}:${item.token_id}`} data-testid="cli-sign-in">
            <div><strong>{item.device_name}</strong><span className="access-badge">{REGION_LABEL[item.region]}</span></div>
            <p>Device name as the device reported it · Signed in {when(item.created_at)} · Last used {when(item.last_used_at)}</p>
            <form action={action}>
              <input type="hidden" name="region" value={item.region} />
              <input type="hidden" name="token_id" value={item.token_id} />
              <button className="access-revoke" type="submit">Revoke</button>
            </form>
          </article>
        ))}
        <aside className="access-note"><strong>Approved a code someone sent you?</strong>
          <p>Approving a code gives whoever started that sign-in your access. Revoke it here, then run <code>tervory login</code> again on your own computer if you need the CLI.</p></aside>
      </section>
    </main>
  );
}
