import { verifyUndoToken } from "@/lib/autumbra-undo-token";
import { loadUndoStatus } from "@/lib/autumbra-undo-status";
import { UndoButton } from "../UndoButton";

export const metadata = { referrer: "no-referrer" };

/** One-tap undo landing — linked from the owner update email. Token is the credential. */
export default async function UndoPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams?: Promise<{ token?: string }>;
}) {
  const resolved = await params;
  const query = searchParams ? await searchParams : {};
  const token = typeof query.token === "string" ? query.token : "";

  let alreadyUndone = false;
  let alreadyMessage = "";
  let valid = false;
  if (token) {
    try {
      const payload = verifyUndoToken(token);
      if (payload.receipt_id !== resolved.id) throw new Error("Invalid receipt");
      valid = true;
      const status = await loadUndoStatus(payload.tenant_id, payload.receipt_id || resolved.id);
      if (status.already_undone) {
        alreadyUndone = true;
        alreadyMessage =
          status.detail || "This block was already undone on an earlier time.";
      }
    } catch {
      // Invalid links do not render an action. A valid token can still be
      // used when the optional already-undone status read is unavailable.
    }
  }

  if (!valid) return <section style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 720, overflowWrap: "anywhere" }}>
    <h1>Undo an Autumbra shield</h1>
    <p>This link is invalid or has expired.</p>
    <p>Use the Undo link in your Autumbra update email.</p>
  </section>;

  return (
    <section style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 720, overflowWrap: "anywhere" }}>
      <h1>Undo an Autumbra shield</h1>
      {alreadyUndone ? (
        <p>{alreadyMessage}</p>
      ) : (
        <p>
          Use Undo to remove the shield linked in your Autumbra update email.
        </p>
      )}
      <UndoButton
        receiptId={resolved.id}
        token={token}
        initialAlreadyUndone={alreadyUndone}
        initialMessage={alreadyMessage}
      />
      <p style={{ marginTop: "2rem", fontSize: "0.95rem", color: "#6b7280" }}>
        Nothing from your logs is kept. If you need help, reply to your Autumbra update email.
      </p>
    </section>
  );
}
