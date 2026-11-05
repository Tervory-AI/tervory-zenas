"use client";

import { useEffect, useRef, useState } from "react";

type Stage = { rotate(value: number): void; zoom(value: number): void; dispose(): void };
type Runtime = { createStage(canvas: HTMLCanvasElement, shapes: unknown[], files: ArrayBuffer[], reduced: boolean): Promise<Stage> };

export function ProductPreview({ id, anchors }: { id: string; anchors: Record<string, { position_mm: number[]; quaternion: number[] }> }) {
  const canvas = useRef<HTMLCanvasElement>(null), stage = useRef<Stage | null>(null);
  const alive = useRef(true);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; stage.current?.dispose(); }; }, []);
  async function open() {
    if (busy || stage.current) return;
    setBusy(true); setStatus("Opening product preview…");
    try {
      const base = "/rig/admin/three-d/file/";
      const response = await fetch(`${base}draft/${id}`, { cache: "no-store" });
      if (!response.ok) throw new Error();
      const bytes = await response.arrayBuffer();
      // Literal same-origin allowlisted route. No remote import or room key in the browser.
      const moduleUrl = base + "runtime.js";
      const runtime = await import(/* webpackIgnore: true */ moduleUrl) as Runtime;
      if (!alive.current || !canvas.current) return;
      const next = await runtime.createStage(canvas.current, [{ role: "product", mount: null, review_anchors: anchors }], [bytes], true);
      if (!alive.current) { next.dispose(); return; }
      stage.current = next; setStatus("Check the product from every side. Coloured marks show the attachment points and their directions.");
    } catch { if (alive.current) setStatus("The preview could not be opened. Try again before approving."); }
    finally { if (alive.current) setBusy(false); }
  }
  return <div className="three-preview">
    <button type="button" onClick={open} disabled={busy}>Open product preview</button>
    <canvas ref={canvas} tabIndex={0} aria-label="Product preview. Left and right arrows turn it; plus and minus zoom."
      onKeyDown={event => {
        if (!["ArrowLeft", "ArrowRight", "+", "-"].includes(event.key)) return;
        event.preventDefault();
        if (event.key === "ArrowLeft" || event.key === "ArrowRight") stage.current?.rotate(event.key === "ArrowLeft" ? -0.25 : 0.25);
        else stage.current?.zoom(event.key === "+" ? 0.85 : 1.15);
      }} />
    <div className="three-controls">
      <button type="button" onClick={() => stage.current?.rotate(-0.25)}>Turn left</button>
      <button type="button" onClick={() => stage.current?.rotate(0.25)}>Turn right</button>
      <button type="button" onClick={() => stage.current?.zoom(0.85)}>Zoom in</button>
      <button type="button" onClick={() => stage.current?.zoom(1.15)}>Zoom out</button>
    </div>
    <p role="status">{status}</p>
  </div>;
}
