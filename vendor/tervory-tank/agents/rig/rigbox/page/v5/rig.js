/* Rig v5 shopper page.
 *
 * Real engine (this host's /v1/rig):
 *   search   GET  /v1/rig/search      the prompt, the matched pistol, approved fits
 *   product  GET  /v1/rig/products/id the pistol when the prompt names only a part
 *   compare  POST /v1/rig/compare     market average, only for items the shopper picks
 *   quote    POST /v1/rig/quote       Get A Quote, the shop's existing quote request
 */
(function () {
  "use strict";

  const KEY = window.RIG_KEY || "";
  const SHOP = window.RIG_SHOP || "";
  const MOBILE = window.matchMedia("(max-width: 760px)");
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Slots on the pistol. Hotspots are percentages of the Type B image box.
  const SLOTS = [
    { id: "optic", label: "Optic", groups: ["red-dot"], side: "l", hot: [21, 5] },
    { id: "screws", label: "Optic screws", groups: ["screws"], side: "l", hot: [31, 10] },
    { id: "magazine", label: "Magazine", groups: ["magazine"], side: "l", hot: [17, 94] },
    { id: "barrel", label: "Barrel", groups: ["barrel"], side: "r", hot: [95, 13] },
    { id: "light", label: "Light", groups: ["light"], side: "r", hot: [79, 39] },
    { id: "trigger", label: "Trigger", groups: ["trigger"], side: "r", hot: [45, 46] },
    { id: "holster", label: "Holster", groups: ["holster"], side: "r", hot: null },
  ];
  const SUP_COLOUR = { A: "var(--data-line)", B: "var(--data-positive)", C: "var(--data-warn)" };
  const BUILD_WORDS = /^(build( it)?( all)?( at)?( the)? best prices?|best prices?|build it|build all)\.?$/i;

  const state = {
    q: "",
    offers: null,
    pistol: null,
    pistols: [],
    slots: [],
    matched: new Set(),
    market: {},
    honest: "",
    message: "",
    mode: "result",
    openSlot: null,
    openIdx: 0,
  };

  // ---------- data ----------
  // The publishable key is rate limited for every shopper together, so repeat searches come from memory.
  const searches = new Map();
  async function find(q, limit) {
    const k = limit + "|" + q.toLowerCase();
    if (!searches.has(k)) {
      const p = api("/v1/rig/search?limit=" + limit + "&q=" + encodeURIComponent(q));
      searches.set(k, p);
      p.catch(() => searches.delete(k));
    }
    return searches.get(k);
  }

  async function api(path, body) {
    const init = { headers: { "X-Rig-Key": KEY, Accept: "application/json" } };
    if (body !== undefined) {
      init.method = "POST";
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const res = await fetch(path, init);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error((data.error && data.error.message) || "Rig could not answer.");
      err.status = res.status;
      throw err;
    }
    return data;
  }

  const isPistol = (p) => String(p.category || "") === "Pistols";
  const group = (p) => String((p.attributes && p.attributes.group) || "");
  const short = (name) => String(name || "");
  const amount = (p) => Number((p.price && p.price.amount) || 0);
  const status = (p) => String((p.availability && p.availability.status) || (p.attributes && p.attributes.stock && p.attributes.stock.status) || "");
  const quotable = (p) => status(p) !== "out_of_stock";

  function money(n, cur) {
    if (n == null || isNaN(n)) return "—";
    const v = Math.round(Number(n));
    const s = Math.abs(v).toString().replace(/\B(?=(\d{3})+(?!\d))/g, "\u202f");
    const sym = (cur || "ZAR") === "ZAR" ? "R\u00a0" : (cur || "") + "\u00a0";
    return (v < 0 ? "−" : "") + sym + s;
  }

  function supOffers(p) {
    const all = (state.offers && state.offers.offers) || {};
    return (all[p.id] || []).slice().sort((a, b) => a.supplier.localeCompare(b.supplier));
  }

  // The build uses the catalogue price. The supplier holding it in stock at that price is the best one.
  function bestSupplier(p) {
    const list = supOffers(p);
    const inStock = list.filter((o) => o.status === "in_stock" || o.status === "low_stock");
    const exact = inStock.find((o) => Math.round(o.price) === Math.round(amount(p)));
    if (exact) return exact;
    if (inStock.length) return inStock.slice().sort((a, b) => a.price - b.price)[0];
    return list.find((o) => Math.round(o.price) === Math.round(amount(p))) || list[0] || null;
  }

  function stockClass(o) {
    if (!o) return "";
    return o.status === "in_stock" ? "in" : o.status === "low_stock" ? "low" : "";
  }

  function stockText(o) {
    if (!o) return "";
    if (o.status === "in_stock") return o.qty + " in stock";
    if (o.status === "low_stock") return o.qty + " left";
    if (o.status === "on_order") return "On order · due " + (o.due || "—");
    return "0 · due " + (o.due || "—");
  }

  function media(p, cls) {
    const id = p.id || "";
    const g = group(p);
    if (isPistol(p)) return `<span class="m" style="--src:url('/page/v5/media/blueprint-left.svg')"></span>`;
    const mask = { screws: "part-optic-plate", trigger: "part-trigger", barrel: "part-barrel-threaded" }[g] ||
      (g === "magazine" ? (id.endsWith("18") ? "part-mag19" : "part-mag16") : "");
    if (mask) return `<span class="m" style="--src:url('/page/v5/media/${mask}.svg')"></span>`;
    const ico = { "red-dot": "ico-dot", light: "ico-light", holster: "ico-holster" }[g] || "ico-spark";
    return `<i class="ico ${ico}"></i>`;
  }

  // ---------- search ----------
  async function search(q) {
    q = String(q || "").trim();
    if (!q || !KEY) return;
    if (state.pistol && BUILD_WORDS.test(q)) {
      buildAll();
      return;
    }
    state.q = q;
    remember(q);
    setUrl({ q, mode: null, open: null });
    closeSheets();
    let first;
    try {
      first = await find(q, 24);
    } catch (e) {
      toast(e.message || "Rig could not answer.");
      return;
    }
    state.honest = first.honest || (first.results && first.results.length ? "exact" : "nothing");
    state.message = first.message || "";
    const rows = first.results || [];
    if (!rows.length || state.honest === "nothing" || state.honest === "not_stocked") {
      showIdk(q, state.message);
      return;
    }
    state.matched = new Set(rows.filter((r) => !isPistol(r) && (r.badges || []).includes("Exact match")).map((r) => r.id));
    let pistol = rows.find((r) => isPistol(r) && r.id === first.focus_id) || rows.find(isPistol);
    if (!pistol) {
      const focus = first.focus_id || rows[0].id;
      const fit = (first.fits || []).find((f) => f.part_sku === focus && !(f.rule && f.rule.fits === false) && f.fits_family);
      const famId = (fit && (fit.fits_sku || fit.fits_family)) || "";
      if (famId) {
        try {
          pistol = await api("/v1/rig/products/" + encodeURIComponent(famId));
        } catch (e) {
          pistol = null;
        }
      }
    }
    if (!pistol) {
      showIdk(q, state.message || "No pistol in the catalogue matches that.");
      return;
    }
    await loadPistol(pistol.name, pistol.id);
  }

  async function loadPistol(name, id) {
    let body;
    try {
      body = await find(name, 24);
    } catch (e) {
      toast(e.message);
      return;
    }
    const rows = body.results || [];
    const pistol = rows.find((r) => r.id === id) || rows.find(isPistol);
    if (!pistol) return showIdk(name, body.message || "");
    state.pistol = pistol;
    state.pistols = rows.filter(isPistol);
    const fits = rows.filter((r) => !isPistol(r) && r.fit && r.fit.fits === true);
    state.slots = SLOTS.map((s) => {
      const options = fits
        .filter((p) => s.groups.includes(group(p)))
        .sort((a, b) => (state.matched.has(b.id) - state.matched.has(a.id)) || (quotable(b) - quotable(a)) || (amount(a) - amount(b)));
      const idx = Math.max(0, options.findIndex((p) => state.matched.has(p.id)));
      return { ...s, options, idx, on: options.some((p) => state.matched.has(p.id)) };
    }).filter((s) => s.options.length);
    state.mode = "result";
    showStage();
    await refreshMarket();
  }

  function selection() {
    const out = [];
    if (state.pistol) out.push({ slot: null, product: state.pistol });
    for (const s of state.slots) if (s.on) out.push({ slot: s, product: s.options[s.idx] });
    return out;
  }

  // Market prices are fetched only for items the shopper has picked.
  async function refreshMarket(extra) {
    const ids = selection().map((x) => x.product.id).concat(extra || []);
    const missing = ids.filter((id) => !(id in state.market));
    if (missing.length) {
      try {
        const body = await api("/v1/rig/compare", { product_ids: missing });
        for (const line of body.lines || []) state.market[line.id] = line.market || null;
      } catch (e) {
        for (const id of missing) state.market[id] = null;
      }
    }
    renderStage();
  }

  function buildAll() {
    for (const s of state.slots) {
      if (s.id === "holster" && !state.matched.has(s.options[s.idx].id)) continue;
      const pick = s.options.findIndex((p) => state.matched.has(p.id));
      const ok = s.options.findIndex(quotable);
      s.idx = pick >= 0 ? pick : ok >= 0 ? ok : s.idx;
      s.on = quotable(s.options[s.idx]);
    }
    state.mode = "build";
    setUrl({ mode: "build" });
    setDockQuery("Build it at the best prices");
    closeSheets();
    refreshMarket();
  }

  // ---------- views ----------
  function setView(v) {
    document.body.dataset.view = v;
    $("landing").hidden = v !== "landing";
    $("stage").hidden = v !== "result";
    $("idk").hidden = v !== "idk";
    $("dock").hidden = v === "landing";
    $("sumbar").hidden = v !== "result";
    window.scrollTo(0, 0);
  }

  function showStage() {
    setView("result");
    setDockQuery(state.mode === "build" ? "Build it at the best prices" : state.q);
    renderStage();
  }

  function showIdk(q) {
    state.pistol = null;
    state.pistols = [];
    state.slots = [];
    state.matched.clear();
    for (const id of ["idkLine", "idkWhy", "idkTry", "idkStats", "colL", "colR", "pistols", "hint", "sumThumbs"]) $(id).replaceChildren();
    $("heroInput").value = q;
    setView("landing");
  }

  function setDockQuery(q) {
    const input = $("dockInput");
    input.value = q;
    const parts = String(q).split(/\s*,\s*/).filter(Boolean);
    const view = $("qView");
    if (parts.length > 1) {
      view.innerHTML = `<span>${esc(parts[0])}</span>` + parts.slice(1).map((p) => `<span class="qc">${esc(p)}</span>`).join("");
      $("dockForm").classList.add("has-view");
    } else {
      view.innerHTML = "";
      $("dockForm").classList.remove("has-view");
    }
  }

  function renderStage() {
    if (!state.pistol) return;
    const mobile = MOBILE.matches;
    document.body.dataset.mode = state.mode;
    const p = state.pistol;
    const img = $("pistolImg");
    img.src = "/page/v5/media/blueprint-left.svg";
    img.classList.add("silhouette");
    img.alt = p.name;
    $("photoNote").hidden = true;
    $("photoNote").textContent = "";

    // Banner for a closest answer.
    const banner = $("banner");
    banner.hidden = !(state.honest === "closest" && state.message) || state.mode === "build";
    banner.textContent = state.message;

    // Cards
    const order = state.slots.slice();
    const numbered = order.filter((s) => s.hot);
    const num = (s) => numbered.indexOf(s) + 1;
    const cards = order.filter((s) => s.id !== "holster" || s.on || state.matched.has(s.options[s.idx].id));
    const extras = order.filter((s) => !cards.includes(s));
    const left = $("colL");
    const right = $("colR");
    left.innerHTML = "";
    right.innerHTML = "";
    if (mobile) {
      const mob = cards.slice().sort((a, b) => (b.on - a.on));
      if (state.mode === "build") left.insertAdjacentHTML("beforeend", linesPanel(true));
      for (const s of mob) left.insertAdjacentHTML("beforeend", card(s, num(s)));
      if (extras.length) left.insertAdjacentHTML("beforeend", moreFits(extras));
    } else {
      for (const s of cards) (s.side === "l" ? left : right).insertAdjacentHTML("beforeend", card(s, num(s)));
      if (state.mode === "build") right.insertAdjacentHTML("beforeend", linesPanel(false));
      else if (extras.length || altCount()) right.insertAdjacentHTML("beforeend", moreFits(extras));
    }

    // Hotspots
    $("hots").innerHTML = (isB ? numbered : []).map((s) =>
      `<button type="button" class="hot${s.on ? " on" : ""}" data-slot="${s.id}" style="left:${s.hot[0]}%;top:${s.hot[1]}%" aria-label="${esc(s.label)}">${num(s)}</button>`
    ).join("");

    // Hint
    const asked = state.matched.size;
    const offCount = state.slots.filter((s) => !s.on).length;
    const typeName = short(p.name);
    let hint;
    if (state.mode === "build") hint = `<i class="ico ico-spark"></i><span>Built at the best in-stock prices.</span>`;
    else if (asked) hint = `<i class="ico ico-spark"></i><span>Matched ${asked} of your asks. ${offCount} more fit.</span> <button type="button" class="link" data-act="build">Build it at the best prices</button>`;
    else hint = `<i class="ico ico-spark"></i><span>${state.slots.length} approved fits for the ${esc(typeName)}.</span> <button type="button" class="link" data-act="build">Build it at the best prices</button>`;
    $("hint").innerHTML = hint;

    // Pistols strip
    $("pistols").innerHTML = `<span class="caps">${state.pistols.length} pistols</span>` + state.pistols.map((x) =>
      `<button type="button" class="pcard${x.id === p.id ? " on" : ""}" data-pistol="${esc(x.id)}" data-name="${esc(x.name)}">
        <span class="pimg"><span class="sil"></span></span>
        <span><b>${esc(mobile ? short(x.name).replace(/^Type B /, "") || short(x.name) : short(x.name))}</b><small>${money(amount(x), x.price.currency)}</small></span>
      </button>`).join("");

    // Dots for the phone carousel
    $("dots").innerHTML = mobile && state.mode !== "build" ? cards.map((_, i) => `<i class="${i === 0 ? "on" : ""}"></i>`).join("") + (extras.length ? "<i></i>" : "") : "";

    renderSum();
    requestAnimationFrame(drawWires);
  }

  function partName(p) {
    return String(p.name || "").replace(/^\w/, (c) => c.toUpperCase());
  }

  function altCount() {
    return state.slots.reduce((n, s) => n + Math.max(0, s.options.length - 1), 0);
  }

  function card(s, n) {
    const p = s.options[s.idx];
    const best = bestSupplier(p);
    const mk = state.market[p.id];
    const cur = p.price.currency;
    const delta = mk && mk.amount ? Math.round(((amount(p) - mk.amount) / mk.amount) * 100) : null;
    const sups = supOffers(p).map((o) =>
      `<span class="sup ${stockClass(o)}${best && o.supplier === best.supplier ? " b" : ""}"><i></i>${o.supplier}</span>`).join("");
    const fitNote = (p.fit && (p.fit.badge || p.fit.note)) || "Approved fit";
    const flag = s.on
      ? `<span class="chk" data-act="toggle" data-slot="${s.id}" role="button" aria-label="Remove from build"><i class="ico ico-check"></i></span>`
      : `<span class="addfit" data-act="toggle" data-slot="${s.id}" role="button" aria-label="Add to build"><i class="ico ico-plus"></i>Fits</span>`;
    const mnum = MOBILE.matches && s.hot ? `<span class="mnum">${n}</span> ` : "";
    const market = s.on && mk ? `<span class="strike">${money(mk.amount, cur)}</span>` : "";
    const chip = s.on && delta != null ? `<span class="delta${delta > 0 ? " neg" : ""}">${delta > 0 ? "+" : "−"}${Math.abs(delta)}%</span>` : "";
    const avail = best && best.status !== "in_stock" && best.status !== "low_stock" ? `<span class="warn">${esc(stockText(best))}</span>` : "";
    return `<div class="card${s.on ? " on" : ""}" data-slot="${s.id}" role="button" tabindex="0" aria-label="${esc(s.label)}: ${esc(p.name)}">
      <div class="card-top">
        <span class="thumb">${media(p)}</span>
        <div class="card-body">
          <span class="caps">${mnum}${esc(s.label)}${s.options.length > 1 ? ` · ${s.idx + 1}/${s.options.length}` : ""}</span>
          <div class="card-name">${esc(partName(p))}</div>
          <div class="card-sub">${esc(p.id)} · ${esc(fitNote)}</div>
          <div class="card-price"><span class="num">${money(amount(p), cur)}</span>${market}${chip}</div>
        </div>
      </div>
      <span class="card-flag">${flag}</span>
      <div class="card-foot">${sups}${avail ? " " + avail : ""}<span class="best">${best ? "Best " + best.supplier : ""}</span></div>
    </div>`;
  }

  function moreFits(extras) {
    const rows = [];
    for (const s of extras) {
      const p = s.options[s.idx];
      rows.push(`<button type="button" class="more-row" data-slot="${s.id}"><span class="thumb">${media(p)}</span><span><b>${esc(s.label)}</b> · ${esc(p.name)}<small>${money(amount(p), p.price.currency)} · approved fit</small></span></button>`);
    }
    for (const s of state.slots) {
      if (s.options.length < 2 || extras.includes(s)) continue;
      const others = s.options.filter((_, i) => i !== s.idx).map((p) => p.name);
      rows.push(`<button type="button" class="more-row" data-slot="${s.id}"><span class="thumb">${media(s.options[(s.idx + 1) % s.options.length])}</span><span><b>${esc(s.label)}</b> · also ${esc(others.join(", "))}</span></button>`);
    }
    const count = extras.length + altCount();
    return `<div class="more-fits"><span class="caps">${count} more approved fit${count === 1 ? "" : "s"}</span>${rows.slice(0, 3).join("")}</div>`;
  }

  function totals() {
    const sel = selection();
    let build = 0;
    let market = 0;
    let marketKnown = true;
    for (const x of sel) {
      build += amount(x.product);
      const mk = state.market[x.product.id];
      if (mk && mk.amount) market += mk.amount;
      else {
        market += amount(x.product);
        if (!(x.product.id in state.market)) marketKnown = false;
      }
    }
    return { sel, build, market, save: market - build, marketKnown, cur: (state.pistol && state.pistol.price.currency) || "ZAR" };
  }

  function linesPanel(mobile) {
    const t = totals();
    const groups = {};
    for (const x of t.sel) {
      const b = bestSupplier(x.product);
      const k = b ? b.supplier : "?";
      (groups[k] = groups[k] || { lead: b && b.lead_days, lines: [], sum: 0 }).lines.push(x);
      groups[k].sum += amount(x.product);
    }
    const body = Object.keys(groups).sort().map((k) => {
      const g = groups[k];
      return `<div class="lp-group"><div class="lp-head"><i style="background:${SUP_COLOUR[k] || "var(--text-tertiary)"}"></i>Supplier ${k} <small>${g.lead ? g.lead + " days" : "on order"}</small><span class="num">${money(g.sum, t.cur)}</span></div>` +
        g.lines.map((x) => `<div class="lp-line"><span>${esc(x.slot ? partName(x.product) : short(x.product.name) + " pistol")}</span><span class="num">${money(amount(x.product), t.cur)}</span></div>`).join("") + `</div>`;
    }).join("");
    const pct = t.market ? Math.round((t.save / t.market) * 100) : 0;
    return `<div class="lines-panel"><header><span class="caps">Line items</span><small>best price each</small></header>${body}
      <div class="lp-total"><b>Total · ${t.sel.length} items</b><span class="num">${money(t.build, t.cur)}</span></div>
      <div class="lp-save"><span>Market ${money(t.market, t.cur)}</span><span class="pos">${t.save >= 0 ? "Save " + money(t.save, t.cur) + " · " + pct + "%" : "Above market " + money(-t.save, t.cur)}</span></div></div>`;
  }

  function renderSum() {
    const t = totals();
    const bar = $("sumbar");
    const build = state.mode === "build";
    bar.classList.toggle("build", build);
    $("sumLabel").textContent = build ? `Your build · ${t.sel.length} items` : "Build · live";
    $("sumBuild").textContent = money(t.build, t.cur);
    const mk = $("sumMarket");
    mk.textContent = money(t.market, t.cur);
    mk.classList.toggle("strike", build);
    const pct = t.market ? Math.round((t.save / t.market) * 100) : 0;
    $("sumSave").textContent = build && t.save > 0 ? `${money(t.save, t.cur)} · ${pct}%` : money(Math.max(0, t.save), t.cur);
    const thumbs = t.sel.slice(0, build ? 4 : 3).map((x) => `<span>${media(x.product)}</span>`).join("");
    const more = t.sel.length > (build ? 4 : 3) ? `<span class="more">+${t.sel.length - (build ? 4 : 3)}</span>` : "";
    $("sumThumbs").innerHTML = thumbs + more;
    const from = $("sumFrom");
    from.hidden = !build;
    if (build) {
      const count = {};
      for (const x of t.sel) {
        const b = bestSupplier(x.product);
        if (b) count[b.supplier] = (count[b.supplier] || 0) + 1;
      }
      const keys = Object.keys(count).sort();
      $("fromBar").innerHTML = keys.map((k) => `<i style="flex:${count[k]};background:${SUP_COLOUR[k]}"></i>`).join("");
      $("fromLegend").textContent = keys.map((k) => `${k}·${count[k]}`).join("  ");
      if (MOBILE.matches) $("fromLegend").innerHTML = keys.map((k) => `<span style="color:${SUP_COLOUR[k]}">●</span> Supplier ${k} · ${count[k]}`).join("&nbsp;&nbsp;");
    }
    $("mQuoteTotal").textContent = money(t.build, t.cur);
    $("quoteBottom").hidden = !(build && MOBILE.matches);
  }

  function drawWires() {
    const svg = $("wires");
    if (MOBILE.matches || !state.pistol) {
      svg.innerHTML = "";
      return;
    }
    const base = $("stage").getBoundingClientRect();
    const out = [];
    for (const s of state.slots) {
      if (!s.hot) continue;
      const cardEl = document.querySelector(`.col .card[data-slot="${s.id}"]`);
      const hotEl = document.querySelector(`.hot[data-slot="${s.id}"]`);
      if (!cardEl || !hotEl) continue;
      const c = cardEl.getBoundingClientRect();
      const h = hotEl.getBoundingClientRect();
      const y = c.top + 26 - base.top;
      const x = s.side === "l" ? c.right - base.left : c.left - base.left;
      const hx = h.left + h.width / 2 - base.left;
      const hy = h.top + h.height / 2 - base.top;
      const kx = s.side === "l" ? x + 34 : x - 34;
      out.push(`<path class="${s.on ? "on" : ""}" d="M${x},${y} L${kx},${y} L${hx},${hy}"/><circle cx="${kx}" cy="${y}" r="2"/>`);
    }
    svg.innerHTML = out.join("");
  }

  // ---------- one item: suppliers and market ----------
  async function openCompare(slotId, idx) {
    const s = state.slots.find((x) => x.id === slotId);
    if (!s) return;
    state.openSlot = slotId;
    state.openIdx = idx == null ? s.idx : idx;
    const p = s.options[state.openIdx];
    if (!(p.id in state.market)) await refreshMarket([p.id]);
    setUrl({ open: slotId });
    renderCompare();
  }

  function renderCompare() {
    const s = state.slots.find((x) => x.id === state.openSlot);
    if (!s) return;
    const p = s.options[state.openIdx];
    const cur = p.price.currency;
    const mk = state.market[p.id];
    const list = supOffers(p);
    const best = bestSupplier(p);
    const prices = list.map((o) => o.price).concat(mk ? [mk.low || mk.amount, mk.high || mk.amount] : []).concat([amount(p)]);
    const lo = Math.min(...prices);
    const hi = Math.max(...prices);
    const pad = (hi - lo) * 0.12 || hi * 0.05;
    const min = lo - pad;
    const max = hi + pad;
    const pos = (v) => (((v - min) / (max - min)) * 100).toFixed(2) + "%";
    const marks = list.map((o) => `<span class="mk ${stockClass(o)}" style="left:${pos(o.price)}">${o.supplier}</span>`).join("");
    const band = mk && mk.low != null ? `<span class="band" style="left:${pos(mk.low)};width:calc(${pos(mk.high)} - ${pos(mk.low)})"></span>` : "";
    const avg = mk ? `<span class="avg" style="left:${pos(mk.amount)}"></span><span class="avg-l" style="left:calc(${pos(mk.amount)} + 46px)">Market avg ${money(mk.amount, cur)}</span>` : "";
    const ends = mk && mk.low != null ? `<span class="end" style="left:${pos(mk.low)}">Market low ${money(mk.low, cur)}</span><span class="end" style="left:${pos(mk.high)};transform:translateX(-100%)">high ${money(mk.high, cur)}</span>` : "";
    const pin = best ? `<span class="best-pin" style="left:${pos(best.price)}">Best ${money(best.price, cur)}<span class="stem"></span><i></i></span>` : "";
    const rows = list.map((o) => {
      const vs = mk ? o.price - mk.amount : null;
      const pct = mk ? ((vs / mk.amount) * 100).toFixed(1) : null;
      const isBest = best && o.supplier === best.supplier;
      const out = !(o.status === "in_stock" || o.status === "low_stock");
      return `<div class="srow${isBest ? " best" : ""}${out ? " out" : ""}">
        <span class="sup-name"><span class="sup-badge">${o.supplier}</span><span><b>Supplier ${o.supplier}</b><small>API</small></span></span>
        <span class="st ${stockClass(o)}"><i></i>${esc(stockText(o))}<small class="m-lead">${o.lead_days ? " · " + o.lead_days + " d" : ""}</small></span>
        <span class="lead">${o.lead_days ? o.lead_days + " days" : "—"}</span>
        <span class="r price">${isBest ? `<span class="tag-best">BEST</span>` : ""}<span class="num">${money(o.price, cur)}</span></span>
        <span class="r vs">${vs == null ? "—" : `<span class="${vs <= 0 ? "pos" : "warn"}">${vs <= 0 ? "−" : "+"}${money(Math.abs(vs), cur).replace("R\u00a0", "R")}</span> <small class="muted">${pct > 0 ? "+" : ""}${pct}%</small>`}</span>
      </div>`;
    }).join("");
    const mkRow = mk
      ? `<div class="srow mkt"><span class="mk-l">● Market average · ${mk.sources || "several"} retailers</span><span class="r num">${money(mk.amount, cur)}</span><span class="r vs">—</span></div>`
      : `<div class="srow mkt"><span class="mk-l">No market price on file for this item.</span></div>`;
    const inBuild = s.on && s.idx === state.openIdx;
    const canUse = quotable(p);
    const fitNote = (p.fit && (p.fit.note || p.fit.badge)) || "Approved fit";
    const sheet = $("compare");
    sheet.innerHTML = `<div class="grabber"></div>
      <div class="cmp-head">
        <span class="cmp-img">${media(p)}</span>
        <div class="cmp-title"><span class="caps">${esc(s.label)} · approved fit</span><h2 id="cmpName">${esc(p.name)}</h2><p>Fits ${esc(state.pistol.name)} · ${esc(fitNote)}</p></div>
        <div class="cmp-nav">
          <button type="button" class="round" data-act="prev" ${state.openIdx === 0 ? "disabled" : ""} aria-label="Previous option"><i class="ico ico-left"></i></button>
          <span class="count">${state.openIdx + 1} / ${s.options.length}</span>
          <button type="button" class="round" data-act="next" ${state.openIdx >= s.options.length - 1 ? "disabled" : ""} aria-label="Next option"><i class="ico ico-right"></i></button>
        </div>
      </div>
      <div class="pvm">
        <div class="pvm-head"><span class="caps">Price vs market</span><span class="legend"><span><i></i>${esc(SHOP)} best</span><span><i class="tick"></i> Market avg</span></span></div>
        <div class="track"><span class="rail"></span>${band}${avg}${marks}${pin}${ends}</div>
        <div class="pvm-foot"><span>Price history</span><span>Not collected yet</span></div>
      </div>
      <div class="sup-table">
        <div class="shead"><span>Supplier</span><span>Stock</span><span class="lead">Lead</span><span class="r">Price</span><span class="r vs">vs market</span></div>
        ${rows}${mkRow}
      </div>
      <div class="cmp-foot"><small>Lowest in-stock price goes in your build.</small>
        ${inBuild
          ? `<button type="button" class="btn-ghost" data-act="close"><i class="ico ico-check"></i>Using Supplier ${best ? best.supplier : ""}</button>`
          : canUse
            ? `<button type="button" class="btn-ghost" data-act="use"><i class="ico ico-plus"></i>Add to build · Supplier ${best ? best.supplier : ""}</button>`
            : `<span class="warn">Out of stock at every supplier</span>`}
      </div>
      <button type="button" class="round x" data-act="close" aria-label="Close"><i class="ico ico-x"></i></button>`;
    sheet.hidden = false;
    $("scrim").hidden = false;
    document.body.classList.add("cmp-open");
    document.querySelectorAll(".col .card").forEach((el) => el.classList.toggle("on-view", el.dataset.slot === s.id));
  }

  // ---------- Get A Quote ----------
  function openQuote() {
    const t = totals();
    const lines = t.sel.filter((x) => quotable(x.product));
    const sheet = $("quote");
    sheet.innerHTML = `<div class="grabber"></div>
      <h2 id="qTitle">Get A Quote</h2>
      <p class="lead">${esc(SHOP)} confirms stock and final prices. Nothing is charged.</p>
      <div class="q-lines">${lines.map((x) => `<div><span>${esc(x.slot ? partName(x.product) : x.product.name)}</span><span class="num">${money(amount(x.product), t.cur)}</span></div>`).join("")}
        <div class="t"><span>Total · ${lines.length} items</span><span class="num">${money(lines.reduce((n, x) => n + amount(x.product), 0), t.cur)}</span></div></div>
      <form class="q-form" id="qForm">
        <label>Name<input name="name" autocomplete="name" required></label>
        <label>Email<input name="email" type="email" autocomplete="email" required></label>
        <label class="full">Phone (optional)<input name="phone" type="tel" autocomplete="tel"></label>
        <div class="q-err" id="qErr"></div>
        <div class="q-actions"><small>Your quote request goes to ${esc(SHOP)}.</small><button class="btn-action" type="submit">Send quote request <i class="ico ico-arrow"></i></button></div>
      </form>
      <button type="button" class="round x" data-act="close" aria-label="Close"><i class="ico ico-x"></i></button>`;
    sheet.hidden = false;
    $("scrim").hidden = false;
    $("qForm").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const f = new FormData(ev.target);
      const contact = { name: f.get("name"), email: f.get("email"), phone: f.get("phone") || "" };
      const setup = {
        tier: "build",
        label: "Rig build",
        parts: lines.map((x) => ({ role: x.slot ? x.slot.id : "pistol", product: { id: x.product.id }, choice: "" })),
      };
      const btn = ev.target.querySelector("button[type=submit]");
      btn.disabled = true;
      try {
        const body = await api("/v1/rig/quote", { setup, contact });
        sheet.innerHTML = `<div class="q-done"><div class="ok"><i class="ico ico-check"></i></div><h2>Quote request saved</h2>
          <p class="lead">${body.delivered === false ? "Saved. " + esc(SHOP) + " will follow up." : esc(SHOP) + " has your quote request."} Reference ${esc(body.request_id || "")}.</p>
          <button type="button" class="btn-ghost" data-act="close">Done</button></div>`;
      } catch (e) {
        $("qErr").textContent = e.message;
        btn.disabled = false;
      }
    });
  }

  function closeSheets() {
    $("compare").hidden = true;
    $("quote").hidden = true;
    $("scrim").hidden = true;
    state.openSlot = null;
    document.body.classList.remove("cmp-open");
    document.querySelectorAll(".col .card").forEach((el) => el.classList.remove("on-view"));
    setUrl({ open: null });
  }

  // ---------- small things ----------
  function toast(msg) {
    const t = $("toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast.t);
    toast.t = setTimeout(() => (t.hidden = true), 3200);
  }

  function remember(q) {
    try {
      const list = JSON.parse(localStorage.getItem("rig.recent") || "[]").filter((x) => x !== q);
      list.unshift(q);
      localStorage.setItem("rig.recent", JSON.stringify(list.slice(0, 6)));
    } catch (e) { /* private mode */ }
  }

  function setUrl(patch) {
    const u = new URL(location.href);
    for (const [k, v] of Object.entries(patch)) {
      if (v == null || v === "") u.searchParams.delete(k);
      else u.searchParams.set(k, v);
    }
    history.replaceState(null, "", u.toString());
  }

  // ---------- events ----------
  document.addEventListener("click", (ev) => {
    const t = ev.target.closest("[data-q],[data-act],[data-slot],[data-pistol],#recentBtn,#saveBtn,#quoteTop,#quoteBottom,#scrim");
    if (!t) {
      if (!ev.target.closest("#recent")) $("recent").hidden = true;
      return;
    }
    if (t.id === "recentBtn") {
      let list = [];
      try { list = JSON.parse(localStorage.getItem("rig.recent") || "[]"); } catch (e) { list = []; }
      $("recent").innerHTML = list.length ? list.map((q) => `<button type="button" data-q="${esc(q)}">${esc(q)}</button>`).join("") : "<p>No searches yet.</p>";
      $("recent").hidden = !$("recent").hidden;
      return;
    }
    if (t.id === "saveBtn") {
      navigator.clipboard && navigator.clipboard.writeText(location.href).then(() => toast("Link to this search copied."), () => toast(location.href));
      return;
    }
    if (t.id === "quoteTop" || t.id === "quoteBottom") return openQuote();
    if (t.id === "scrim") return closeSheets();
    if (t.dataset.q) {
      $("recent").hidden = true;
      return search(t.dataset.q);
    }
    const act = t.dataset.act;
    if (act === "build") return buildAll();
    if (act === "close") return closeSheets();
    if (act === "prev" || act === "next") {
      const s = state.slots.find((x) => x.id === state.openSlot);
      state.openIdx = Math.max(0, Math.min(s.options.length - 1, state.openIdx + (act === "next" ? 1 : -1)));
      return openCompare(s.id, state.openIdx);
    }
    if (act === "use") {
      const s = state.slots.find((x) => x.id === state.openSlot);
      s.idx = state.openIdx;
      s.on = true;
      return refreshMarket().then(renderCompare);
    }
    if (act === "toggle") {
      ev.stopPropagation();
      const s = state.slots.find((x) => x.id === t.dataset.slot);
      if (!s.on && !quotable(s.options[s.idx])) return toast(s.options[s.idx].name + " is out of stock.");
      s.on = !s.on;
      if (!s.on && state.mode === "build") state.mode = "result";
      return refreshMarket();
    }
    if (t.dataset.pistol) {
      state.matched = new Set();
      state.q = t.dataset.name;
      setDockQuery(t.dataset.name);
      setUrl({ q: t.dataset.name, mode: null });
      return loadPistol(t.dataset.name, t.dataset.pistol);
    }
    if (t.dataset.slot) return openCompare(t.dataset.slot);
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closeSheets();
    if (ev.key === "Enter" && ev.target.classList && ev.target.classList.contains("card")) openCompare(ev.target.dataset.slot);
  });

  $("heroForm").addEventListener("submit", (ev) => { ev.preventDefault(); search($("heroInput").value); });
  $("dockForm").addEventListener("submit", (ev) => { ev.preventDefault(); $("dockInput").blur(); search($("dockInput").value); });
  $("dockInput").addEventListener("input", () => $("dockForm").classList.remove("has-view"));
  $("colL").addEventListener("scroll", () => {
    const el = $("colL");
    const i = Math.round(el.scrollLeft / 282);
    document.querySelectorAll("#dots i").forEach((d, k) => d.classList.toggle("on", k === i));
  });
  window.addEventListener("resize", () => requestAnimationFrame(drawWires));
  MOBILE.addEventListener("change", () => state.pistol && renderStage());

  // ---------- start ----------
  (async function start() {
    const params = new URLSearchParams(location.search);
    const skin = params.get("skin");
    if (skin === "cn" || skin === "rig") document.documentElement.dataset.skin = skin;
    const q = params.get("q");
    if (!q) {
      setView("landing");
      return;
    }
    await search(q);
    if (params.get("mode") === "build" && state.pistol) buildAll();
    const open = params.get("open");
    if (open && state.pistol) openCompare(open);
  })();
})();
