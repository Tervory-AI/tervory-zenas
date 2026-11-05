import { rigProxy } from "@/lib/tank";

const SEARCH = "/v1/rig/search";
const ASK = "/v1/rig/ask";
const GRAPH = "/v1/rig/graph";
const COMPARE = "/v1/rig/compare";
const PRODUCT = "/v1/rig/products";
const ID = /^[A-Za-z0-9._-]{1,64}$/;

type SearchHit = { id?: string; name?: string; price?: { amount?: number; currency?: string } };

function asId(value: unknown): string {
  const id = String(value ?? "");
  if (!ID.test(id)) throw new Error("bad id");
  return id;
}

function money(price: unknown): string {
  if (!price || typeof price !== "object" || !("amount" in price)) return "";
  const amount = Number((price as { amount?: number }).amount);
  if (!Number.isFinite(amount)) return "";
  return `R${Math.round(amount).toLocaleString("en-US")}`;
}

/** Read-only calls to the Rig room through Tank. Search, graph, compare, and ask. No writes and no local files. */
export async function rigCall(tenantId: string, op: string, payload: Record<string, unknown>): Promise<unknown> {
  if (!tenantId) throw new Error("down");
  if (op === "search") {
    const q = String(payload.q ?? "").trim().slice(0, 200);
    if (!q) return { results: [] };
    return rigProxy(SEARCH, tenantId, { method: "GET", query: { q, limit: "12" } });
  }
  if (op === "graph") {
    return rigProxy(`${GRAPH}/${encodeURIComponent(asId(payload.id))}`, tenantId, { method: "GET" });
  }
  if (op === "compare") {
    const ids = Array.isArray(payload.product_ids) ? payload.product_ids.slice(0, 24).map(asId) : [];
    if (!ids.length) throw new Error("bad id");
    return rigProxy(COMPARE, tenantId, { method: "POST", body: { product_ids: ids } });
  }
  if (op === "ask") {
    const question = String(payload.question ?? "").trim().slice(0, 500);
    if (!question) return { answer: "I don't know", items: [], sources: [] };
    const found = await rigProxy(SEARCH, tenantId, { method: "GET", query: { q: question, limit: "5" } });
    const results = Array.isArray(found.results) ? (found.results as SearchHit[]) : [];
    if (!results.length || found.exact_match === false) {
      return { answer: "I don't know", items: [], sources: [] };
    }
    const productId = String(results[0]?.id ?? "");
    if (!ID.test(productId)) return { answer: "I don't know", items: [], sources: [] };
    const asked = await rigProxy(ASK, tenantId, { method: "POST", body: { product_id: productId, question } });
    const answer = String(asked.answer ?? "I don't know");
    const cites = Array.isArray(asked.cites) ? asked.cites.map(String).filter((id) => ID.test(id)) : [];
    if (answer === "I don't know" || !cites.length) {
      return { answer: "I don't know", items: [], sources: [] };
    }
    const sources = [];
    const items = [];
    for (const id of cites) {
      let name = id;
      let price = "";
      try {
        const product = await rigProxy(`${PRODUCT}/${encodeURIComponent(id)}`, tenantId, { method: "GET" });
        name = String(product.name ?? id);
        price = money(product.price);
      } catch {
        name = id;
      }
      items.push({ name, price });
      sources.push({
        title: price ? `${name} · ${price}` : name,
        detail: `Catalogue · product ${id} · listed in this catalogue`,
      });
    }
    return { answer, items, sources };
  }
  throw new Error("unsupported");
}
