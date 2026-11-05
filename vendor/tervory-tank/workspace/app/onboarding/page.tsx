"use client";

import { OrganisationSwitcher, useSignin } from "../trigger-signin-provider";
import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { plainRunError, type BatchCounts, type PassRecord, type StartRequest } from "./csv";
import { Wizard, type RunProgress } from "./wizard";


export default function Page() {
  return (
    <Suspense fallback={<p className="loading">Loading…</p>}>
      <Flow />
    </Suspense>
  );
}

function Flow() {
  const params = useSearchParams();
  const { isLoaded, orgId: signedInOrg } = useSignin();
  const organization = signedInOrg ? { id: signedInOrg, name: "Your organisation" } : null;
  const orgId = organization?.id ?? null;
  const [engines, setEngines] = useState<string[] | null>(null);
  const [enginesError, setEnginesError] = useState<string | null>(null);
  const [progress, setProgress] = useState<RunProgress | null>(null);
  const [activeBatch, setActiveBatch] = useState<string | null>(null);

  const loadEngines = useCallback(() => {
    if (!orgId) return;
    setEnginesError(null);
    fetch("/api/base?engines=1", { cache: "no-store" })
      .then(async (res) => {
        const data: unknown = await res.json().catch(() => null);
        if (!res.ok || !Array.isArray(data)) throw new Error("load");
        setEngines(data.filter((item): item is string => typeof item === "string"));
      })
      .catch(() => {
        setEngines(null);
        setEnginesError("We could not load industries. Try again in a moment.");
      });
  }, [orgId]);

  useEffect(() => {
    loadEngines();
  }, [loadEngines]);

  useEffect(() => {
    if (!activeBatch) return;
    const batch = activeBatch;
    let live = true;
    async function tick() {
      try {
        const [recordsRes, batchesRes] = await Promise.all([
          fetch(`/api/base?batch=${encodeURIComponent(batch)}&limit=200`, { cache: "no-store" }),
          fetch("/api/base?batches=1", { cache: "no-store" }),
        ]);
        if (!live) return;
        const recordData: unknown = recordsRes.ok ? await recordsRes.json() : [];
        const batchData: unknown = batchesRes.ok ? await batchesRes.json() : [];
        const records = Array.isArray(recordData) ? recordData as PassRecord[] : [];
        const batches = Array.isArray(batchData) ? batchData as ({ batch: string } & BatchCounts)[] : [];
        const summary = batches.find((item) => item.batch === batch) ?? null;
        setProgress({
          summary: summary ? { total: Number(summary.total), done: Number(summary.done), failed: Number(summary.failed), pending: Number(summary.pending) } : null,
          records,
        });
      } catch {
        // Keep the last progress line. The next poll will try again.
      }
    }
    void tick();
    const id = setInterval(() => void tick(), 3000);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [activeBatch]);

  async function onStart(request: StartRequest): Promise<{ batch: string }> {
    const res = await fetch("/api/base", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        batch: request.batch,
        csv: request.csv,
        preview: false,
        engine: request.engine,
        split_by_state: false,
      }),
    });
    const data = (await res.json().catch(() => ({}))) as { error?: unknown; labels?: unknown };
    if (!res.ok) throw new Error(plainRunError(data.error));
    const labels = Array.isArray(data.labels) ? data.labels : [];
    const label = typeof labels[0] === "string" && labels[0] ? labels[0] : request.batch;
    setActiveBatch(label);
    return { batch: label };
  }

  if (!isLoaded) return <p className="loading">Loading…</p>;

  return (
    <Wizard
      key={organization?.id ?? "none"}
      orgName={organization?.name ?? null}
      orgSlot={(
        <OrganisationSwitcher />
      )}
      engines={organization ? engines : null}
      enginesError={organization ? enginesError : null}
      onRetryEngines={loadEngines}
      onStart={onStart}
      progress={progress}
      initialStep={organization && params.get("step") === "industry" ? "industry" : "organization"}
    />
  );
}
