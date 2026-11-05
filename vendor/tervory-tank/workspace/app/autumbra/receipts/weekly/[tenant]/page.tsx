import { mayReadWeeklyReceipts } from "@/lib/autumbra-receipts-access";
import { describeReceipt } from "@/lib/autumbra-copy";
import { loadWeeklyReceipts } from "@/lib/autumbra-weekly";

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
      <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 720 }}>
        <h1>Your Autumbra weekly update</h1>
        <p>This link is invalid or has expired.</p>
      </main>
    );
  }
  const { receipts, note, timeZone } = await loadWeeklyReceipts(tenant, token || null);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 720 }}>
      <h1>Your Autumbra weekly update</h1>
      <p>
        This is the receipt page linked from your Autumbra owner email. It lists what Autumbra
        checked, what it found, and what it did for your site this week.
      </p>
      {note && receipts.length === 0 ? <p style={{ color: "#6b7280" }}>{note}</p> : null}
      {receipts.length === 0 ? (
        <p>No receipts for this week yet.</p>
      ) : (
        <ul style={{ paddingLeft: "1.25rem", lineHeight: 1.6, listStyle: "disc" }}>
          {receipts.map((row, index) => {
            const id = row.id || `receipt-${index}`;
            const href = row.url && row.url.includes("token=") ? row.url : "";
            const { title, when } = describeReceipt(row, timeZone);
            return (
              <li key={id} style={{ marginBottom: "1rem" }}>
                <div>{title}</div>
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
        Nothing from your logs is kept — we read them, acted, and deleted them. If a shield
        caused a problem, use Undo this block above or the Undo link in that same email.
      </p>
    </main>
  );
}
