import { shortDay } from "./format";

type Point = { ts: string; price: number };

export function PriceChart({ points }: { points: Point[] }) {
  if (points.length === 0) {
    return <p className="empty">No prices yet. The first check has not come in.</p>;
  }

  const width = 640;
  const height = 168;
  const pad = { left: 56, right: 12, top: 16, bottom: 28 };
  const min = Math.min(...points.map((point) => point.price));
  const max = Math.max(...points.map((point) => point.price));
  const span = max - min || Math.max(Math.abs(max) * 0.08, 1);
  const y0 = min - span * 0.15;
  const y1 = max + span * 0.15;
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const xAt = (index: number) => pad.left + (points.length === 1 ? innerW / 2 : (index / (points.length - 1)) * innerW);
  const yAt = (price: number) => pad.top + ((y1 - price) / (y1 - y0)) * innerH;
  const path = points.map((point, index) => `${index === 0 ? "M" : "L"}${xAt(index).toFixed(1)},${yAt(point.price).toFixed(1)}`).join(" ");
  const last = points.length - 1;

  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Price history" className="chart">
        <title>Price history</title>
        <line x1={pad.left} y1={pad.top} x2={pad.left} y2={height - pad.bottom} />
        <line x1={pad.left} y1={height - pad.bottom} x2={width - pad.right} y2={height - pad.bottom} />
        <text x={pad.left - 8} y={yAt(max) + 4} textAnchor="end">{max.toFixed(2)}</text>
        {min !== max && <text x={pad.left - 8} y={yAt(min) + 4} textAnchor="end">{min.toFixed(2)}</text>}
        <text x={xAt(0)} y={height - 8} textAnchor="start">{shortDay(points[0].ts)}</text>
        {points.length > 1 && <text x={xAt(last)} y={height - 8} textAnchor="end">{shortDay(points[last].ts)}</text>}
        <path d={path} fill="none" />
        <circle cx={xAt(last)} cy={yAt(points[last].price)} r="4.5" />
      </svg>
      <details>
        <summary>Price numbers</summary>
        <table>
          <thead>
            <tr><th>When</th><th>Price</th></tr>
          </thead>
          <tbody>
            {[...points].reverse().map((point, index) => (
              <tr key={`${point.ts}-${index}`}>
                <td className="num">{shortDay(point.ts)}</td>
                <td className="num">{point.price.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
