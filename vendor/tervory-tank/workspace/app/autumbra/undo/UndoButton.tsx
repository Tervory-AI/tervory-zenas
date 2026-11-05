"use client";

import { useEffect, useState } from "react";

export function UndoButton({
  receiptId,
  token,
  initialAlreadyUndone = false,
  initialMessage = "",
}: {
  receiptId: string;
  token?: string;
  initialAlreadyUndone?: boolean;
  initialMessage?: string;
}) {
  const [status, setStatus] = useState<"idle" | "working" | "done" | "already" | "error">(
    initialAlreadyUndone ? "already" : "idle",
  );
  const [message, setMessage] = useState(initialAlreadyUndone ? initialMessage : "");

  useEffect(() => {
    if (initialAlreadyUndone) return;
    const signed = (token || "").trim();
    if (!signed) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/autumbra/undo?token=${encodeURIComponent(signed)}`, {
          method: "GET",
          cache: "no-store",
        });
        const data = (await res.json()) as {
          already_undone?: boolean;
          detail?: string;
        };
        if (!cancelled && data.already_undone) {
          setStatus("already");
          setMessage(data.detail || "This block was already undone.");
        }
      } catch {
        /* leave idle — first tap still works */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, initialAlreadyUndone]);

  async function onUndo() {
    setStatus("working");
    setMessage("");
    const signed = (token || "").trim();
    if (!signed) {
      setStatus("error");
      setMessage("This undo link is invalid or has expired.");
      return;
    }
    try {
      const res = await fetch("/api/autumbra/undo", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ token: signed }),
      });
      const data = (await res.json()) as {
        ok?: boolean;
        detail?: string;
        already_undone?: boolean;
      };
      if (data.already_undone) {
        setStatus("already");
        setMessage(data.detail || "This block was already undone.");
        return;
      }
      if (!res.ok || !data.ok) {
        setStatus("error");
        setMessage(data.detail || "Something went wrong. Try again, or reply to your Autumbra email.");
        return;
      }
      setStatus("done");
      setMessage("Done. That shield is being removed.");
    } catch {
      setStatus("error");
      setMessage("Could not reach Autumbra. Try again in a moment.");
    }
  }

  const disabled =
    status === "working" || status === "done" || status === "already" || !(token || "").trim();

  return (
    <div style={{ marginTop: "1.5rem" }}>
      {status === "already" ? (
        <p style={{ marginTop: "0.75rem", color: "#14532d", fontSize: "1.05rem" }}>{message}</p>
      ) : (
        <>
          <button
            type="button"
            onClick={onUndo}
            disabled={disabled}
            style={{
              fontSize: "1.05rem",
              padding: "0.75rem 1.25rem",
              borderRadius: 8,
              border: "none",
              background: status === "done" ? "#1b7f4a" : "#111827",
              color: "white",
              cursor: disabled ? "default" : "pointer",
            }}
          >
            {status === "working" ? "Undoing…" : status === "done" ? "Undone" : "Undo this shield"}
          </button>
          {message ? (
            <p style={{ marginTop: "0.75rem", color: status === "error" ? "#b91c1c" : "#14532d" }}>
              {message}
            </p>
          ) : null}
        </>
      )}
      {!token ? (
        <p style={{ marginTop: "0.75rem", color: "#b91c1c" }}>
          This undo link is invalid or has expired. Use the Undo link from your Autumbra email.
        </p>
      ) : null}
      <p style={{ display: "none" }} aria-hidden data-receipt={receiptId} />
    </div>
  );
}
