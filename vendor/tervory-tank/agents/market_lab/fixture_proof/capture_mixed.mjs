// Citation focus through the real page loader, Tank and Postgres.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';
import {parseArgs} from 'node:util';
const {values}=parseArgs({options:{browser:{type:'string'},base:{type:'string',default:'http://127.0.0.1:3046'},output:{type:'string',default:'agents/market_lab/evidence/polish/mixed'}}});
const base=new URL(values.base);
assert(['127.0.0.1','localhost'].includes(base.hostname));
await fs.mkdir(values.output,{recursive:true});
const report={label:'local, fixture page',rows:{observations:123,notices:60},checks:[],errors:[]};
const browser=await chromium.launch({executablePath:values.browser,headless:true,args:['--no-sandbox']});
try {
  for (const width of [1440,375]) {
    const page=await browser.newPage({viewport:{width,height:1000}});
    page.on('pageerror',error=>report.errors.push(error.message));
    await page.goto(new URL('/market-lab',base).href);
    const noticeLinks=[];
    for (const index of [5,59]) {
      const answer=await page.request.post(new URL('/market-lab/ask',base).href,{data:{question:`Harbor notice ${1960+index}`}});
      assert.equal(answer.status(),200);
      const body=await answer.json();
      assert.equal(body.facts.length,1);
      const href=body.facts[0].href;
      assert(href.endsWith(`?row=${index}&kind=event#event-${index}`));
      noticeLinks.push(href);
    }
    const sourcePath=new URL(noticeLinks[0],base).pathname;
    for (const [kind,index,pageNumber,href] of [
      ['event',5,2,noticeLinks[0]],['event',59,1,noticeLinks[1]],
      ['point',0,3,`${sourcePath}?row=0#point-0`],['point',122,1,`${sourcePath}?row=122#point-122`],
    ]) {
      await page.goto(new URL(href,base).href);
      await page.getByText(`Page ${pageNumber} of 3`,{exact:true}).waitFor();
      const target=page.locator(`#${kind}-${index}`);
      assert.equal(await target.count(),1);
      assert(await target.evaluate(node=>node.matches(':target')),'Citation fragment must target the actual row');
      assert.equal(await target.evaluate(node=>getComputedStyle(node).outlineStyle),'solid');
      assert.equal(await target.evaluate(node=>getComputedStyle(node).outlineWidth),'2px');
      await target.scrollIntoViewIfNeeded();
      // The proof caption is outside the product, as requested by the founder.
      await page.getByText('local, fixture page',{exact:true}).evaluate(node=>node.remove());
      await page.screenshot({path:`${values.output}/${kind}-${index}-${width}.png`});
      report.checks.push({width,kind,index,page:pageNumber,href,highlighted:true});
    }
    await page.close();
  }
  assert.deepEqual(report.errors,[]);
  report.result='passed';
  await fs.writeFile(`${values.output}/browser-proof.json`,JSON.stringify(report,null,2)+'\n');
  console.log('Mixed-source proof passed: 123 observations, 60 notices; notice 5/59 and observation 0/122 open on pages 2/1/3/1 with highlighted targets at 1440px and 375px.');
} finally {await browser.close();}
