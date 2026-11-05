const KEY = "rig.adminKey";
const form = document.querySelector("#key-form");
const input = document.querySelector("#admin-key");
const review = document.querySelector("#review");
const status = document.querySelector("#admin-status");
const empty = document.querySelector("#empty");
const rows = document.querySelector("#rows");
const tableWrap = document.querySelector(".table-wrap");
const suggest = document.querySelector("#suggest");

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const key = input.value.trim();
  if (!key) {
    status.textContent = "Enter an admin key.";
    return;
  }
  sessionStorage.setItem(KEY, key);
  void loadFits();
});

suggest.addEventListener("click", () => {
  void suggestFits();
});

document.querySelector("#change-key").addEventListener("click", () => {
  sessionStorage.removeItem(KEY);
  input.value = "";
  review.hidden = true;
  form.hidden = false;
  status.textContent = "";
});

if (sessionStorage.getItem(KEY)) void loadFits();

async function loadFits() {
  const key = sessionStorage.getItem(KEY) || "";
  if (!key) return;
  form.hidden = true;
  review.hidden = false;
  status.textContent = "Loading…";
  let response;
  try {
    response = await adminFetch("/v1/rig/admin/fits?status=suggested");
  } catch {
    status.textContent = "The list did not load. Try again.";
    return;
  }
  if (response.status === 401 || response.status === 403) {
    sessionStorage.removeItem(KEY);
    form.hidden = false;
    review.hidden = true;
    input.value = "";
    status.textContent = "That key is not valid.";
    return;
  }
  if (!response.ok) {
    status.textContent = "The list did not load. Try again.";
    return;
  }
  const body = await response.json();
  paint(Array.isArray(body.fits) ? body.fits : []);
}

function paint(fits) {
  rows.replaceChildren();
  const waiting = fits.length === 0;
  empty.hidden = !waiting;
  tableWrap.hidden = waiting;
  status.textContent = waiting ? "" : fits.length === 1 ? "1 fit to review" : fits.length + " fits to review";
  for (const fit of fits) rows.append(fitRow(fit));
}

function fitRow(fit) {
  const row = document.createElement("tr");
  row.append(
    cell("Part", fit.part_name || fit.part_sku || ""),
    cell("Fits with", fit.fits_with || ""),
    cell("Confidence", fit.confidence == null ? "" : String(fit.confidence), true),
    cell("Note", fit.note || ""),
    actions(fit),
  );
  return row;
}

function cell(label, text, mono) {
  const td = document.createElement("td");
  td.dataset.label = label;
  if (mono) td.className = "mono";
  td.textContent = text;
  return td;
}

function actions(fit) {
  const td = document.createElement("td");
  td.dataset.label = "Actions";
  const approve = document.createElement("button");
  approve.type = "button";
  approve.className = "approve";
  approve.textContent = "Approve";
  approve.addEventListener("click", () => {
    void decide(fit, "approve", approve);
  });
  const reject = document.createElement("button");
  reject.type = "button";
  reject.className = "secondary";
  reject.textContent = "Reject";
  reject.addEventListener("click", () => {
    void decide(fit, "reject", reject);
  });
  td.append(approve, reject);
  return td;
}

async function decide(fit, action, button) {
  button.disabled = true;
  let response;
  try {
    response = await adminFetch("/v1/rig/admin/fits/" + action, {
      method: "POST",
      body: JSON.stringify(target(fit)),
    });
  } catch {
    button.disabled = false;
    status.textContent = "That did not save. Try again.";
    return;
  }
  if (!response.ok) {
    button.disabled = false;
    status.textContent = "That did not save. Try again.";
    return;
  }
  status.textContent = action === "approve" ? "Approved." : "Rejected.";
  const row = button.closest("tr");
  if (row) row.remove();
  const left = rows.children.length;
  empty.hidden = left !== 0;
  tableWrap.hidden = left === 0;
}

async function suggestFits() {
  suggest.disabled = true;
  status.textContent = "Looking for fits…";
  let response;
  try {
    response = await adminFetch("/v1/rig/admin/fits/suggest", { method: "POST" });
  } catch {
    suggest.disabled = false;
    status.textContent = "Suggestions did not run. Try again.";
    return;
  }
  suggest.disabled = false;
  if (!response.ok) {
    status.textContent = "Suggestions did not run. Try again.";
    return;
  }
  await loadFits();
}

function target(fit) {
  const body = { part_sku: fit.part_sku };
  if (fit.fits_sku) body.fits_sku = fit.fits_sku;
  else body.fits_family = fit.fits_family;
  return body;
}

function adminFetch(url, options) {
  const headers = {
    Accept: "application/json",
    "X-Rig-Key": sessionStorage.getItem(KEY) || "",
  };
  if (options && options.body) headers["Content-Type"] = "application/json";
  return fetch(url, {
    method: (options && options.method) || "GET",
    headers,
    body: options && options.body,
  });
}
