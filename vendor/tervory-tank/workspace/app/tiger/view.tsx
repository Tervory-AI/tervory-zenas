"use client";

import { useEffect, useState } from "react";
import { addProduct, removeProduct } from "./actions";
import { EVERY_OPTIONS, FOOT, LEDE } from "./copy";
import { SECTIONS, searchTiger, type AlertRow, type ProductRow, type ReadRow, type Section, type TigerData } from "./model";
import { SquadLinks } from "../squad-links";
import { AttemptTime, PageLink, Pill, SimpleTable } from "./parts";
import { PositionViews } from "./position-views";
import type { ComparisonData, WatchView } from "./position-model";

function SearchIcon() {
  return (
    <svg className="search-icon" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M10.5 10.5 L14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function RowStatus({ row }: { row: ProductRow }) {
  return row.reason
    ? <p className="check-status">Not checked — {row.reason}</p>
    : <Pill status={row.status} />;
}

function ProductIdentifier({ name, url }: { name: string | null; url: string }) {
  return url ? <a href={url} title={url} target="_blank" rel="noreferrer">{name || url}</a> : name ? <>{name}</> : null;
}

export function TigerApp({
  data,
  notice,
  initialTab,
  accountName,
  comparison,
  initialView = "position",
}: {
  data: TigerData;
  notice: string;
  initialTab: Section;
  accountName: string | null;
  comparison?: ComparisonData;
  initialView?: WatchView;
}) {
  const [section, setSection] = useState<Section>(initialTab);
  const [addOpen, setAddOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [hidden, setHidden] = useState<string[]>([]);
  const [help, setHelp] = useState(false);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setAddOpen(false);
      setMenuOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const products = data.products.filter((row) => !hidden.includes(row.id));
  const visible: TigerData = { ...data, products };
  const hit = searchTiger(visible, query);
  const headerRed = !addOpen;

  function show(next: Section) {
    setMenuOpen(false);
    setAddOpen(false);
    setQuery("");
    setSection(next);
  }

  const comparing = section === "watch" && comparison !== undefined;
  const rootClass = ["tiger", comparing ? "position-view" : "", addOpen ? "add-open" : "", menuOpen ? "menu-open" : ""].filter(Boolean).join(" ");

  return (
    <div className={rootClass}>
      <header className="topbar">
        <img src={comparing ? "/brand/tervory-logo.png" : "/brand/tervory-logo-reversed.png"} alt="Tervory" />
        <button type="button" className="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen((open) => !open)}>Menu</button>
      </header>

      <aside className="panel" aria-label="Tiger">
        <div className="panel-top">
          {comparing ? <><img src="/brand/tervory-logo.png" alt="Tervory" width={208} height={38} /><div className="position-account">{accountName?.trim()}<span aria-hidden="true">⌄</span></div><p className="panel-identity">Tiger</p></> : <img src="/brand/tiger-on-dark.png" alt="Tiger" width={2607} height={801} />}
        </div>
        <nav className="panel-account" aria-label="Account">
          <p hidden={comparing} className="account" role={accountName?.trim() ? undefined : "status"}>{accountName?.trim() || "Account name could not be loaded."}</p>
          <a className="nav" href="https://app.tervory.com/">Dashboard</a>
          <a className="nav" href="/tasks" onClick={() => setMenuOpen(false)}>Tasks</a>
          <a className="nav" href="/chats" onClick={() => setMenuOpen(false)}>Chats</a>
        </nav>
        <SquadLinks className="panel-account squad-nav" linkClassName="nav" />
        <div className="panel-own">
          <nav aria-label="Tiger sections">
            {SECTIONS.map((item) => (
              <button key={item.id} type="button" className={item.id === section ? "nav on" : "nav"} onClick={() => show(item.id)}>
                {item.label}
              </button>
            ))}
          </nav>
        </div>
        <div className="panel-bottom">
          {!comparing ? <img src="/brand/tervory-logo-reversed.png" alt="Tervory" width={186} height={34} /> : null}
          <button type="button" className="nav" onClick={() => { setHelp((open) => !open); setMenuOpen(false); }}>Support</button>
        </div>
      </aside>

      <div className="body">
        <form className={query ? "search on" : "search"} role="search" onSubmit={(event) => { event.preventDefault(); setQuery(draft.trim()); }}>
          {comparing ? <img className="search-icon" src="/tiger/search.svg" alt="" /> : <SearchIcon />}
          <input
            aria-label={comparing ? "Search watched products, readings and alerts" : "Search watched products, reads and alerts"}
            placeholder={comparing ? "Search watched products, readings and alerts" : "Search watched products, reads and alerts"}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
          {!comparing && query ? (
            <button type="button" className="text" onClick={() => { setQuery(""); setDraft(""); }}>Clear</button>
          ) : (
            !comparing ? <span className="hint">Press Enter to search</span> : null
          )}
        </form>

        <header className="head">
          <div>
            <h1>Tiger</h1>
            <p className="lede">{LEDE}</p>
          </div>
          {!comparing ? <button type="button" className={headerRed ? "primary" : "line"} onClick={() => setAddOpen(true)}>Add a product</button> : null}
        </header>

        {notice && !comparing ? <p className="notice">{notice}</p> : null}
        {help && !comparing ? <p className="notice">Manage check intervals and alert preferences when adding a product. Each watch shows its latest check status; completed readings are available in History.</p> : null}

        {hit && !comparing ? <Results hit={hit} /> : null}
        {!hit && section === "overview" ? <Overview data={visible} onWatch={() => show("watch")} onAlerts={() => show("alerts")} /> : null}
        {!hit && section === "alerts" ? <Alerts rows={data.alerts} example={data.example} /> : null}
        {section === "watch" && comparison ? <PositionViews data={comparison} initialView={initialView} query={query} plugins={comparison.plugins ?? null} /> : null}
        {!hit && section === "watch" && !comparison ? (
          <Watch rows={products} note={data.listNote} onHide={(id) => setHidden((current) => [...current, id])} />
        ) : null}
        {!hit && section === "history" ? (
          <section className="block">
            <div className="title-row"><h2>History</h2></div>
            <p className="intro">The latest 40 check attempts from your watched pages.</p>
            <SimpleTable heads={["When", "Product", "Price", "Stock", "Status"]}
              rows={data.reads.map((row) => [row.when, <ProductIdentifier key="product" name={row.product} url={row.url} />, row.price, row.stock, row.reason ? `Not checked — ${row.reason}` : "Read"])}
              empty="No readings yet." priceCol={2} />
          </section>
        ) : null}

        <footer className="foot">
          <p>{FOOT}</p>
        </footer>
      </div>

      {addOpen ? <AddProduct onClose={() => setAddOpen(false)} /> : null}
    </div>
  );
}

function Overview({ data, onWatch, onAlerts }: { data: TigerData; onWatch: () => void; onAlerts: () => void }) {
  const recent = data.alerts.slice(0, 3);
  return (
    <>
      <section className="counts" aria-label="Counts">
        <article className="count"><b>{data.watchedCount}</b><strong>{data.watchedCount === 1 ? "Watched product" : "Watched products"}</strong><p className="fine">Product pages Tiger checks.</p></article>
        <article className="count"><b>{data.changesToday}</b><strong>{data.changesToday === 1 ? "Change today" : "Changes today"}</strong><p className="fine">Confirmed changes since midnight UTC.</p></article>
      </section>
      <section className="block">
        <div className="title-row">
          <h2>Latest reads</h2>
        </div>
        <p className="intro">The latest check for each watched product.</p>
        {data.products.length === 0 ? <p className="empty">No products watched yet.</p> : (
        <ul className="cards">
          {data.products.map((row) => (
            <li key={row.id}>
              <div className="card-top">
                <strong><ProductIdentifier name={row.name} url={row.url} /></strong>
                {!row.reason ? <Pill status={row.status} /> : null}
              </div>
              {row.reason ? <RowStatus row={row} /> : row.price ? <p className="price">{row.price} · {row.stock}</p> : null}
              <p className="fine"><PageLink row={row} /></p>
              <p className="fine">{row.attemptedAt ? <>{row.reason ? "Attempted" : "Checked"} <AttemptTime row={row} /></> : row.reason ? null : "Not checked yet"}</p>
            </li>
          ))}
        </ul>
        )}
        {data.products.length === 0 ? null : (
        <>
        <div className="table-wrap overview-table">
          <table>
            <thead><tr><th>Product</th><th>Page</th><th>Price</th><th>Stock</th><th>Last attempt</th><th>Status</th></tr></thead>
            <tbody>
              {data.products.map((row) => (
                <tr key={row.id}>
                  <td className="name"><ProductIdentifier name={row.name} url={row.url} /></td>
                  <td className="muted"><PageLink row={row} /></td>
                  <td className={row.price == null ? "muted" : "price"}>{row.price}</td>
                  <td>{row.stock}</td>
                  <td className="muted"><AttemptTime row={row} /></td>
                  <td><RowStatus row={row} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="table-foot">
          <span>{data.listNote ?? `${data.products.length} watched`}</span>
          <button type="button" className="text" onClick={onWatch}>See full watch list →</button>
        </div>
        </>
        )}
      </section>
      <section className="block">
        <div className="title-row">
          <h2>Recent alerts</h2>
        </div>
        <p className="intro">Recent confirmed price and stock changes.</p>
        {recent.length === 0 ? <p className="empty">No alerts yet.</p> : (
        <ul className="cards">
          {recent.map((row) => (
            <li key={row.id}>
              <p className="fine">{row.when}</p>
              <strong><ProductIdentifier name={row.product} url={row.url} /></strong>
              <p>{row.change}</p>
            </li>
          ))}
        </ul>
        )}
        {recent.length === 0 ? null : (
        <>
        <div className="table-wrap overview-table">
          <table>
            <thead><tr><th>When</th><th>Product</th><th>What changed</th></tr></thead>
            <tbody>
              {recent.map((row) => (
                <tr key={row.id}><td className="muted">{row.when}</td><td className="name"><ProductIdentifier name={row.product} url={row.url} /></td><td>{row.change}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="table-foot end">
          <button type="button" className="text" onClick={onAlerts}>See all alerts →</button>
        </div>
        </>
        )}
      </section>
    </>
  );
}

function Alerts({ rows, example }: { rows: AlertRow[]; example: boolean }) {
  const [kind, setKind] = useState<"All" | "Price" | "Stock">("All");
  const listed = rows.filter((row) => kind === "All" || row.kind === kind);
  return (
    <section className="block">
      <div className="title-row">
        <h2>Alerts</h2>
      </div>
      <p className="intro">Confirmed changes from completed product checks.</p>
      {rows.length === 0 ? <p className="empty">No alerts yet.</p> : null}
      <div className="filters">
        {(["All", "Price", "Stock"] as const).map((item) => (
          <button key={item} type="button" className={kind === item ? "chip on" : "chip"} onClick={() => setKind(item)}>{item}</button>
        ))}
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>When</th><th>Product</th><th>Store</th><th>Change</th><th>Before</th><th>Now</th></tr></thead>
          <tbody>
            {listed.map((row) => (
              <tr key={row.id}>
                <td className="muted">{row.when}</td>
                <td className="name"><ProductIdentifier name={row.product} url={row.url} /></td>
                <td className="muted">{row.store}</td>
                <td>{row.reason ? <p className="check-status">Not checked — {row.reason}</p> : <Pill status={row.kind} />}</td>
                <td>{row.before}</td>
                <td>{row.now}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="table-foot">
        <span>{listed.length} {listed.length === 1 ? "alert" : "alerts"}{example ? " · Example" : ""}</span>
      </div>
      <article className="note-card">
        <h3>How alerts work</h3>
        <p>Price alerts report a confirmed change in the product price.</p>
        <p>Stock alerts report a confirmed change in availability.</p>
      </article>
    </section>
  );
}

function Watch({ rows, note, onHide }: { rows: ProductRow[]; note: string | null; onHide: (id: string) => void }) {
  return (
    <section className="block">
      <div className="title-row">
        <h2>Watch list</h2>
      </div>
      <p className="intro">Your watched pages, check intervals and alert preferences.</p>
      {rows.length === 0 ? <p className="empty">No products watched yet.</p> : null}
      <div className="table-wrap">
        <table>
          <thead><tr><th>Product</th><th>Page</th><th>How often</th><th>Alert me on</th><th>Last attempt</th><th>Status</th><th /></tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td className="name"><ProductIdentifier name={row.name} url={row.url} /></td>
                <td><PageLink row={row} /></td>
                <td className="muted">{row.every}</td>
                <td className="muted">{row.alertOn}</td>
                <td className="muted"><AttemptTime row={row} /></td>
                <td><RowStatus row={row} /></td>
                <td className="end">
                  {row.live ? (
                    <form action={removeProduct}>
                      <input type="hidden" name="id" value={row.id} />
                      <button type="submit" className="text">Remove</button>
                    </form>
                  ) : (
                    <button type="button" className="text" onClick={() => onHide(row.id)}>Remove</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="table-foot">
        <span>{note ?? `${rows.length} on the watch list`}</span>
      </div>
    </section>
  );
}

function Results({ hit }: { hit: NonNullable<ReturnType<typeof searchTiger>> }) {
  return (
    <section className="block">
      <div className="title-row">
        <h2>Results for “{hit.query}”</h2>
      </div>
      <p className="intro">{hit.total} {hit.total === 1 ? "match" : "matches"} in your watched products, reads and alerts.</p>
      <h3 className="group">Watched products</h3>
      <SimpleTable
        heads={["Product", "Page", "Stock", "Last attempt", "Status"]}
        rows={hit.products.map((row) => [<ProductIdentifier key="product" name={row.name} url={row.url} />, <PageLink key="page" row={row} />, row.stock, <AttemptTime key="time" row={row} />, <RowStatus key="status" row={row} />])}
        empty="No watched products match."
      />
      <h3 className="group">Reads</h3>
      <SimpleTable
        heads={["When", "Product", "Price", "Stock", "Status"]}
        rows={hit.reads.map((row: ReadRow) => [row.when, <ProductIdentifier key="product" name={row.product} url={row.url} />, row.price, row.stock, row.reason ? `Not checked — ${row.reason}` : "Read"])}
        empty="No reads match."
        priceCol={2}
      />
      <h3 className="group">Alerts</h3>
      <SimpleTable
        heads={["When", "Product", "What changed"]}
        rows={hit.alerts.map((row) => [row.when, <ProductIdentifier key="product" name={row.product} url={row.url} />, row.change])}
        empty="No alerts match."
      />
    </section>
  );
}

function AddProduct({ onClose }: { onClose: () => void }) {
  const [priceOn, setPriceOn] = useState(true);
  const [stockOn, setStockOn] = useState(true);
  return (
    <div className="scrim" role="presentation" onClick={onClose}>
      <div className="popup narrow" role="dialog" aria-modal="true" aria-labelledby="add-title" onClick={(event) => event.stopPropagation()}>
        <form action={addProduct} className="add-form">
          <div className="popup-head">
            <div>
              <h2 id="add-title">Add a product</h2>
              <p className="sub">Set the product page, check interval and alert preferences.</p>
            </div>
            <button type="button" className="text" onClick={onClose}>Close</button>
          </div>
          <label>
            Product page link
            <input name="url" type="url" required placeholder="https://store.example/product" autoComplete="off" />
            <span className="fine">Use a public page that requires no sign-in or subscription.</span>
          </label>
          <label>
            How often
            <select name="every" defaultValue="6h">
              {EVERY_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
          <fieldset>
            <legend>Alert me when</legend>
            <label className="check">
              <input name="price" type="checkbox" checked={priceOn} onChange={(event) => setPriceOn(event.target.checked)} />
              The price changes
            </label>
            <label className="check">
              <input name="stock" type="checkbox" checked={stockOn} onChange={(event) => setStockOn(event.target.checked)} />
              The stock changes
            </label>
            <span className="fine">Alerts are sent only after a completed check confirms a change.</span>
          </fieldset>
          <div className="actions">
            <button type="button" className="line small" onClick={onClose}>Cancel</button>
            <button type="submit" className="primary">Start watching</button>
          </div>
        </form>
      </div>
    </div>
  );
}
