const form = document.querySelector("form.search");
const query = document.querySelector("#q");
const status = document.querySelector("#status");
const results = document.querySelector("#results");
const searchView = document.querySelector("#search");
const productView = document.querySelector("#product");
const main = document.querySelector("#main");
const productName = document.querySelector("#product-name");
const productPrice = document.querySelector("#product-price");
const productStatus = document.querySelector("#product-status");
const showAccessories = document.querySelector("#show-accessories");
const groups = document.querySelector("#groups");
const bundleParts = document.querySelector("#bundle-parts");
const bundleTotal = document.querySelector("#bundle-total");
const compareLines = document.querySelector("#compare-lines");

let currentSetup = null;
let currentGraph = null;

form.addEventListener("submit", (event) => {
  event.preventDefault();
  void run(query.value.trim());
});

document.querySelector("#back").addEventListener("click", () => {
  showSearch();
  history.pushState({}, "", "/");
});

showAccessories.addEventListener("click", () => {
  groups.hidden = false;
  if (currentGraph) renderGroups(currentGraph, currentSetup);
});

document.querySelector("#compare").addEventListener("click", () => {
  void comparePrices();
});

window.addEventListener("popstate", () => {
  const id = pathProduct();
  if (id) void openProduct(id, false);
  else showSearch();
});

if (pathProduct()) void openProduct(pathProduct(), false);

async function run(q) {
  results.replaceChildren();
  if (!q) {
    status.textContent = "Type a name to search.";
    return;
  }
  status.textContent = "Searching…";
  const params = new URLSearchParams({ q, limit: "12" });
  let response;
  try {
    response = await fetch("/v1/rig/search?" + params.toString(), {
      headers: {
        Accept: "application/json",
        "X-Rig-Key": window.RIG_KEY || "",
      },
    });
  } catch {
    status.textContent = "Search did not complete. Try again.";
    return;
  }
  if (!response.ok) {
    status.textContent = "Search did not complete. Try again.";
    return;
  }
  const body = await response.json();
  const items = Array.isArray(body.results) ? body.results : [];
  if (items.length === 0) {
    status.textContent = "Nothing matched that search.";
    return;
  }
  if (body.exact_match === false) {
    status.textContent = body.message || "No exact match for that model.";
    const label = document.createElement("li");
    label.className = "closest";
    label.textContent = "Closest matches";
    results.append(label);
  } else {
    status.textContent = items.length === 1 ? "1 product" : items.length + " products";
  }
  for (const item of items) results.append(card(item));
}

function card(item) {
  const row = document.createElement("li");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "card";
  const name = document.createElement("h2");
  name.textContent = item.name || "";
  const meta = document.createElement("p");
  meta.className = "meta";
  meta.textContent = [item.brand, item.category].filter(Boolean).join(" · ");
  const price = document.createElement("p");
  price.className = "price";
  price.textContent = priceLine(item.price);
  button.append(name, meta, price);
  button.addEventListener("click", () => {
    void openProduct(item.id, true);
  });
  row.append(button);
  return row;
}

async function openProduct(id, push) {
  if (!id) return;
  if (push) history.pushState({}, "", "/product/" + encodeURIComponent(id));
  searchView.hidden = true;
  productView.hidden = false;
  main.classList.add("wide");
  productName.textContent = "";
  productPrice.textContent = "";
  productStatus.textContent = "Loading…";
  groups.hidden = true;
  groups.replaceChildren();
  bundleParts.replaceChildren();
  bundleTotal.textContent = "";
  compareLines.hidden = true;
  compareLines.replaceChildren();
  currentSetup = null;
  currentGraph = null;
  let graphResponse;
  let setupResponse;
  try {
    [graphResponse, setupResponse] = await Promise.all([
      rigFetch("/v1/rig/graph/" + encodeURIComponent(id)),
      rigFetch("/v1/rig/setups", {
        method: "POST",
        body: JSON.stringify({ product_id: id }),
      }),
    ]);
  } catch {
    productStatus.textContent = "That product did not load. Try again.";
    return;
  }
  if (!graphResponse.ok || !setupResponse.ok) {
    productStatus.textContent = "No such product.";
    return;
  }
  currentGraph = await graphResponse.json();
  const setups = await setupResponse.json();
  currentSetup = Array.isArray(setups.setups) ? setups.setups[0] : null;
  productName.textContent = (currentGraph.centre && currentGraph.centre.name) || "";
  productPrice.textContent = priceLine(currentGraph.centre && currentGraph.centre.price);
  productStatus.textContent = "";
  renderBundle(currentSetup);
  // Keep an accessories request made while the product data was loading.
  if (!groups.hidden) renderGroups(currentGraph, currentSetup);
}

function renderGroups(graph, setup) {
  groups.replaceChildren();
  const chosen = new Set(((setup && setup.parts) || []).map((part) => part.product && part.product.id));
  const branches = Array.isArray(graph.branches) ? graph.branches : [];
  if (branches.length === 0) {
    productStatus.textContent = "No approved parts fit this product yet.";
    return;
  }
  productStatus.textContent = "";
  for (const branch of branches) {
    const section = document.createElement("section");
    section.className = "group";
    const heading = document.createElement("h2");
    heading.textContent = branch.role || branch.category || "";
    const list = document.createElement("ul");
    list.className = "fits";
    for (const product of branch.products || []) {
      list.append(fitCard(product, chosen.has(product.id)));
    }
    section.append(heading, list);
    groups.append(section);
  }
}

function fitCard(product, chosen) {
  const item = document.createElement("li");
  item.className = chosen ? "fit chosen" : "fit";
  const name = document.createElement("h3");
  name.textContent = product.name || "";
  const price = document.createElement("p");
  price.className = "price";
  price.textContent = priceLine(product.price);
  item.append(name, price);
  if (product.why) {
    const why = document.createElement("p");
    why.className = "why";
    const label = document.createElement("span");
    label.textContent = "Why it fits";
    why.append(label, document.createTextNode(" " + product.why));
    item.append(why);
  }
  return item;
}

function renderBundle(setup) {
  bundleParts.replaceChildren();
  if (!setup) {
    bundleTotal.textContent = "";
    return;
  }
  const parts = setup.parts || [];
  const centreRole = parts[0] ? parts[0].role : "";
  parts.forEach((part, index) => {
    const item = document.createElement("li");
    const role = document.createElement("p");
    role.className = "role";
    role.textContent = part.role || "";
    const name = document.createElement("p");
    name.className = "part-name";
    name.textContent = (part.product && part.product.name) || "";
    const price = document.createElement("p");
    price.className = "price";
    price.textContent = priceLine(part.product && part.product.price);
    item.append(role, name, price);
    if (index > 0 && part.role !== centreRole) {
      const swap = document.createElement("button");
      swap.type = "button";
      swap.className = "secondary swap";
      swap.textContent = "Swap part";
      swap.addEventListener("click", () => {
        void openSwap(item, part.role);
      });
      item.append(swap);
    }
    bundleParts.append(item);
  });
  bundleTotal.textContent = priceLine(setup.total);
}

async function openSwap(item, role) {
  const existing = item.querySelector(".options");
  if (existing) {
    existing.remove();
    return;
  }
  let response;
  try {
    response = await rigFetch("/v1/rig/swap", {
      method: "POST",
      body: JSON.stringify({ setup: currentSetup, role }),
    });
  } catch {
    return;
  }
  if (!response.ok) return;
  const body = await response.json();
  if (body.setup) {
    currentSetup = body.setup;
    bundleTotal.textContent = priceLine(currentSetup.total);
  }
  const options = document.createElement("ul");
  options.className = "options";
  const choices = Array.isArray(body.options) ? body.options : [];
  if (choices.length === 0) {
    const empty = document.createElement("li");
    empty.textContent = "No other part fits.";
    options.append(empty);
  }
  for (const option of choices) {
    const choice = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "text";
    button.textContent = option.name || "";
    button.addEventListener("click", () => {
      void choosePart(role, option);
    });
    choice.append(button);
    options.append(choice);
  }
  item.append(options);
}

async function choosePart(role, option) {
  if (!currentSetup) return;
  const next = JSON.parse(JSON.stringify(currentSetup));
  for (const part of next.parts || []) {
    if (part.role === role) part.product = { id: option.id, name: option.name };
  }
  let response;
  try {
    response = await rigFetch("/v1/rig/swap", {
      method: "POST",
      body: JSON.stringify({ setup: next, role }),
    });
  } catch {
    return;
  }
  if (!response.ok) return;
  const body = await response.json();
  currentSetup = body.setup || next;
  renderBundle(currentSetup);
  if (currentGraph) renderGroups(currentGraph, currentSetup);
}

async function comparePrices() {
  if (!currentSetup) return;
  const ids = (currentSetup.parts || []).map((part) => part.product && part.product.id).filter(Boolean);
  if (!ids.length) return;
  compareLines.hidden = false;
  compareLines.textContent = "Loading…";
  let response;
  try {
    response = await rigFetch("/v1/rig/compare", {
      method: "POST",
      body: JSON.stringify({ product_ids: ids }),
    });
  } catch {
    compareLines.textContent = "Prices did not load. Try again.";
    return;
  }
  if (!response.ok) {
    compareLines.textContent = "Prices did not load. Try again.";
    return;
  }
  renderCompare(await response.json());
}

function renderCompare(body) {
  compareLines.replaceChildren();
  const label = body.retailer ? body.retailer + " price" : "Price";
  for (const line of body.lines || []) {
    const item = document.createElement("article");
    item.className = "compare-line";
    const name = document.createElement("h3");
    name.textContent = line.name || "";
    const market = document.createElement("p");
    market.className = "market";
    market.textContent =
      line.market && line.market.amount != null
        ? "Market average: " + priceLine(line.market)
        : "Market average: not available";
    const own = document.createElement("p");
    own.className = "own";
    own.textContent = label + ": " + priceLine(line.price);
    item.append(name, market, own);
    compareLines.append(item);
  }
}

function showSearch() {
  productView.hidden = true;
  searchView.hidden = false;
  main.classList.remove("wide");
}

function pathProduct() {
  const match = window.location.pathname.match(/^\/product\/([^/]+)$/);
  return match ? decodeURIComponent(match[1]) : "";
}

function rigFetch(url, options) {
  const headers = {
    Accept: "application/json",
    "X-Rig-Key": window.RIG_KEY || "",
  };
  if (options && options.body) headers["Content-Type"] = "application/json";
  return fetch(url, { method: (options && options.method) || "GET", headers, body: options && options.body });
}

function priceLine(price) {
  if (!price || price.amount == null) return "";
  const value = Number(price.amount);
  const amount = Number.isFinite(value)
    ? value.toLocaleString("en-ZA", { maximumFractionDigits: 2 })
    : String(price.amount);
  return price.currency ? amount + " " + price.currency : amount;
}
