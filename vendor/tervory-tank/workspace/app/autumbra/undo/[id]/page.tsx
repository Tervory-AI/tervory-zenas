import { verifyUndoToken } from "@/lib/autumbra-undo-token";
import { loadUndoStatus } from "@/lib/autumbra-undo-status";
import { UndoButton } from "../UndoButton";

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
  if (token) {
    try {
      const payload = verifyUndoToken(token);
      const status = await loadUndoStatus(payload.tenant_id, payload.receipt_id || resolved.id);
      if (status.already_undone) {
        alreadyUndone = true;
        alreadyMessage =
          status.detail || "This block was already undone on an earlier time.";
      }
    } catch {
      // Invalid token — UndoButton shows the expired copy.
    }
  }

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 720 }}>
      <h1>Undo an Autumbra shield</h1>
      {alreadyUndone ? (
        <p>{alreadyMessage}</p>
      ) : (
        <p>
          Autumbra blocked an address that was attacking your site. If that caused a problem
          for you, tap the button below and we will remove that block.
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
    </main>
  );
}
