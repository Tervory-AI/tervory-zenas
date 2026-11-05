"use client";

import { useEffect, useState } from "react";
import { type Part, type Pistol } from "./copy";

type SourceRef = { title: string; detail: string };
type Item = { name: string; price: string };
type Turn =
  | { role: "you"; text: string }
  | { role: "rig"; text: string; items: Item[]; sources: SourceRef[]; note?: string };

type Hit = { id?: string; name?: string; category?: string; price?: { amount?: number } };

function rand(amount: number): string {
  return `R${Math.round(amount).toLocaleString("en-US")}`;
}

function Pill({ children, on = true }: { children: string; on?: boolean }) {
  return (
    <span className={on ? "pill" : "pill off"}>
      <i />
      {children}
    </span>
  );
}

async function call<T>(body: unknown): Promise<T> {
  const response = await fetch("/rig/api/rig", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify(body),
  });
  const data = (await response.json()) as T & { error?: string };
  if (!response.ok) throw new Error(data.error || "Rig did not answer. Try again.");
  return data;
}

export function SearchView({ query, onQuery, hasCatalogue }: { query: string; onQuery: (value: string) => void; hasCatalogue: boolean }) {
  const [text, setText] = useState("");
  const [pistols, setPistols] = useState<Pistol[]>([]);
  const [parts, setParts] = useState<Part[]>([]);
  const [chosen, setChosen] = useState<string>("");
  const [added, setAdded] = useState<string[]>([]);
  const [line, setLine] = useState("");
  const [thread, setThread] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [final, setFinal] = useState(false);

  useEffect(() => {
    if (query && query !== text) {
      setText(query);
      void run(query, false);
    }
    // The parent sets query when another tab submits a search.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  useEffect(() => {
    if (!hasCatalogue) {
      setPistols([]); setParts([]); setAdded([]); setChosen("");
      setThread([]); setLine(""); setFinal(false);
    }
  }, [hasCatalogue]);

  function applyHits(q: string, hits: Hit[], exact: boolean, message?: string) {
    const rows: Pistol[] = hits.map((hit) => {
      const id = String(hit.id ?? "");
      const amount = Number(hit.price?.amount ?? 0);
      return {
        id,
        name: String(hit.name ?? ""),
        price: Number.isFinite(amount) ? amount : 0,
        market: null,
      };
    });
    setPistols(rows);
    const noun = hits.every((hit) => hit.category === "Pistols")
      ? rows.length === 1
        ? "pistol"
        : "pistols"
      : rows.length === 1
        ? "product"
        : "products";
    if (!exact) {
      const names = rows.map((row) => row.name).filter(Boolean).join(", ");
      setLine(
        `Search looks through Rig's own catalogue records. ${message || `No exact match for ${q}.`} Closest matches: ${names}.`,
      );
      setChosen("");
      setParts([]);
      setAdded([]);
      setFinal(false);
      return;
    }
    setLine(`Search looks through Rig's own catalogue records. Showing results for "${q}": ${rows.length} ${noun}.`);
    setParts([]); setAdded([]); setFinal(false);
    if (rows[0]) {
      setChosen(rows[0].id);
      void loadParts(rows[0].id);
    }
  }

  async function loadParts(id: string) {
    try {
      const body = await call<{ branches?: { category?: string; products?: Hit[] }[] }>({ op: "graph", id });
      const next: Part[] = [];
      for (const branch of body.branches ?? []) {
        const type = branch.category === "Aftermarket sights" ? "Sights" : String(branch.category ?? "");
        for (const product of branch.products ?? []) {
          next.push({
            id: String(product.id ?? ""),
            type,
            name: String(product.name ?? ""),
            price: Number(product.price?.amount ?? 0),
            market: null,
          });
        }
      }
      setParts(next);
      setAdded([]);
      setFinal(false);
    } catch {
      setParts([]);
      setLine("");
    }
  }

  async function run(q: string, tellParent: boolean) {
    const trimmed = q.trim();
    if (!trimmed || !hasCatalogue) return;
    if (tellParent) onQuery(trimmed);
    setText(trimmed);
    setLine("");
    try {
      const body = await call<{ results?: Hit[]; exact_match?: boolean; message?: string }>({ op: "search", q: trimmed });
      const hits = Array.isArray(body.results) ? body.results : [];
      if (!hits.length) {
        setPistols([]);
        setParts([]);
        setChosen("");
        setAdded([]);
        setFinal(false);
        setLine("");
        return;
      }
      applyHits(trimmed, hits, body.exact_match !== false, body.message);
    } catch {
      setLine("");
    }
  }

  function choose(id: string) {
    setChosen(id);
    void loadParts(id);
  }

  function toggle(id: string) {
    setAdded((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
    setFinal(true);
  }

  function reset() {
    setChosen("");
    setAdded([]);
    setParts([]);
    setFinal(false);
  }

  async function send(event: React.FormEvent) {
    event.preventDefault();
    const question = draft.trim();
    if (!question || busy || !hasCatalogue) return;
    const prior = thread;
    setThread([...prior, { role: "you", text: question }]);
    setDraft("");
    setBusy(true);
    try {
      const data = await call<{ answer: string; items?: Item[]; sources?: SourceRef[] }>({
        op: "ask",
        question,
        product_id: chosen,
      });
      const sources = data.sources ?? [];
      setThread([
        ...prior,
        { role: "you", text: question },
        {
          role: "rig",
          text: data.answer || "I don't know",
          items: data.items ?? [],
          sources,
          note: sources.length ? "Prices are the retailer's. Market average shows once your selection is final." : undefined,
        },
      ]);
    } catch {
      setThread([
        ...prior,
        { role: "you", text: question },
        { role: "rig", text: "Rig did not answer. Try again.", items: [], sources: [] },
      ]);
    } finally {
      setBusy(false);
    }
  }

  const centre = hasCatalogue ? pistols.find((row) => row.id === chosen) : undefined;
  const selected = parts.filter((part) => added.includes(part.id));
  const lines = centre ? [{ id: centre.id, name: centre.name, price: centre.price, market: centre.market }, ...selected] : [];
  const total = lines.reduce((sum, lineItem) => sum + lineItem.price, 0);
  const markets = lines.map((lineItem) => lineItem.market);
  const marketTotal = markets.every((value) => value != null) ? markets.reduce<number>((sum, value) => sum + (value ?? 0), 0) : null;

  return (
    <div className="search-view">
      <div className="search-block">
        <form
          className="search-bar"
          role="search"
          onSubmit={(event) => {
            event.preventDefault();
            void run(text, true);
          }}
        >
          <input
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="Search a brand, model or part"
            aria-label="Search a brand, model or part"
          />
          <button type="submit" className="primary">
            Search
          </button>
        </form>
        <p className="results-line">{hasCatalogue ? line : ""}</p>
      </div>

      <div className="search-split">
        <div className="search-main">
          {hasCatalogue && pistols.length ? (
            <section>
              <h2>Pistols</h2>
              <p className="intro">Pick one to see the parts that fit it.</p>
              <div className="pistols">
                {pistols.map((pistol) => (
                  <article key={pistol.id} className={pistol.id === chosen ? "pistol chosen" : "pistol"}>
                    <div className="pistol-top">
                      <h3>{pistol.name}</h3>
                      {pistol.id === chosen ? (
                        <Pill>Chosen</Pill>
                      ) : (
                        <button type="button" className="text choose" onClick={() => choose(pistol.id)}>
                          Choose
                        </button>
                      )}
                    </div>
                    <p className="price">{rand(pistol.price)}</p>
                    <p className="retail">Retailer price</p>
                  </article>
                ))}
              </div>
            </section>
          ) : null}

          {hasCatalogue && chosen && parts.length ? (
            <section>
              <h2>Compatible upgrades for {centre?.name ?? "this pistol"}</h2>
              <p className="intro">Parts confirmed to fit this pistol, sorted by type.</p>
              <div className="sheet">
                <table className="upgrades">
                  <thead>
                    <tr>
                      <th>Type</th>
                      <th>Part</th>
                      <th className="num">Price</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {parts.map((part) => (
                      <tr key={part.id}>
                        <td>{part.type}</td>
                        <td className="name">{part.name}</td>
                        <td className="num">{rand(part.price)}</td>
                        <td className="act">
                          {added.includes(part.id) ? (
                            <button type="button" className="pill-btn" onClick={() => toggle(part.id)}>
                              <Pill>Added</Pill>
                            </button>
                          ) : (
                            <button type="button" className="text" onClick={() => toggle(part.id)}>
                              Add
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ) : null}
        </div>

        <aside className="ask" aria-label="Ask Rig">
          <h2>Ask Rig</h2>
          <p className="intro">Ask in plain words. Answers come only from Rig's catalogue.</p>
          <div className="thread">
            {(hasCatalogue ? thread : []).map((turn, index) =>
              turn.role === "you" ? (
                <article key={index} className="you">
                  <b>You</b>
                  <p>{turn.text}</p>
                </article>
              ) : (
                <article key={index} className="rig-msg">
                  <b>Rig</b>
                  <p>{turn.text}</p>
                  {turn.items.length ? (
                    <ul className="answer-items">
                      {turn.items.map((item) => (
                        <li key={item.name}>
                          <span>{item.name}</span>
                          <span>{item.price}</span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {turn.note ? <p className="note">{turn.note}</p> : null}
                  <div className="sources">
                    <h3>Sources</h3>
                    {turn.sources.length ? (
                      <ol>
                        {turn.sources.map((source, sourceIndex) => (
                          <li key={source.title}>
                            <span className="source-no">{sourceIndex + 1}</span>
                            <div>
                              <strong>{source.title}</strong>
                              <p>{source.detail}</p>
                            </div>
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <p className="none">None in this catalogue.</p>
                    )}
                  </div>
                </article>
              ),
            )}
          </div>
          <form className="composer" onSubmit={send}>
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder='Ask a follow-up'
              aria-label="Ask Rig"
              disabled={busy}
            />
            <button type="submit" className="line" disabled={busy}>
              {busy ? "Sending…" : "Send"}
            </button>
          </form>
        </aside>
      </div>

      {lines.length ? <section className="build-wrap">
        <h2>Your build</h2>
        <div className="card build">
        <div className="build-head">
          <h3>Selection final</h3>
          <Pill on={final && Boolean(centre)}>Final</Pill>
        </div>
        {lines.length ? (
          <table className="build-table">
            <thead>
              <tr>
                <th />
                <th>Price</th>
                <th>Market avg.</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((lineItem) => (
                <tr key={lineItem.id}>
                  <td className="name">{lineItem.name}</td>
                  <td>{rand(lineItem.price)}</td>
                  <td>{lineItem.market != null ? rand(lineItem.market) : "—"}</td>
                </tr>
              ))}
              <tr className="total">
                <td>Total</td>
                <td>{rand(total)}</td>
                <td>{marketTotal != null ? rand(marketTotal) : "—"}</td>
              </tr>
            </tbody>
          </table>
        ) : (
          <p className="intro">Choose a pistol to start a build.</p>
        )}
        <p className="note">The total is the plain sum of the parts. No bundle discount. Market average comes from other shops' prices.</p>
        <button type="button" className="line" onClick={reset}>
          Start again
        </button>
        </div>
      </section> : null}

    </div>
  );
}
