/** Approve with a matter id goes through Tank matters/{id}/release. */
export function usesMatterRelease(decision: string, matterId: string): boolean {
  return decision === "approved" && Boolean(matterId.trim());
}
