import type { TrainingGraduation } from "@/lib/tank";
import { graduationReview, type GraduationMark } from "./graduation-copy";
import { promoteTraining } from "./graduation-actions";

type GraduationRow = TrainingGraduation & GraduationMark & {
  heldout_by_region?: Record<string, number | null>;
};

const cost = (n: number | null) => n === null ? "Not measured" : `$${Number(n).toFixed(6)}`;

function reviewCell(row: GraduationRow) {
  const label = graduationReview(row);
  if (label !== "Promote") return label;
  return <form action={promoteTraining}>
    <input type="hidden" name="task" value={row.task_type}/><button type="submit">Promote</button>
  </form>;
}

export function GraduationPanel({rows}: {rows: GraduationRow[] | null}) {
  return <section className="tank-graduation" aria-labelledby="graduation-title">
    <h2 id="graduation-title">Graduation</h2>
    <p><a href="/tank/proof">View cost and quality proof</a></p>
    <p>100 checked examples, then a held-out check: code first, a cheaper roster model next, then our own model. Promotion requires your click and passing live checks.</p>
    {rows === null ? <p>Graduation evidence is unavailable.</p> : <div className="tank-graduation-scroll"><table>
      <thead><tr>{["Task", "Current model", "Gold / 100", "Best candidate", "Held-out score", "Now / call", "Candidate / call", "Review"].map(h => <th key={h}>{h}</th>)}</tr></thead>
      <tbody>{rows.map(row => <tr key={row.task_type}><td>{row.task_type}</td><td>{row.model}</td><td>{row.gold_count} / 100</td>
        <td>{row.candidate ?? "No proven candidate"}</td><td>{row.heldout_score === null ? "Not tested" : `${(row.heldout_score * 100).toFixed(1)}%`}</td>
        <td>{row.current_cost === null ? "no calls yet" : cost(row.current_cost)}</td><td>{cost(row.candidate_cost)}</td><td>{reviewCell(row)}</td></tr>)}</tbody>
    </table></div>}
  </section>;
}
