"use client";
import { skalaFetch } from "./request";
import { useRef, useState } from "react";

type Preview = { token: string; items: { id: string; version: string; score: number; runs: number; model: boolean }[] };

export function ApproveAllReady({ onApproved, className, label = "Approve all ready", disabled = false }: { label?: string; onApproved?: () => Promise<void>; className?: string; disabled?: boolean }) {
  const submitting = useRef(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function request(confirm: boolean) {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setMessage("");
    try {
      const res = await skalaFetch("/api/skala", {method: "POST", headers: {"content-type": "application/json"},
        body: JSON.stringify({ action: confirm ? "bulk-confirm" : "bulk-preview", token: preview?.token, confirmed: confirm })});
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Approval could not be confirmed. Reload the list.");
      if (confirm) {
        dialog.current?.close();
        setPreview(null);
        setMessage(`${data.approved.length} versions approved. Each approval was recorded.`);
        await onApproved?.();
      } else {
        setPreview(data);
        dialog.current?.showModal();
      }
    } catch (err) {
      setMessage("Approval could not be confirmed. Reload the list.");
      dialog.current?.close();
      setPreview(null);
    } finally { submitting.current = false; setBusy(false); }
  }
  return <div className="sk-bulk">
    <button className={className} type="button" disabled={busy || disabled} onClick={() => request(false)}>{busy ? "Checking…" : label}</button>
    {message ? <p role="status">{message}</p> : null}
    <dialog ref={dialog} className="sk-dialog" style={{maxWidth: "min(850px, calc(100vw - 32px))", boxSizing: "border-box", border: "1px solid #B8BFD0", borderRadius: 8, padding: 24, color: "#001840"}} aria-label="Confirm ready versions">
      <h2>Approve these versions?</h2>
      <p>Room-authored skills need individual source review and are excluded.</p>
      {preview?.items.length ? <ul className="sk-bulk-list" style={{maxHeight: "45vh", overflow: "auto", overflowWrap: "anywhere"}}>{preview.items.map(item => <li key={item.id}>{item.id} · {item.version} · {(item.score * 100).toFixed(1)}%{item.model ? ` · ${item.runs}/3 runs` : " · code skill"}</li>)}</ul> : <p>No versions are ready. Run evals to prepare a version for approval.</p>}
      <button className={className ?? "primary"} type="button" disabled={busy || !preview?.items.length} onClick={() => request(true)}>Confirm listed approvals</button>{" "}
      <button className={className} type="button" disabled={busy} onClick={() => { dialog.current?.close(); setPreview(null); }}>Cancel</button>
    </dialog>
  </div>;
}
