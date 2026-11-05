"use client";

import { useEffect, useRef, useState } from "react";
import type { RetailerPanel } from "./types";

export function Products({ rows, busy, remove }: {
  rows: NonNullable<RetailerPanel["product_rows"]>;
  busy: boolean;
  remove: (skus: string[]) => Promise<boolean>;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const all = useRef<HTMLInputElement>(null);
  const current = rows.filter(row => selected.has(row.sku));
  useEffect(() => { if (all.current) all.current.indeterminate = current.length > 0 && current.length < rows.length; }, [current.length, rows.length]);
  const confirm = async (skus: string[]) => {
    if (busy || !skus.length || !window.confirm(`Delete ${skus.length} ${skus.length === 1 ? "product" : "products"}? This removes them from your catalogue and search. It can't be undone.`)) return;
    if (await remove(skus)) setSelected(new Set());
  };
  return <section className="retailer-card retailer-wide retailer-products" aria-labelledby="rig-products-heading">
    <h2 id="rig-products-heading">Products</h2>
    <div className="retailer-product-controls">
      <input ref={all} type="checkbox" aria-label="Products" disabled={busy || !rows.length}
        checked={rows.length > 0 && current.length === rows.length}
        onChange={event => setSelected(new Set(event.target.checked ? rows.map(row => row.sku) : []))} />
      <button type="button" disabled={busy || !current.length} onClick={() => confirm(current.map(row => row.sku))}>Delete selected</button>
    </div>
    <div className="retailer-product-scroll"><table>
      <thead><tr><th /><th scope="col">Product code</th><th scope="col">Product name</th><th scope="col">Price</th><th /></tr></thead>
      <tbody>{rows.map(row => <tr key={row.sku}>
        <td><input type="checkbox" aria-label={row.sku} disabled={busy} checked={selected.has(row.sku)} onChange={event => setSelected(previous => {
          const next = new Set(previous); if (event.target.checked) next.add(row.sku); else next.delete(row.sku); return next;
        })} /></td>
        <td>{row.sku}</td><td>{row.name}</td>
        <td>{new Intl.NumberFormat("en", { style: "currency", currency: row.currency }).format(Number(row.price))}</td>
        <td><button type="button" disabled={busy} onClick={() => confirm([row.sku])}>Delete</button></td>
      </tr>)}</tbody>
    </table></div>
  </section>;
}
