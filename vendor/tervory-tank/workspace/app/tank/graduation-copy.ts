export type GraduationMark = {
  can_promote: boolean;
  trained_on_removed?: boolean;
  blocked_reason?: string | null;
};

export function graduationReview(row: GraduationMark): string {
  if (row.trained_on_removed || row.blocked_reason === "trained on removed data") {
    return "trained on removed data";
  }
  if (row.can_promote) return "Promote";
  if (row.blocked_reason === "dropped") return "Blocked: one region scored lower";
  return "More evidence needed";
}
