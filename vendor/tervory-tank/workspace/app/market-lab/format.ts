const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Calendar date from a YYYY-MM-DD prefix. No timezone shift. */
export function formatDay(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!match) return iso;
  const month = MONTHS[Number(match[2]) - 1];
  if (!month) return iso;
  return `${Number(match[3])} ${month} ${match[1]}`;
}

export function formatMonth(iso: string): string {
  const match = /^(\d{4})-(\d{2})/.exec(iso);
  if (!match) return iso;
  const month = MONTHS[Number(match[2]) - 1];
  if (!month) return iso;
  return `${month} ${match[1]}`;
}

/** Clock time in Pacific, the zone the design uses. */
export function formatRefreshed(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Not refreshed yet";
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: "America/Los_Angeles",
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    }).formatToParts(date).map((part) => [part.type, part.value]),
  );
  const minute = parts.minute ?? "00";
  const ampm = (parts.dayPeriod ?? "").toUpperCase();
  return `${parts.day} ${parts.month} ${parts.year}, ${parts.hour}:${minute} ${ampm} PT`;
}
