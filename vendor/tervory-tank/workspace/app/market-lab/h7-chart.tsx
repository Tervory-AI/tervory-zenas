import type { Curve } from "./h7-model";
import { percent, plotBins } from "./h7-model";

const root = "/market-lab/h7/";
/** Figma's exact chart assets; only the empirical dot positions come from data. */
export function CalibrationChart({ row }: { row: Curve | null }) {
  const points = row ? plotBins(row) : [];
  return <figure className="h7-chart"><figcaption>Prediction confidence vs actual hit rate</figcaption>
    {([false, true] as const).map(mobile => {
      const mode = mobile ? "mobile" : "desktop", left = mobile ? 64 : 72, width = mobile ? 207 : 400;
      const step = width / 6;
      return <div className={`h7-plot ${mode}`} key={mode} aria-label={row ? `${row.kind} / ${row.feed}` : "Prediction confidence vs actual hit rate"}>
        {[100,75,50,25,0].map((label,index) => <span key={label}>
          <img src={`${root}${mode}-imgGrid.svg`} alt="" style={{position:"absolute",left,top:7.5+index*36}}/>
          <span className="h7-y-label" style={{left:mobile?0:0,top:index*36,width:mobile?56:64}}>{label}%</span>
        </span>)}
        <img src={`${root}${mode}-imgAxis.svg`} alt="" style={{position:"absolute",left:left-1,top:8}}/>
        <img src={`${root}${mode}-imgAxis1.svg`} alt="" style={{position:"absolute",left,top:151}}/>
        {[0,1,2,3,4,5].map(index => <img key={index} src={`${root}${mode}-imgConfidence.svg`} alt=""
          style={{position:"absolute",left:left+index*step-(mobile ? .571 : .339),top:128-index*24-(mobile ? .821 : .941)}}/>)}
        {[0,50,100].map(label => <span className="h7-x-label" key={label}
          style={{left:left+label/100*width,top:160,width:"max-content",transform:label===0?"none":label===50?"translateX(-50%)":"translateX(-100%)"}}>{label}%</span>)}
        {points.map((point,index) => <img key={index} data-confidence={point.confidence} data-actual={point.actual}
          src={`${root}${mode}-dot.svg`} alt=""
          title={`${percent(point.confidence)} / ${percent(point.actual)} · ${point.count}`}
          style={{position:"absolute",left:left+point.confidence*width-4,top:152-point.actual*144-4}}/>)}
      </div>;
    })}
    <p>Confidence {percent(row?.mean_confidence)} / Actual hit rate {percent(row?.realised_rate)}</p>
    <ul className="h7-sr">{points.map((point,index) => <li key={index}>Confidence: {percent(point.confidence)} · Actual hit rate: {percent(point.actual)} · Samples: {point.count}</li>)}</ul>
  </figure>;
}
