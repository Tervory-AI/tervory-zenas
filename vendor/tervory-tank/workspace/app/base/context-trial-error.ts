// Only these reviewed 409 messages may cross the server-to-browser boundary.
export function contextTrialError(error: unknown): string | null {
  if (!(error instanceof Error)) return null;
  const prefix = "Tank /v1/base/context-dev/trial 409: ";
  if (!error.message.startsWith(prefix)) return null;
  try {
    const { detail } = JSON.parse(error.message.slice(prefix.length));
    if (detail === "No completed batch to test yet. Run a Base batch first." ||
        (typeof detail === "string" && /^A trial is already running \(started (?:[01]\d|2[0-3]):[0-5]\d\)\.$/.test(detail))) {
      return detail;
    }
  } catch { /* Other errors keep the generic response. */ }
  return null;
}
