"use client";
import { useCallback, useEffect, useRef, useState } from "react";
export function LiveView({ id, account }: { id: string; account: string }) {
  const [png, setPng] = useState(""); const [error, setError] = useState(""); const [ready, setReady] = useState(false);
  const running = useRef(false); const text = useRef<HTMLInputElement>(null);
  const act = useCallback(async (action: string, values: Record<string, unknown> = {}) => {
    if (running.current) return; running.current = true;
    try {
      const response = await fetch(`/access/api/queue/${id}?account=${account}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tenant_id: account, action, ...values }) });
      if (!response.ok) throw new Error();
      const result = await response.json() as { png?: string; ready?: boolean };
      if (result.png) setPng(result.png);
      if (result.ready) { setReady(true); setPng(""); }
      setError("");
    } catch { setError("The live page could not be updated. It may have expired, or the check still needs you. Retry the page to open a new session."); }
    finally { running.current = false; }
  }, [id, account]);
  useEffect(() => { void act("open"); }, [act]);
  useEffect(() => { if (ready || !png) return; const timer = setInterval(() => void act("frame"), 2000); return () => clearInterval(timer); }, [act, png, ready]);
  return <main className="access-page access-live"><a href="/access">Back to connections</a><h1>A page needs a person<span>.</span></h1><p>You are using this room's own browser. Handle the check yourself, then hand the page back. The view expires after 15 minutes. Add passwords through Connections.</p>{error && <p role="alert" className="access-message">{error}</p>}{ready ? <p role="status">Handed back. The room can continue from this same session.</p> : <><div className="access-live-controls"><button onClick={() => act("frame")}>Refresh view</button><button onClick={() => act("scroll", { delta: -600 })}>Scroll up</button><button onClick={() => act("scroll", { delta: 600 })}>Scroll down</button><button onClick={() => act("key", { key: "Tab" })}>Tab</button><button onClick={() => act("key", { key: "Enter" })}>Enter</button><input ref={text} type="password" autoComplete="off" aria-label="Text for the focused browser field" /><button onClick={() => { const value = text.current?.value ?? ""; if (text.current) text.current.value = ""; void act("text", { text: value }); }}>Type text</button><button className="access-primary" onClick={() => act("hand_back")}>Hand back</button></div>{png ? <img alt="Live page in the room's browser. Click to interact." src={`data:image/png;base64,${png}`} width={1280} height={900} onClick={e => { const box = e.currentTarget.getBoundingClientRect(); void act("click", { x: Math.round((e.clientX-box.left)*1280/box.width), y: Math.round((e.clientY-box.top)*900/box.height) }); }} /> : <p>Opening the room's browser…</p>}</> }</main>;
}
