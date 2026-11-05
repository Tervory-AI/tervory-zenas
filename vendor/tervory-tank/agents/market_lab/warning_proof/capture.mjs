// Read-only source navigation plus local fixture feedback on the real Tank.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {parseArgs} from 'node:util';
import {pathToFileURL} from 'node:url';
const {values}=parseArgs({options:{base:{type:'string',default:'http://127.0.0.1:3047'},output:{type:'string'},playwright:{type:'string'},browser:{type:'string',default:'/usr/bin/chromium'}}});
const base=new URL(values.base);
assert(['127.0.0.1','localhost'].includes(base.hostname));
const {chromium}=await import(pathToFileURL(path.resolve(values.playwright ?? 'agents/market_lab/fixture_proof/node_modules/playwright/index.mjs')).href);
const output=path.resolve(values.output ?? 'agents/market_lab/evidence/warnings');
await fs.mkdir(output,{recursive:true});
const report={caption:'local, fixture page',widths:[],screenshots:[],links:[],reactions:[],statuses:[],errors:[]};
const browser=await chromium.launch({executablePath:values.browser,headless:true,args:['--no-sandbox']});
try {
  for(const width of [1440,375]){
    const context=await browser.newContext({viewport:{width,height:1000}});
    await context.route('**/*',route=>new URL(route.request().url()).origin===base.origin ? route.continue():route.abort());
    const page=await context.newPage();page.on('pageerror',error=>report.errors.push(error.message));
    const shot=async name=>{
      assert.equal(await page.getByText('local, fixture page',{exact:true}).count(),0);
      await page.evaluate(()=>document.fonts.ready);
      await page.screenshot({path:path.join(output,`${name}-${width}.png`)});
      report.screenshots.push(`${name}-${width}.png`);
    };
    await page.goto(new URL('/market-lab',base).href);
    await page.locator('.ml-warning').waitFor();assert.equal(await page.locator('.ml-warning').count(),1);
    const facts=page.locator('.ml-warning p a');
    const links=await facts.evaluateAll(nodes=>nodes.map(n=>n.getAttribute('href')));
    assert(links.length>=5);
    const text=await page.locator('#warnings').innerText();
    assert(text.includes('3 of 3 past changes')&&text.includes('18.0%')&&text.includes('may become unavailable'));
    assert(!/z-score|coefficient|engine|\d{4}-\d{2}-\d{2}/.test(text));
    await page.locator('#warnings').scrollIntoViewIfNeeded();await shot('warning');
    for(const label of ['Useful','Not useful']){
      await page.getByRole('button',{name:label,exact:true}).click();
      await page.waitForFunction(name=>[...document.querySelectorAll('button')].some(b=>b.textContent===name&&b.getAttribute('aria-pressed')==='true'),label);
      assert(await page.getByText('Your choice is saved.',{exact:true}).isVisible());
      report.reactions.push({width,choice:label,saved:true});
    }
    await page.reload();assert.equal(await page.getByRole('button',{name:'Not useful',exact:true}).getAttribute('aria-pressed'),'true');
    if(width===1440){
      const checkedRaw=new Set();
      for(const href of links){
        const source=await context.newPage();const response=await source.goto(new URL(href,base).href);assert.equal(response.status(),200);
        assert(await source.locator('.ml-warning-evidence').isVisible());report.links.push(href);
        const raw=await source.locator('.ml-warning-evidence a[href*="?row="]').evaluateAll(nodes=>nodes.map(n=>n.getAttribute('href')));
        for(const target of [...new Set(raw)]){
          if(checkedRaw.has(target))continue; checkedRaw.add(target);
          const stored=await context.newPage();assert.equal((await stored.goto(new URL(target,base).href)).status(),200);
          const row=stored.locator(new URL(target,base).hash);assert.equal(await row.count(),1);
          await row.scrollIntoViewIfNeeded();assert(await row.isVisible());
          assert.notEqual(await row.evaluate(node=>getComputedStyle(node).backgroundColor),'rgba(0, 0, 0, 0)');
          await stored.close();
        }
        if(href.endsWith('#history')){await source.screenshot({path:path.join(output,'evidence-1440.png')});report.screenshots.push('evidence-1440.png');}
        await source.close();
      }
    }
    await context.addCookies([{name:'proof_case',value:'one',url:base.origin}]);
    await page.goto(new URL('/market-lab?case=one#warnings',base).href);
    await page.getByText('Not enough history yet: 1 of 3 past episodes',{exact:true}).waitFor();
    assert.equal(await page.locator('.ml-warning').count(),0);await page.locator('#warnings').scrollIntoViewIfNeeded();await shot('one-episode');
    await context.addCookies([{name:'proof_case',value:'resolved',url:base.origin}]);
    await page.goto(new URL('/market-lab?case=resolved#warnings',base).href);await page.getByText('Came true',{exact:true}).waitFor();
    assert.equal(await page.locator('.ml-warning').count(),3);
    for(const label of ['Came true','Did not come true','Not measurable: there were not enough saved observations during the window.']){
      const status=page.getByText(label,{exact:true});assert.equal(await status.count(),1);report.statuses.push({width,label});
      const source=await context.newPage();assert.equal((await source.goto(new URL(await status.getAttribute('href'),base).href)).status(),200);
      await source.locator('#resolution').waitFor();
      if(width===1440){const name=label==='Came true'?'came-true':label==='Did not come true'?'did-not-come-true':'not-measurable';await source.screenshot({path:path.join(output,`${name}-1440.png`)});report.screenshots.push(`${name}-1440.png`);}
      await source.close();
    }
    await page.locator('#warnings').scrollIntoViewIfNeeded();await shot('resolved');
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    report.widths.push(width);await context.close();
  }
  assert.deepEqual(report.errors,[]);
} finally { await browser.close(); }
await fs.writeFile(path.join(output,'browser.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify({result:'passed',widths:report.widths,citations:report.links.length,reactions:report.reactions.length,statuses:report.statuses.length,browserErrors:report.errors.length}));
