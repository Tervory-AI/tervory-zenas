/** Moot Court text edition (MOOT-1a). Training only — nothing here is a filed matter. */

export const MOOT_LABEL = "Moot Court";

export const MOOT_LEAD = "This is where Zenas trains in text.";

/** Students come later. This page has no sign-up. */
export const MOOT_LATER = "Law students will use it later, for free.";

export const MOOT_PARTS = [
  { id: "problem", title: "The problem", empty: "Load a locked civil appeal problem to begin." },
  {
    id: "hearing",
    title: "The hearing",
    empty: "AI against AI, student against AI, or students against students — all in text.",
  },
  { id: "ruling", title: "The ruling", empty: "Order, reasons, fairness check, then the reveal." },
] as const;

export type MootPartId = (typeof MOOT_PARTS)[number]["id"];

export type TrainingRound = Record<MootPartId, string>;

/** No match is selected on the signed-in panel yet (watch pages live under /moot). */
export function trainingRound(): TrainingRound | null {
  return null;
}
