/** Labels for the Tiger page. Sample rows are not shown. */

export const LEDE =
  "Track product prices and stock availability across your watched stores.";
export const FOOT =
  "Scheduled checks of public product pages. Alerts for confirmed price and stock changes.";

export type ReadStatus = "Not checked" | "Read" | "Price changed" | "Stock changed";

export const EVERY_OPTIONS = [
  { value: "15m", label: "Every 15 minutes" },
  { value: "1h", label: "Every hour" },
  { value: "6h", label: "Every 6 hours" },
  { value: "12h", label: "Every 12 hours" },
  { value: "1d", label: "Every day" },
] as const;
