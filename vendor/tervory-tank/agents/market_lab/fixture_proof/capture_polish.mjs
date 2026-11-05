// Source-page proof using real Workspace components, Tank and local Postgres.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {chromium} from 'playwright';
const {values}=parseArgs({options:{browser:{type:'string'},base:{type:'string',default:'http://127.0.0.1:3046'},output:{type:'string'}}});
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../..');
const base=new URL(values.base);
assert(['127.0.0.1','localhost'].includes(base.hostname));
const output=values.output || path.join(root,'agents/market_lab/evidence/polish');
await fs.mkdir(output,{recursive:true});
const report={label:'local, fixture page',question:'What is the latest container volume?',widths:[],screenshots:[],browserErrors:[],externalRequests:[]};
const browser=await chromium.launch({executablePath:values.browser,headless:true,args:['--no-sandbox']});
try {
  for(const width of [1440,375]) {
    const context=await browser.newContext({viewport:{width,height:1000}});
    await context.route('**/*',route=>{
      const url=new URL(route.request().url());
      if(['http:','https:'].includes(url.protocol)&&url.origin!==base.origin){report.externalRequests.push(url.hostname);return route.abort();}
      return route.continue();
    });
    const page=await context.newPage();
    page.on('pageerror',error=>report.browserErrors.push(error.message));
    async function screenshot(name) {
      assert(await page.getByText('local, fixture page',{exact:true}).isVisible());
      const file=`${name}-${width}.png`;
      await page.screenshot({path:path.join(output,file)});
      report.screenshots.push(file);
    }
    await page.goto(new URL('/market-lab',base).href);
    await page.getByRole('textbox',{name:'Ask Market Lab',exact:true}).fill(report.question);
    const answer=page.waitForResponse(response=>response.url()===new URL('/market-lab/ask',base).href&&response.request().method()==='POST');
    await page.getByRole('button',{name:'Send',exact:true}).click();
    const response=await answer;
    assert.equal(response.status(),200);
    const brief=await response.json();
    assert.equal(brief.facts.length,1);
    assert(brief.facts[0].text.includes('14,474'));
    report.fact=brief.facts[0];
    const citation=page.locator('.ml-answer .ml-lines a');
    await citation.waitFor();
    await page.locator('.ml-chat').evaluate(node=>node.scrollIntoView({block:'start'}));
    await screenshot('brief');
    await citation.click();
    const sourcePath=new URL(page.url()).pathname;
    // Default source opening must start with the newest period, without a citation query.
    await page.goto(new URL(sourcePath,base).href);
    assert.equal(await page.title(),'Market Lab · Tervory');
    const firstIds=await page.locator('tbody tr').evaluateAll(rows=>rows.map(row=>row.id));
    assert.equal(firstIds.length,50);
    assert.equal(await page.locator('tbody tr').first().locator('td').nth(1).innerText(),'Jan 2026');
    assert.equal(await page.locator('tbody tr').first().locator('.ml-source-value').innerText(),'14,474');
    assert.equal(await page.locator('tbody tr').last().locator('td').nth(1).innerText(),'Jan 1977');
    assert.equal(await page.getByRole('link',{name:'Previous',exact:true}).count(),0);
    await page.getByText('Page 1 of 2',{exact:true}).waitFor();
    await screenshot('source-page-1');
    await page.getByRole('link',{name:'Next',exact:true}).click();
    await page.getByText('Page 2 of 2',{exact:true}).waitFor();
    assert.equal(await page.title(),'Market Lab · Tervory');
    const lastIds=await page.locator('tbody tr').evaluateAll(rows=>rows.map(row=>row.id));
    assert.equal(lastIds.length,17);
    assert.equal(await page.locator('tbody tr').first().locator('td').nth(1).innerText(),'Jan 1976');
    assert.equal(await page.locator('tbody tr').last().locator('td').nth(1).innerText(),'Jan 1960');
    assert.equal(new Set([...firstIds,...lastIds]).size,67);
    assert.equal(await page.getByRole('link',{name:'Next',exact:true}).count(),0);
    await screenshot('source-page-2');
    await page.getByRole('link',{name:'Previous',exact:true}).click();
    await page.getByText('Page 1 of 2',{exact:true}).waitFor();
    assert.deepEqual(await page.locator('tbody tr').evaluateAll(rows=>rows.map(row=>row.id)),firstIds);
    // An existing citation uses an original row index, not the current display position.
    await page.goto(new URL(`${sourcePath}?row=0#point-0`,base).href);
    await page.getByText('Page 2 of 2',{exact:true}).waitFor();
    assert.equal(await page.locator('#point-0 td').nth(1).innerText(),'Jan 1960');
    await page.locator('#point-0').evaluate(node=>node.scrollIntoView({block:'center'}));
    await screenshot('earlier-citation');
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.goto(new URL(`${sourcePath}?page=999`,base).href);
    await page.getByText('Page 2 of 2',{exact:true}).waitFor();
    assert.equal(await page.locator('tbody tr').count(),17);
    await page.goto(new URL(`${sourcePath}?page=invalid`,base).href);
    await page.getByText('Page 1 of 2',{exact:true}).waitFor();
    assert.equal(await page.locator('tbody tr').count(),50);
    report.widths.push(width);
    report.title=await page.title();
    report.pageSizes=[firstIds.length,lastIds.length];
    report.firstPeriod='Jan 2026';
    report.lastPeriod='Jan 1960';
    report.currentValue='14,474';
    report.oldCitation={href:`${sourcePath}?row=0#point-0`,page:2,period:'Jan 1960'};
    await context.close();
  }
  assert.deepEqual(report.browserErrors,[]);
  assert.deepEqual(report.externalRequests,[]);
  report.result='passed';
  await fs.writeFile(path.join(output,'browser-proof.json'),JSON.stringify(report,null,2)+'\n');
  console.log('Source-page proof passed: title, formatted value, 50/17 rows, Next/Previous, stable old citation, mobile and desktop.');
} finally {await browser.close();}
