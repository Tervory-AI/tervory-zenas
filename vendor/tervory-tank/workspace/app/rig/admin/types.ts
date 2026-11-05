export type FitReview = { part_sku: string; part_name: string; fits_sku: string | null; fits_family: string | null; fits_with: string; note: string; confidence: number | null; rule?: Record<string, unknown> };
export type FeedReceipt = { id: string; provider: string; site: string; feed_id?: string; added: number; updated: number; rejected: number; created_at: string };
export type RetailerPanel = {
  settings?: { name: string; email: string };
  price_configured: boolean; currency: string; products: number; can_set_exchange_rate: boolean;
  product_rows?: { sku: string; name: string; price: string; currency: string }[];
  feeds: { provider: string; site: string; next_refresh: string | null; feed_id?: string; mapping_confirmed?: boolean; currency?: string; country?: string; price_basis?: string; format?: string; records_path?: string; columns?: Record<string, string> }[];
  templates: Record<string, Record<string, string>>;
  fits: FitReview[]; missed_searches: { query: string; count: number }[]; receipts: FeedReceipt[];
};
export type MappingPreview = { preview_id?: string; fields: string[]; columns: Record<string, string>; required: string[] };
export type AdminResult = { commerce?: CommerceResult; ok: boolean; message: string; deleted?: number; panel?: RetailerPanel; mapping?: MappingPreview; embed?: { publishable_key: string; snippet: string; origins: string[] } };
export type AdminOperation = "commerce-connect" | "commerce-orders" | "store-delivery" | "connect" | "import" | "delete-products" | "refresh" | "mapping" | "confirm-mapping" | "settings" | "embed-keys" | "price-rule" | "exchange-rate" | "templates" | "approve" | "reject" | "suggest";

export type CartLine = { id: string; product_id: string; title: string; quantity: number; price: string; currency: string; kind: "stock" | "locate" | "back_order"; delivery: { min_days?: number; max_days?: number; ship_days?: number; eta?: string; status?: string } };
export type CartOrder = { id: string; state: string; expires_at: string; payment_confirmed: boolean; number?: string; total?: string; currency?: string; late_payment?: boolean; lines: CartLine[] };
export type CommerceResult = { saved?: boolean; platform?: string; site?: string; store_hash?: string; delivery?: { min_days?: number; max_days?: number; ship_days?: number }; orders?: CartOrder[] };
