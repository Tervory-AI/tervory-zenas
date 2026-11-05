import assert from "node:assert/strict";
import { test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { BriefView, CalibrationView } from "./h7-view";
import { percent, plotBins, type Brief, type Curve } from "./h7-model";
import { loadBrief, loadCalibration } from "./h7-load";
const identity = { tenantId: "00000000-0000-0000-0000-000000000031", userId: "placeholder-user" };
export const brief: Brief = { report_id: "00000000-0000-0000-0000-000000000041", name: "placeholder saved brief", summary: "placeholder saved claim", made_at: "2026-11-04T00:00:00Z", status: "email:sent", deliveries: [{channel:"email",status:"sent"}],
  sources: [{claim:"placeholder saved claim",url:"https://placeholder.example/source",stored_href:"/market-lab/sources/00000000-0000-0000-0000-000000000051?row=0#passage-0"}], signals:[], history:[] };
export const curve: Curve = { kind:"placeholder_pair",feed:"placeholder_feed",horizon_seconds:2592000,count:2,mean_confidence:.45,realised_rate:.5,hits:1,misses:1,
  bins:[{lower:0,upper:.1,count:1,mean_confidence:.1,realised_rate:0},{lower:.8,upper:.9,count:1,mean_confidence:.8,realised_rate:1}] };

test("brief detail uses the tenant authenticated existing read and saved text", async () => {
  const fetch = globalThis.fetch;
  try {
    globalThis.fetch = async (input, options) => {
      const url = new URL(String(input));
      assert.equal(url.pathname, "/v1/market-lab/reads/briefs");
      assert.equal(url.searchParams.get("report_id"), brief.report_id);
      assert.equal(url.searchParams.has("tenant_id"), false);
      const headers = new Headers(options?.headers);
      assert.equal(headers.get("x-tervory-tenant"), identity.tenantId);
      assert.equal(headers.get("x-tervory-user"), identity.userId);
      assert.equal(options?.cache, "no-store");
      return Response.json({records:[brief]});
    };
    const result = await loadBrief(identity, brief.report_id);
    assert.equal(result.state,"ready");
    const html = renderToStaticMarkup(<BriefView value={result}/>);
    assert.ok(html.includes(brief.summary));
    assert.ok(html.includes(brief.sources[0].stored_href!));
    assert.ok(!html.includes("[Saved answer"));
  } finally { globalThis.fetch = fetch; }
});

test("read errors never become a measured zero or leaked transport error", async () => {
  const fetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response("placeholder private error",{status:404});
    assert.deepEqual(await loadBrief(identity, brief.report_id),{state:"empty"});
    globalThis.fetch = async () => new Response("placeholder private error",{status:503});
    assert.deepEqual(await loadCalibration(identity),{state:"error"});
    for(const state of ["loading","empty","error"] as const) {
      const html=renderToStaticMarkup(<CalibrationView value={{state}}/>);
      assert.ok(!html.includes("0.0%")); assert.ok(!html.includes("placeholder private error"));
      const detail=renderToStaticMarkup(<BriefView value={{state}}/>);
      assert.ok(!detail.includes(brief.summary));
    }
  } finally {globalThis.fetch=fetch;}
});

test("calibration reads the same curve with separate horizons and honest unknowns", async () => {
  const fetch=globalThis.fetch;
  try {
    globalThis.fetch=async input=>{
      assert.equal(new URL(String(input)).pathname,"/v1/market-lab/reads/calibration.curve");
      return Response.json({records:[curve,{...curve,horizon_seconds:604800}]});
    };
    const value=await loadCalibration(identity);
    const html=renderToStaticMarkup(<CalibrationView value={value}/>);
    assert.ok(html.includes("30 days") && html.includes("7 days"));
    assert.ok(html.includes("45.0%") && html.includes("50.0%"));
    assert.equal(percent(null),"—");
    assert.deepEqual(plotBins(curve),[{confidence:.1,actual:0,count:1},{confidence:.8,actual:1,count:1}]);
  } finally {globalThis.fetch=fetch;}
});


test("calibration follows existing pagination instead of omitting later cohorts", async () => {
  const fetch=globalThis.fetch;
  const pages: number[]=[];
  try {
    globalThis.fetch=async input=>{
      const page=Number(new URL(String(input)).searchParams.get("page")??0);
      pages.push(page);
      return Response.json({records:page===0?Array.from({length:100},(_,i)=>({...curve,kind:`placeholder_${i}`})):[{...curve,kind:"placeholder_last"}],total:101});
    };
    const value=await loadCalibration(identity);
    assert.equal(value.state,"ready");
    if(value.state==="ready") {assert.equal(value.data.length,101);assert.equal(value.data[100].kind,"placeholder_last");}
    assert.deepEqual(pages,[0,1]);
  } finally {globalThis.fetch=fetch;}
});
