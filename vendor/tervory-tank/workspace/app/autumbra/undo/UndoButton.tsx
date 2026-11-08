"use client";

import { useEffect, useRef, useState } from "react";
import { readUndoResponse } from "./request";

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
  const inFlight = useRef(false);

  useEffect(() => {
    if (initialAlreadyUndone) return;
    const signed = (token || "").trim();
    if (!signed) return;
    let cancelled = false;
    const controller = new AbortController();
    (async () => {
      try {
        const {response: res, data} = await readUndoResponse(`/api/autumbra/undo?token=${encodeURIComponent(signed)}`, {
          method: "GET",
          cache: "no-store",
          signal: controller.signal,
        });
        if (!res.ok || !data?.ok) throw new Error("Unavailable");
        if (!cancelled && data.already_undone) {
          setStatus("already");
          setMessage(data.detail || "This block was already undone.");
        }
      } catch {
        if (!cancelled) setMessage("Could not reach Autumbra. Try again in a moment.");
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [token, initialAlreadyUndone]);

  async function onUndo() {
    if (inFlight.current || status === "done" || status === "already") return;
    inFlight.current = true;
    setStatus("working");
    setMessage("");
    const signed = (token || "").trim();
    if (!signed) {
      setStatus("error");
      setMessage("This undo link is invalid or has expired.");
      inFlight.current = false;
      return;
    }
    try {
      const {response: res, data} = await readUndoResponse("/api/autumbra/undo", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ token: signed }),
      });
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
    } finally {
      inFlight.current = false;
    }
  }

  const disabled =
    status === "working" || status === "done" || status === "already" || !(token || "").trim();

  return (
    <div style={{ marginTop: "1.5rem", minHeight: "9rem" }} aria-busy={status === "working"}>
      {status === "already" ? (
        <p role="status" style={{ marginTop: "0.75rem", color: "#14532d", fontSize: "1.05rem" }}>{message}</p>
      ) : (
        <>
          <button
            type="button"
            onClick={onUndo}
            disabled={disabled}
            style={{
              fontSize: "1.05rem",
              minWidth: "11rem",
              minHeight: 44,
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
            <p role={status === "error" ? "alert" : "status"} style={{ marginTop: "0.75rem", color: status === "error" ? "#b91c1c" : "#14532d" }}>
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
