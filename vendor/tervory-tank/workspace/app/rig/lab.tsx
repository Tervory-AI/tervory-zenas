"use client";

import { useEffect, useState } from "react";
import {
  ACCOUNT_BOTTOM,
  ACCOUNT_NAV,
  BUILT,
  CHANGES,
  PLAN,
  SKIN_STEPS,
  TABS,
  type TabId,
  type WaitingFit,
} from "./copy";
import { Plugins } from "./plugins";
import { SearchView } from "./search-view";
import { SquadLinks } from "../squad-links";
import type { PluginPanel } from "./vault-model";
type Decided = { id: string; name: string; fits: string; why: string; state: string; on: boolean };

function Pill({ children, on = true }: { children: string; on?: boolean }) {
  return (
    <span className={on ? "pill" : "pill off"}>
      <i />
      {children}
    </span>
  );
}

function CompactSearch({ onSearch }: { onSearch: (query: string) => void }) {
  return (
    <form
      className="compact"
      role="search"
      onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        const query = String(data.get("q") ?? "").trim();
        if (query) onSearch(query);
      }}
    >
      <input name="q" placeholder="Search a brand, model or part" aria-label="Search a brand, model or part" />
      <button type="submit" className="line">
        Search
      </button>
    </form>
  );
}

export function CatalogueNumbers({ products, fits }: { products: number | null; fits: number | null }) {
  if (products === null || products === 0 || fits === null) {
    return null;
  }
  return (
    <section className="counts" aria-label="Counts">
      <article className="count">
        <b>{products}</b>
        <strong>Products</strong>
        <p className="fine">In this account&apos;s catalogue.</p>
      </article>
      <article className="count">
        <b>{fits}</b>
        <strong>Approved fits</strong>
        <p className="fine">Parts confirmed to fit a product.</p>
      </article>
    </section>
  );
}

export function Lab({
  accountName,
  products,
  fits,
  plugins,
}: {
  accountName: string;
  products: number | null;
  fits: number | null;
  plugins: PluginPanel;
}) {
  const [tab, setTab] = useState<TabId>("search");
  const [pluginsOpen, setPluginsOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [waiting, setWaiting] = useState<WaitingFit[]>([]);
  const [decided, setDecided] = useState<Decided[]>([]);
  const [approve, setApprove] = useState<WaitingFit | null>(null);
  const [keySaved, setKeySaved] = useState(false);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setPluginsOpen(false);
      setMenuOpen(false);
      setApprove(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function show(next: TabId) {
    setPluginsOpen(false);
    setMenuOpen(false);
    setApprove(null);
    setTab(next);
  }

  function searchFrom(value: string) {
    setQuery(value);
    show("search");
  }

  function confirmApprove() {
    if (!approve) return;
    setDecided((current) => [{ id: approve.id, name: approve.name, fits: approve.fits, why: approve.why, state: "Approved", on: true }, ...current]);
    setWaiting((current) => current.filter((row) => row.id !== approve.id));
    setApprove(null);
  }

  function reject(row: WaitingFit) {
    setDecided((current) => [{ id: row.id, name: row.name, fits: row.fits, why: row.why, state: "Rejected", on: false }, ...current]);
    setWaiting((current) => current.filter((item) => item.id !== row.id));
  }

  const rootClass = ["rig", pluginsOpen || approve ? "modal-open" : "", menuOpen ? "menu-open" : ""].filter(Boolean).join(" ");

  return (
    <div className={rootClass}>
      <header className="topbar">
        <img src="/brand/tervory-logo-reversed.png" alt="Tervory" width={186} height={34} />
        <button type="button" className="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen((open) => !open)}>
          Menu
        </button>
      </header>

      <aside className="panel" aria-label="Rig">
        <img className="rig-mark" src="/brand/rig-on-dark.png" alt="Rig" width={1173} height={651} />
        <div className="panel-top">
          <nav className="std" aria-label="Account">
            <p className="account">{accountName}</p>
            {ACCOUNT_NAV.map((link) => (
              <a key={link.label} href={link.href}>
                {link.label}
              </a>
            ))}
          </nav>
          <SquadLinks className="std squad-nav" />
        </div>
        <div className="panel-own">
          <p className="rig-label">RIG</p>
          <nav aria-label="Rig">
            {TABS.map((item) => (
              <button key={item.id} type="button" className={item.id === tab && !pluginsOpen ? "nav on" : "nav"} aria-current={item.id === tab && !pluginsOpen ? "page" : undefined} onClick={() => show(item.id)}>
                {item.label}
              </button>
            ))}
            <button type="button" className={pluginsOpen ? "nav on" : "nav"} aria-expanded={pluginsOpen} onClick={() => { setMenuOpen(false); setApprove(null); setPluginsOpen(true); }}>
              Plugins
            </button>
          </nav>
        </div>
        <div className="panel-bottom">
          <img className="tervory-mark" src="/brand/tervory-logo-reversed.png" alt="Tervory" width={186} height={34} />
          {ACCOUNT_BOTTOM.map((link) => (
            <a key={link.label} className="nav" href={link.href}>
              {link.label}
            </a>
          ))}
        </div>
      </aside>

      <div className={tab === "search" ? "body home" : "body"}>
        {tab === "search" ? <SearchView query={query} onQuery={setQuery} hasCatalogue={products !== null && products > 0} /> : null}

        {tab === "overview" ? (
          <>
            <header className="head">
              <div className="titles">
                <h1>Rig</h1>
                <p className="lede">Finds the parts that fit and puts the customer's build together at the best prices.</p>
              </div>
              <CompactSearch onSearch={searchFrom} />
              <a className="primary" href="https://rig.tervory.com">
                Open live search
              </a>
            </header>
            <CatalogueNumbers products={products} fits={fits} />
            <section className="block">
              <h2>What's built</h2>
              <div className="sheet">
                {BUILT.map((row) => (
                  <div className="wide-row" key={row.title}>
                    <div>
                      <h3>{row.title}</h3>
                      <p>{row.text}</p>
                    </div>
                    <Pill>Done</Pill>
                  </div>
                ))}
              </div>
            </section>
            <section className="block">
              <h2>Next step</h2>
            </section>
            <footer className="foot">
            </footer>
          </>
        ) : null}

        {tab === "catalogues" ? (
          <>
            <header className="head">
              <div className="titles">
                <h1>Catalogues</h1>
                <p className="lede">The product lists Rig searches.</p>
              </div>
              <CompactSearch onSearch={searchFrom} />
            </header>
            <CatalogueNumbers products={products} fits={fits} />
            {products !== null && products > 0 && fits !== null ? (
              <section className="block">
                <h2>This account</h2>
                <p className="intro">Counts come from the Rig room. Shops stay separate.</p>
              </section>
            ) : null}
            <footer className="foot">
              <p className="small">Market average prices are shown once a shopper's selection is final.</p>
            </footer>
          </>
        ) : null}

        {tab === "fits" ? (
          <>
            <header className="head">
              <div className="titles">
                <h1>Fits to review</h1>
                <p className="lede">New fits wait here until an admin approves them. Shoppers only see approved fits.</p>
              </div>
              <CompactSearch onSearch={searchFrom} />
            </header>
            <article className="card key-card">
              <div>
                <h3>Admin key</h3>
                <p>Approving or rejecting a fit needs the admin key. Once saved it is never shown again.</p>
              </div>
              {keySaved ? (
                <div className="mask">{null}</div>
              ) : (
                <input type="password" aria-label="Admin key" placeholder="Paste key" autoComplete="off" />
              )}
              <button
                type="button"
                className="text"
                onClick={() => {
                  setKeySaved((saved) => !saved);
                }}
              >
                Replace
              </button>
            </article>
            <section className="counts">
              <article className="count">
                <b>{waiting.length ? waiting.length : null}</b>
                <strong>Waiting</strong>
                <p className="fine">Suggested fits not yet decided.</p>
              </article>
              <article className="count">
                <b>{products && fits !== null ? fits : null}</b>
                <strong>Approved</strong>
                <p className="fine">{products && fits !== null ? "Showing to shoppers now." : null}</p>
              </article>
              <article className="count">
                <b>{decided.length ? decided.filter((row) => !row.on).length : null}</b>
                <strong>Rejected</strong>
                <p className="fine">Never shown to shoppers.</p>
              </article>
            </section>
            <section className="block">
              <h2>Waiting for review</h2>
              <p className="intro">Check each one against the maker's fit list before approving.</p>
              <div className="sheet">
                <table className="fits">
                  <thead>
                    <tr>
                      <th>Part</th>
                      <th>Why Rig thinks it fits</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {waiting.map((row) => (
                      <tr key={row.id}>
                        <td>
                          <strong>{row.name}</strong>
                          <p>{row.fits}</p>
                        </td>
                        <td>{row.why}</td>
                        <td className="act">
                          <button type="button" className="line" onClick={() => setApprove(row)}>
                            Approve
                          </button>
                          <button type="button" className="text" onClick={() => reject(row)}>
                            Reject
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
            <section className="block">
              <h2>Recently decided</h2>
              <div className="sheet">
                <table className="fits">
                  <thead>
                    <tr>
                      <th>Part</th>
                      <th>Reason</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {decided.map((row) => (
                      <tr key={row.id}>
                        <td>
                          <strong>{row.name}</strong>
                          <p>{row.fits}</p>
                        </td>
                        <td>{row.why}</td>
                        <td>
                          <Pill on={row.on}>{row.state}</Pill>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
            <footer className="foot">
              <p>Rig only looks for new fits when you press Find new fits. It never runs on its own.</p>
              <p className="small">Only approved fits are shown to shoppers.</p>
            </footer>
          </>
        ) : null}

        {tab === "skins" ? (
          <>
            <header className="head">
              <div className="titles">
                <h1>Skins</h1>
                <p className="lede">How Rig looks on each shop's site. A dealer's page carries the dealer's brand, never Tervory's.</p>
              </div>
              <CompactSearch onSearch={searchFrom} />
              <a className="primary" href="https://rig.tervory.com">
                Preview live page
              </a>
            </header>
            <section className="counts">
              <article className="count">
                <b />
                <strong>Live skin</strong>
                <p className="fine">Tervory, on rig.tervory.com.</p>
              </article>
              <article className="count">
                <b />
                <strong>Dealer skins</strong>
                <p className="fine">No dealer skin is on this page.</p>
              </article>
              <article className="count">
                <b />
                <strong>Dealer pages live</strong>
                <p className="fine">Built after the main product.</p>
              </article>
            </section>
            <section className="block">
              <h2>Skins</h2>
              <div className="skin-grid">
                <article className="card skin">
                  <div className="banner">Tervory skin preview</div>
                  <div className="card-top">
                    <h3>Tervory</h3>
                  </div>
                  <p>The default skin. Tervory logo, navy and red. Used on rig.tervory.com.</p>
                  <a className="text more" href="https://rig.tervory.com">
                    Open rig.tervory.com →
                  </a>
                </article>
              </div>
            </section>
            <section className="block">
              <h2>How a dealer skin works</h2>
              <ol className="sheet steps">
                {SKIN_STEPS.map((step, index) => (
                  <li key={step}>
                    <span>{index + 1}</span>
                    {step}
                  </li>
                ))}
              </ol>
            </section>
            <footer className="foot">
              <p>Dealer skins are planned, not built yet.</p>
            </footer>
          </>
        ) : null}

        {tab === "progress" ? (
          <>
            <header className="head">
              <div className="titles">
                <h1>Progress</h1>
                <p className="lede">Where Rig is on the build plan.</p>
              </div>
              <CompactSearch onSearch={searchFrom} />
            </header>
            <section className="block">
              <h2>Build plan</h2>
              <div className="sheet">
                {PLAN.map((row) => (
                  <div className="plan-row" key={row.title}>
                    <b>{row.code}</b>
                    <div>
                      <h3>{row.title}</h3>
                      <p>{row.text}</p>
                    </div>
                    <Pill on={row.on}>{row.state}</Pill>
                  </div>
                ))}
              </div>
            </section>
            <section className="block">
              <h2>Recent changes</h2>
              <p className="intro">What went live most recently.</p>
              <div className="sheet">
                {CHANGES.map((row) => (
                  <div className="change-row" key={row.id}>
                    <span>Change #{row.id}</span>
                    <strong>{row.title}</strong>
                    <span>{row.when}</span>
                  </div>
                ))}
              </div>
            </section>
            <footer className="foot">
              <p>Each change passes Rig's search checks before it goes live.</p>
            </footer>
          </>
        ) : null}
      </div>

      {pluginsOpen ? <Plugins panel={plugins} onClose={() => setPluginsOpen(false)} /> : null}

      {approve ? (
        <div className="scrim" role="presentation">
          <div className="popup approve" role="dialog" aria-modal="true" aria-labelledby="approve-title">
            <div className="popup-head">
              <h2 id="approve-title">Approve this fit?</h2>
              <button type="button" className="text close" onClick={() => setApprove(null)}>
                Close
              </button>
            </div>
            <p className="sub">Once approved, shoppers see it as a compatible upgrade.</p>
            <article className="fit-summary">
              <h3>{approve.name}</h3>
              <p>{approve.fits}</p>
              <p>Why Rig thinks it fits: {approve.why.charAt(0).toLowerCase() + approve.why.slice(1)}</p>
            </article>
            <label className="admin-field">
              Admin key
              <input readOnly value="" aria-label="Admin key" />
            </label>
            <div className="popup-actions">
              <button type="button" className="line" onClick={() => setApprove(null)}>
                Cancel
              </button>
              <button type="button" className="primary" onClick={confirmApprove}>
                Approve fit
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
