/** The first worked example locked in zenas.deadlines 1.0.0.
 *  tests/test_zenas_deadlines.py and evals/cases.jsonl both expect these dates.
 *  This is not a client matter.
 */

export const WORKED_EXAMPLE = {
  name: "fourteen days under FRCP 6 lands on a Friday",
  label: "Worked example",
  title: "Fourteen days under FRCP 6",
  jurisdiction: "us",
  jurisdictionLabel: "United States",
  trigger: "2026-01-02",
  days: 14,
  deadline: "2026-01-16",
  reading: "16 January 2026. A Friday. The last day counts.",
  note: "This date is a worked example from the deadline tests. It is not a client matter.",
  steps: [
    {
      rule: "FRCP 6(a)(1)(A)",
      text: "The trigger day 2026-01-02 is excluded. Counting starts 2026-01-03.",
    },
    {
      rule: "FRCP 6(a)(1)(B)",
      text: "Counted 14 calendar days, including intermediate Saturdays, Sundays, and legal holidays, through 2026-01-16.",
    },
    {
      rule: "FRCP 6(a)(1)(C)",
      text: "The last day 2026-01-16 is a weekday and not a legal holiday, so it is included.",
    },
  ],
} as const;
