"use client";
import { useRef, useState, useTransition } from "react";
import { squadClients, squadState, changeSquad } from "./squad-actions";
import type { SquadClients, SquadState } from "./squad-actions";
const labels: Record<string, string> = { base: "Base", tiger: "Tiger", market_lab: "Market Lab", rig: "Rig", tisan: "Tisan", radix: "Radix", zenas: "Zenas" };

export function ClientSquads({ initial, selectedClient = "", initialState = null }: { initial: SquadClients; selectedClient?: string; initialState?: SquadState | null }) {
  const [clients, setClients] = useState(initial);
  const [selected, setSelected] = useState(selectedClient);
  const [state, setState] = useState<SquadState | null>(initialState);
  const [pending, start] = useTransition();
  const request = useRef(0);
  function loadClient(id: string) {
    const sequence = ++request.current;
    setSelected(id); setState(null);
    if (id) start(async () => { const result = await squadState(id); if (sequence === request.current) setState(result); });
  }
  function loadPage(page: number) {
    const sequence = ++request.current;
    setSelected(""); setState(null);
    start(async () => { const result = await squadClients(page); if (sequence === request.current) setClients(result); });
  }
  return <section className="dash-card client-squads" aria-labelledby="client-squads-heading" aria-busy={pending}>
    <h2 id="client-squads-heading">Client squads</h2>
    <p className="dash-note">Enable the agents each client can use. All off means chat only. Changes are logged on this server.</p>
    {clients.error ? <p role="alert" className="dash-problem">{clients.error}</p> : clients.clients.length === 0 ? <p className="dash-empty">No clients on this page.</p> :
      <label className="client-squads-picker">Client<select value={selected} disabled={pending} onChange={e => loadClient(e.target.value)}>
        <option value="">Select a client</option>{clients.clients.map(client => <option key={client.id} value={client.id}>{client.name}</option>)}
      </select></label>}
    <div className="dash-row">
      <button className="dash-button" disabled={pending || clients.page === 0} onClick={() => loadPage(clients.page - 1)}>Previous clients</button>
      <button className="dash-button" disabled={pending || !clients.more} onClick={() => loadPage(clients.page + 1)}>Next clients</button>
      {clients.error && <button className="dash-button" disabled={pending} onClick={() => loadPage(clients.page)}>Reload clients</button>}
    </div>
    {pending && <p role="status" className="dash-note">Updating access…</p>}
    {state?.error && <p role="alert" className="dash-problem">{state.error}</p>}
    {selected && <button className="dash-button" disabled={pending} onClick={() => loadClient(selected)}>Reload access</button>}
    {state && !state.error && <>
      <div className="client-squads-agents">{state.grants.map(grant => <div className="dash-row" key={grant.agent}>
        <span>{labels[grant.agent] ?? grant.agent}</span>
        <button type="button" role="switch" aria-label={`${labels[grant.agent] ?? grant.agent} access`} aria-checked={grant.enabled}
          className="dash-button" disabled={pending} onClick={() => start(async () => {
            const sequence = ++request.current;
            const result = await changeSquad(selected, grant.agent, !grant.enabled);
            if (sequence === request.current) setState(result);
          })}>{grant.enabled ? "On" : "Off"}</button>
      </div>)}</div>
      <details className="client-squads-log"><summary>Recent access changes</summary>{state.log.length ? <ul>{state.log.map(row => <li key={row.id}>
        {labels[row.agent] ?? row.agent} · {row.enabled ? "On" : "Off"} · {row.changed_by}<br /><time dateTime={row.changed_at}>{row.changed_at.replace("T", " ")}</time>
      </li>)}</ul> : <p className="dash-note">No access changes recorded.</p>}</details>
    </>}
  </section>;
}
