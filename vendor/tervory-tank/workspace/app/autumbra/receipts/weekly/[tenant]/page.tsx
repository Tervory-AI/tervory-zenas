import { mayReadWeeklyReceipts } from "@/lib/autumbra-receipts-access";
import { describeReceipt } from "@/lib/autumbra-copy";
import { loadWeeklyReceipts } from "@/lib/autumbra-weekly";

export const metadata = { referrer: "no-referrer" };

/** Weekly owner-update receipt page — token or signed-in owner of THAT tenant. No workspace menu. */
export default async function WeeklyReceiptPage({
  params,
  searchParams,
}: {
  params: Promise<{ tenant: string }>;
  searchParams?: Promise<{ token?: string }>;
}) {
  const resolved = await params;
  const tenant = resolved.tenant;
  const query = searchParams ? await searchParams : {};
  const token = typeof query.token === "string" ? query.token : "";
  const allowed = await mayReadWeeklyReceipts(tenant, token);
  if (!allowed) {
    return (
      <section style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 720, overflowWrap: "anywhere" }}>
        <h1>Your Autumbra weekly update</h1>
        <p>This link is invalid or has expired.</p>
        <p>Use the receipt link in your Autumbra update email.</p>
      </section>
    );
  }
  const { receipts, note, timeZone, available, unauthorized } = await loadWeeklyReceipts(tenant, token || null);

  return (
    <section style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 720, overflowWrap: "anywhere" }}>
      <h1>Your Autumbra weekly update</h1>
      <p>
        This is the receipt page linked from your Autumbra owner email. It lists what Autumbra
        checked, what it found, and what it did for your site this week.
      </p>
      {note && receipts.length === 0 ? <p style={{ color: "#6b7280" }}>{note}</p> : null}
      <p>{available === false ? "—" : receipts.length} recorded</p>
      {!receipts.length && <dl><dt>Cost</dt><dd>—</dd><dt>Would have cost</dt><dd>—</dd></dl>}
      {receipts.length === 0 ? (
        <p>{unauthorized ? "This link is invalid or has expired." : available === false
          ? "Reconnect Autumbra to load this week's receipts."
          : "No receipts for this week yet. Connect your site's log feed to start a check."}</p>
      ) : (
        <ul style={{ paddingLeft: "1.25rem", lineHeight: 1.6, listStyle: "disc" }}>
          {receipts.map((row, index) => {
            const id = row.id || `receipt-${index}`;
            const href = row.url && row.url.includes("token=") ? row.url : "";
            const { title, when } = describeReceipt(row, timeZone);
            return (
              <li key={id} style={{ marginBottom: "1rem" }}>
                <div>{title}</div>
                <dl>
                  <dt>Cost</dt><dd>{row.cost_usd === undefined ? "—" : `$${row.cost_usd.toFixed(6)}`}</dd>
                  <dt>Would have cost</dt><dd>{row.would_have_cost_usd === undefined ? "—" : `$${row.would_have_cost_usd.toFixed(6)}`}</dd>
                </dl>
                {when ? (
                  <div style={{ color: "#6b7280", fontSize: "0.95rem" }}>{when}</div>
                ) : null}
                {row.advisory_url ? (
                  <div>
                    <a href={row.advisory_url}>Public advisory</a>
                  </div>
                ) : null}
                {href ? (
                  <div style={{ marginTop: "0.25rem" }}>
                    <a href={href}>Undo this block</a>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      <p style={{ marginTop: "1.5rem", fontSize: "0.95rem", color: "#6b7280" }}>
        Nothing from your logs is kept. If you need help, reply to your Autumbra update email.
      </p>
    </section>
  );
}
