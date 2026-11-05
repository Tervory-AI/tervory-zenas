// CSV intake for client onboarding. Runs in the browser. Nothing here talks to Tank.

export const MAX_BYTES = 10 * 1024 * 1024;

export const FIELDS = ["name", "address", "city", "state", "zip", "phone", "website"] as const;
export type Field = (typeof FIELDS)[number];

export const FIELD_LABEL: Record<Field, string> = {
  name: "Business name",
  address: "Street address",
  city: "City",
  state: "State",
  zip: "ZIP",
  phone: "Phone",
  website: "Website, optional",
};

const SYNONYMS: Record<Field, string[]> = {
  name: ["name", "business_name", "business", "company", "company_name", "license_name", "licence_name", "licensee", "licensee_name", "organization", "organisation", "trade_name", "dealer"],
  address: ["address", "street", "street_address", "address_1", "address1", "premise_street", "addr", "address_line_1"],
  city: ["city", "town", "premise_city"],
  state: ["state", "st", "province", "premise_state"],
  zip: ["zip", "zipcode", "zip_code", "postal", "postal_code", "postcode", "premise_zip", "premise_zip_code"],
  phone: ["phone", "telephone", "tel", "phone_number", "voice", "voice_phone", "mobile"],
  website: ["website", "url", "site", "web", "homepage", "home_page"],
};

export const MSG = {
  tooBig: "This file is over 10 MB. Use a file under 10 MB.",
  empty: "This file is empty.",
  notCsv: "Choose a CSV file.",
  spreadsheet: "Choose a CSV file. Spreadsheet files need to be saved as CSV first.",
  noHeader: "The first row should be the column names.",
  noRows: "There are no records under the column names.",
  needName: "Choose which column is the business name.",
  needPlace: "Map a column for the street, city, state, or ZIP so we know where the business is.",
  runFailed: "The run did not start. Try again.",
} as const;

export type CostLine = { id: string; title: string; cost: string };

export const COST_LINES: CostLine[] = [
  { id: "1a", title: "Clean names and addresses", cost: "Free" },
  { id: "1b", title: "Match records you already have", cost: "Free" },
  { id: "2a", title: "Find each website", cost: "Free. If our own search is not enough, we use the cheapest paid search available." },
  { id: "2b", title: "Read the website", cost: "Free" },
  { id: "2c", title: "Pick out what the business does", cost: "Free" },
  { id: "3", title: "Find contacts", cost: "Free" },
  { id: "4", title: "Check social pages", cost: "Free" },
  { id: "5", title: "Estimate the size of the business", cost: "Free" },
  { id: "6", title: "Apply the industry profile", cost: "Free" },
  { id: "7", title: "Check each record and score it", cost: "Free" },
];

export const COST_NOTE =
  "Most steps are free. A paid step uses the cheapest option available.";

export type Issue = { level: "error" | "note"; message: string };
export type Table = { headers: string[]; rows: string[][] };
export type Mapping = Record<Field, number | null>;
export type MappedRow = { line: number; values: Record<Field, string>; empty: boolean };

export type StartRequest = { batch: string; csv: string; engine: string; count: number };

export type PassRecord = { status: string; passes?: Record<string, unknown> | null };
export type BatchCounts = { total: number; done: number; failed: number; pending: number };

const PLACE: Field[] = ["address", "city", "state", "zip"];

export function normalizeHeader(value: string): string {
  return value.trim().toLowerCase().replace(/['’.]/g, "").replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
}

export function industryLabel(id: string): string {
  if (id === "ffl") return "Firearms dealers";
  const words = id.replace(/[_-]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : id;
}

export function industryHint(id: string): string | null {
  if (id === "ffl") return "Licensed firearms dealers.";
  return null;
}

export function emptyMapping(): Mapping {
  return { name: null, address: null, city: null, state: null, zip: null, phone: null, website: null };
}

export function fileIssue(file: { name: string; size: number; type?: string }): string | null {
  const name = file.name.toLowerCase();
  if (name.endsWith(".xlsx") || name.endsWith(".xls")) return MSG.spreadsheet;
  const type = file.type ?? "";
  const csvLike = name.endsWith(".csv") || name.endsWith(".txt") || name.endsWith(".tsv") || type === "text/csv" || type === "text/plain" || type === "text/tab-separated-values" || type === "";
  if (!csvLike) return MSG.notCsv;
  if (file.size > MAX_BYTES) return MSG.tooBig;
  if (file.size === 0) return MSG.empty;
  return null;
}

function detectDelim(line: string): string {
  let best = ",";
  let bestN = 1;
  for (const delim of [",", "\t", ";", "|"]) {
    const count = line.split(delim).length;
    if (count > bestN) {
      best = delim;
      bestN = count;
    }
  }
  return best;
}

export function parseCsv(text: string): Table {
  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
  text = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  if (!text.trim()) return { headers: [], rows: [] };
  const delim = detectDelim(text.split("\n", 1)[0] ?? "");
  const grid: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (c === '"') quoted = false;
      else cell += c;
    } else if (c === '"') quoted = true;
    else if (c === delim) {
      row.push(cell);
      cell = "";
    } else if (c === "\n") {
      row.push(cell);
      grid.push(row);
      row = [];
      cell = "";
    } else cell += c;
  }
  if (cell.length || row.length) {
    row.push(cell);
    grid.push(row);
  }
  const headers = (grid.shift() ?? []).map((header) => header.trim());
  let rows = grid;
  if (rows.length > 0 && rows[rows.length - 1].every((value) => !value.trim())) rows = rows.slice(0, -1);
  return { headers, rows };
}

export function autoMap(headers: string[]): Mapping {
  const mapping = emptyMapping();
  const used = new Set<number>();
  for (const field of FIELDS) {
    const synonyms = new Set(SYNONYMS[field]);
    const index = headers.findIndex((header, i) => !used.has(i) && header.trim() && synonyms.has(normalizeHeader(header)));
    if (index >= 0) {
      used.add(index);
      mapping[field] = index;
    }
  }
  return mapping;
}

export function mapRows(table: Table, mapping: Mapping): MappedRow[] {
  return table.rows.map((cells, index) => {
    const values = {} as Record<Field, string>;
    for (const field of FIELDS) {
      const column = mapping[field];
      values[field] = column == null ? "" : (cells[column] ?? "").trim();
    }
    return { line: index + 2, values, empty: cells.every((value) => !value.trim()) };
  });
}

function listLines(lines: number[]): string {
  const shown = lines.slice(0, 5).join(", ");
  return lines.length > 5 ? `${shown}, and ${lines.length - 5} more` : shown;
}

export function issuesFor(table: Table | null, mapping: Mapping | null): Issue[] {
  if (!table || table.headers.every((header) => !header.trim())) return [{ level: "error", message: MSG.noHeader }];
  if (table.rows.length === 0 || table.rows.every((cells) => cells.every((value) => !value.trim()))) {
    return [{ level: "error", message: MSG.noRows }];
  }
  if (!mapping) return [{ level: "error", message: MSG.needName }];
  const issues: Issue[] = [];
  if (mapping.name == null) issues.push({ level: "error", message: MSG.needName });
  if (PLACE.every((field) => mapping[field] == null)) issues.push({ level: "error", message: MSG.needPlace });
  const mapped = mapRows(table, mapping);
  const emptyLines = mapped.filter((row) => row.empty).map((row) => row.line);
  if (emptyLines.length === 1) issues.push({ level: "note", message: `Row ${emptyLines[0]} is empty. Empty rows are not uploaded.` });
  else if (emptyLines.length > 1) issues.push({ level: "note", message: `Rows ${listLines(emptyLines)} are empty. Empty rows are not uploaded.` });
  if (mapping.name != null && PLACE.some((field) => mapping[field] != null)) {
    const nameless: number[] = [];
    const placeless: number[] = [];
    for (const row of mapped) {
      if (row.empty) continue;
      if (!row.values.name) nameless.push(row.line);
      else if (PLACE.every((field) => !row.values[field])) placeless.push(row.line);
    }
    if (nameless.length) issues.push({ level: "error", message: nameless.length === 1 ? `Row ${nameless[0]} has no business name.` : `Rows ${listLines(nameless)} have no business name.` });
    if (placeless.length) issues.push({ level: "error", message: placeless.length === 1 ? `Row ${placeless[0]} has no street, city, state, or ZIP.` : `Rows ${listLines(placeless)} have no street, city, state, or ZIP.` });
  }
  return issues;
}

export function canStart(issues: Issue[]): boolean {
  return issues.every((issue) => issue.level !== "error");
}

function escapeCell(value: string): string {
  if (/[",\n]/.test(value)) return `"${value.replace(/"/g, '""')}"`;
  return value;
}

export function uploadableRows(table: Table, mapping: Mapping): MappedRow[] {
  return mapRows(table, mapping).filter((row) => !row.empty);
}

export function toCsv(table: Table, mapping: Mapping): string {
  const columns: Field[] = ["name", "address", "city", "state", "zip", "phone"];
  if (mapping.website != null) columns.push("website");
  const lines = [columns.join(",")];
  for (const row of uploadableRows(table, mapping)) {
    lines.push(columns.map((field) => escapeCell(row.values[field])).join(","));
  }
  return `${lines.join("\n")}\n`;
}

export function batchName(filename: string): string {
  const stem = filename.replace(/\.[^.]+$/, "").replace(/[^\w .'-]+/g, " ").replace(/\s+/g, " ").trim();
  return (stem || "First batch").slice(0, 80);
}

export function dataPath(batch: string): string {
  return `/data?batch=${encodeURIComponent(batch)}`;
}

export function plainRunError(raw: unknown): string {
  const text = typeof raw === "string" ? raw.trim() : "";
  if (!text || text.length > 240 || /tank|litellm|api[_-]?key|postgres:\/\//i.test(text)) return MSG.runFailed;
  return text;
}

export function passState(records: PassRecord[], id: string): { state: "waiting" | "working" | "done" | "skipped"; have: number; total: number } {
  const total = records.length;
  if (total === 0) return { state: "waiting", have: 0, total: 0 };
  const have = records.filter((record) => record.passes != null && Object.prototype.hasOwnProperty.call(record.passes, id)).length;
  const finished = records.every((record) => record.status === "done" || record.status === "failed");
  if (have === 0 && finished) return { state: "skipped", have: 0, total };
  if (have === 0) return { state: "waiting", have: 0, total };
  if (!finished && have < total) return { state: "working", have, total };
  return { state: "done", have, total };
}

export function describePasses(records: PassRecord[]): { id: string; label: string; state: string; detail: string }[] {
  return COST_LINES.map((line) => {
    const view = passState(records, line.id);
    const detail = view.state === "waiting" ? "Waiting" : view.state === "skipped" ? "Not needed" : view.state === "working" ? `${view.have} of ${view.total}` : "Done";
    return { id: line.id, label: line.title, state: view.state, detail };
  });
}

export function progressLine(summary: BatchCounts | null, records: PassRecord[]): string {
  const total = summary?.total ?? records.length;
  const done = summary?.done ?? records.filter((record) => record.status === "done").length;
  const pending = summary?.pending ?? records.filter((record) => record.status === "queued" || record.status === "running").length;
  const failed = summary?.failed ?? records.filter((record) => record.status === "failed").length;
  const parts = [`${total} records`, `${done} enriched`];
  if (pending) parts.push(`${pending} still going`);
  if (failed) parts.push(`${failed} failed`);
  return parts.join(" · ");
}
