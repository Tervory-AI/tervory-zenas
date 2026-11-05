/** Bounded CSV intake and spreadsheet-safe output. No network or model calls. */
export const MAX_CSV_BYTES = 500_000;
export const MAX_CSV_ROWS = 1000;

export function parseBusinessCsv(text: string): Record<string, string>[] {
  text = text.replace(/^\uFEFF/, "");
  if (!text.trim()) throw new Error("Choose a CSV with a header and at least one business.");
  const rows: string[][] = [];
  let row: string[] = [], cell = "", quoted = false, closed = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') { quoted = false; closed = true; }
      else cell += c;
    } else if (c === '"' && !cell && !closed) quoted = true;
    else if (c === "," || c === "\n" || c === "\r") {
      row.push(cell.trim()); cell = ""; closed = false;
      if (c !== ",") {
        if (c === "\r" && text[i + 1] === "\n") i++;
        if (row.some(Boolean)) rows.push(row);
        row = [];
      }
    } else {
      if (closed && c.trim() || c === '"') throw new Error("The CSV has a misplaced quote. Save it as comma-separated CSV and try again.");
      cell += c;
    }
    if (rows.length > MAX_CSV_ROWS + 1 || row.length > 50 || cell.length > 10_000) throw new Error("Use up to 1,000 businesses, 50 columns and 10,000 characters per cell.");
  }
  if (quoted) throw new Error("The CSV has an unclosed quote.");
  row.push(cell.trim());
  if (row.some(Boolean)) rows.push(row);
  const headers = (rows.shift() ?? []).map((value) => value.toLowerCase().replace(/\s+/g, "_"));
  if (!headers.includes("name")) throw new Error("The CSV needs a name column. You can also include website, phone, address, city, state and zip.");
  if (headers.some((name) => !name || name === "prototype" || Object.hasOwn(Object.prototype, name)) || new Set(headers).size !== headers.length) throw new Error("Use a unique, non-empty heading for each column.");
  if (!rows.length || rows.length > MAX_CSV_ROWS || headers.length > 50) throw new Error("Include between 1 and 1,000 businesses, with at most 50 columns.");
  return rows.map((values, index) => {
    if (values.length !== headers.length) throw new Error(`Row ${index + 2} does not match the header's column count.`);
    const record = Object.fromEntries(headers.map((name, i) => [name, values[i]]));
    if (!record.name) throw new Error(`Row ${index + 2} needs a business name.`);
    return record;
  });
}

export function csvCell(value: unknown): string {
  let text = Array.isArray(value) ? value.map(String).join("; ") : String(value ?? "");
  // CSV quoting alone does not stop a spreadsheet from executing a formula.
  if ((/^[\s\uFEFF]*[=+@-]/.test(text) || /^[\t\r\n]/.test(text)) && !/^\+?[1-9][0-9]{6,14}$/.test(text)) text = "'" + text;
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** Additive evidence/export schema; these fields await Base page Figma approval. */
export const CONTACT_QUALITY_COLUMNS = [
  "phone_e164", "phone_valid", "phone_line_type", "email_syntax_ok", "email_disposable",
] as const;
