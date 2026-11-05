// Browser proof over the real components and local Postgres fixture records.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';
const {values} = parseArgs({options:{playwright:{type:'string'},browser:{type:'string'},base:{type:'string',default:'http://127.0.0.1:3046'},output:{type:'string'}}});
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const base=new URL(values.base);
assert(['127.0.0.1','localhost'].includes(base.hostname),'Fixture proof only runs on loopback');
const playwright=await import(values.playwright ? pathToFileURL(path.resolve(values.playwright)).href : 'playwright');
const {chromium}=playwright.default ?? playwright;
const output=path.resolve(values.output || path.join(root,'agents/market_lab/evidence'));
await fs.mkdir(output,{recursive:true});
const browser=await chromium.launch({executablePath:values.browser,headless:true,args:['--no-sandbox']});
const question='What are the latest population figures for California?';
const report={label:'local, fixture page',question,widths:[],screenshots:[],citations:[],sourcePagesRead:[],unrelatedQuestions:[],missingSourceChecked:false,listingQuestion:'Active listings in Alameda',browserErrors:[],externalRequests:[]};
const seenCitations=new Set();
try{
  for(const width of [1440,375]){
    const context=await browser.newContext({viewport:{width,height:1000}});
    await context.route('**/*',route=>{
      const url=new URL(route.request().url());
      if(['http:','https:'].includes(url.protocol) && url.origin!==base.origin){
        report.externalRequests.push(url.hostname);
        return route.abort();
      }
      return route.continue();
    });
    const page=await context.newPage();
    page.on('pageerror',error=>report.browserErrors.push(error.message));
    const screenshot=async(name)=>{
      assert(await page.getByText('local, fixture page',{exact:true}).isVisible());
      await page.screenshot({path:path.join(output,`${name}-${width}.png`)});
      report.screenshots.push(`${name}-${width}.png`);
    };
    assert.equal((await page.goto(new URL('/market-lab',base).href)).status(),200);
    await page.getByRole('heading',{name:'Market Lab',exact:true}).waitFor();
    await page.locator('.ml-card').first().waitFor();
    await page.evaluate(()=>document.fonts.ready);
    assert.equal(await page.locator('.ml-counts .ml-count').first().innerText(),'16');
    assert.equal(await page.locator('.ml-card').count(),16);
    assert(await page.getByText('1 stored observation',{exact:true}).count()>0);
    assert.equal(await page.getByText('1 stored observations',{exact:true}).count(),0);
    assert(await page.getByText('2 stored observations',{exact:true}).count()>0);
    if(width===1440){
      const hrefs=await page.locator('.ml-card a[href^="/market-lab/sources/"]').evaluateAll(nodes=>nodes.map(node=>node.getAttribute('href')));
      assert.equal(new Set(hrefs).size,16);
      for(const href of hrefs){
        const source=await context.newPage();
        assert.equal((await source.goto(new URL(href,base).href)).status(),200);
        const visible=await source.locator('main').innerText();
        assert(!/County not named in source|\bPRCP\b|\bTAVG\b|source category|engine's|Engrossed|930630|\b\d{4}-\d{2}-\d{2}\b|Terms checked|Counts may be calculated/.test(visible));
        const publisherHref=await source.locator('.ml-source p').filter({hasText:/^Source:/}).locator('a').getAttribute('href');
        const publisher=new URL(publisherHref);
        assert.equal(publisher.protocol,'https:');
        assert(!publisher.hostname.startsWith('api.') && !/\/api\/|\/webservices\/|\.json/.test(publisher.pathname),'Publisher links must be readable dataset pages');
        report.sourcePagesRead.push({href,title:await source.locator('h1').innerText(),publisherHref});
        await source.close();
      }
    }
    await page.getByPlaceholder('Search feeds and linked pages').fill('zzzz no match');
    await page.getByText('Nothing in Market Lab matches that.',{exact:true}).waitFor();
    assert.equal(await page.getByRole('heading',{name:'On',exact:true}).count(),0);
    assert.equal(await page.getByRole('heading',{name:'Linked pages',exact:true}).count(),0);
    await screenshot('no-match');
    await page.getByPlaceholder('Search feeds and linked pages').fill('');
    const carrier=page.locator('.ml-linkcard').filter({has:page.getByRole('heading',{name:'Carrier rules',exact:true})});
    await carrier.getByRole('button',{name:'Show links'}).click();
    assert.match(await carrier.locator('li').first().innerText(), /Checked \d/);
    assert.match(await carrier.locator('li').nth(1).innerText(), /Not checked yet/);
    await carrier.getByRole('button',{name:'Hide links'}).click();
    await page.locator('h1').evaluate(node=>node.scrollIntoView({block:'start'}));
    await screenshot('feeds');
    const unrelated=JSON.parse(await fs.readFile(path.join(root,'tests/fixtures/market_lab_questions.json'),'utf8')).unrelated;
    for(const [index,query] of unrelated.entries()){
      await page.getByRole('textbox',{name:'Ask Market Lab',exact:true}).fill(query);
      const received=page.waitForResponse(r=>r.url()===new URL('/market-lab/ask',base).href&&r.request().method()==='POST');
      await page.getByRole('button',{name:'Send',exact:true}).click();
      const response=await received;
      assert.equal(response.status(),200);
      assert.deepEqual(await response.json(),{content:'No stored data matches this question',facts:[],sources:[]});
      await page.getByText('No stored data matches this question',{exact:true}).waitFor();
      assert.equal(await page.locator('.ml-answer .ml-lines a').count(),0);
      if(width===1440)report.unrelatedQuestions.push(query);
      if(index===0){
        await page.locator('.ml-chat').evaluate(node=>node.scrollIntoView({block:'start'}));
        await screenshot('no-data');
      }
      await page.goto(new URL('/market-lab',base).href);
    }
    const missing=await page.goto(new URL('/market-lab/sources/00000000-0000-0000-0000-000000000000',base).href);
    assert.equal(missing.status(),404);
    await page.getByRole('heading',{name:"This source page doesn't exist",exact:true}).waitFor();
    assert(!((await page.locator('main').innerText()).includes('Try again shortly')));
    await screenshot('missing-source');
    report.missingSourceChecked=true;
    await page.goto(new URL('/market-lab',base).href);
    await page.getByRole('textbox',{name:'Ask Market Lab',exact:true}).fill(question);
    await screenshot('question');
    const received=page.waitForResponse(r=>r.url()===new URL('/market-lab/ask',base).href&&r.request().method()==='POST');
    await page.getByRole('button',{name:'Send',exact:true}).click();
    const response=await received;
    assert.equal(response.status(),200);
    const brief=await response.json();
    assert(brief.facts.length>0,'Question must be answered from stored rows');
    await page.locator('.ml-answer .ml-lines a').first().waitFor();
    assert.equal(await page.locator('.ml-answer .ml-lines a').count(),brief.facts.length);
    // Every numeric statement in the brief is entirely inside its source link.
    const uncited=await page.locator('.ml-answer').evaluate(node=>{
      const copy=node.cloneNode(true);
      copy.querySelectorAll('a').forEach(link=>link.remove());
      return copy.textContent;
    });
    assert(!/\d/.test(uncited),'No uncited number may remain in the brief');
    await page.locator('.ml-chat').evaluate(node=>node.scrollIntoView({block:'start'}));
    await screenshot('brief');
    for(const fact of brief.facts){
      assert.match(fact.href,/^\/market-lab\/sources\/[0-9a-f-]+\?row=\d+#point-\d+$/);
      const source=await context.newPage();
      assert.equal((await source.goto(new URL(fact.href,base).href)).status(),200);
      const id=new URL(fact.href,base).hash;
      const row=source.locator(id);
      await row.waitFor();
      const cells=await row.locator('th,td').allTextContents();
      cells[3]=await row.locator('.ml-source-value').innerText();
      assert(['California','Los Angeles County, California'].includes(cells[1]),'Area must show a place name');
      const stored=await source.locator('.ml-source p').first().innerText();
      assert.match(stored,/^Stored on \d{1,2} [A-Z][a-z]{2} \d{4}, \d{1,2}:\d{2} [AP]M PT\./);
      assert(!/T\d{2}:\d{2}/.test(stored),'No raw timestamp in source metadata');
      assert.equal(await source.getByRole('heading',{name:'Source downloads',exact:true}).count(),0,'No download heading for an empty list');
      assert.equal(await source.locator('#point-0 .ml-source-revision').innerText(),'Earlier figure (acs-v1)');
      assert.equal(await source.locator('#point-1 .ml-source-revision').innerText(),'Current figure (acs-v2)');
      assert(fact.text.includes(cells[0]),'Cited title must match');
      assert(fact.text.includes(cells[2]),'Cited date must match');
      assert(fact.text.includes(cells[3]),'Cited formatted value must match');
      assert(fact.text.includes(cells[4]),'Cited unit must match');
      if(!seenCitations.has(fact.href)){
        seenCitations.add(fact.href);
        report.citations.push({statement:fact.text,href:fact.href,storedRow:cells});
      }
      await source.close();
    }
    // Open the real citation from the browser's answer and capture the source.
    await page.locator('.ml-answer .ml-lines a').first().click();
    await page.getByRole('heading',{name:'Stored observations'}).waitFor();
    await page.locator(':target').evaluate(node=>node.scrollIntoView({block:'center'}));
    await screenshot('source');
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal page overflow');
    await page.goto(new URL('/market-lab',base).href);
    await page.getByRole('textbox',{name:'Ask Market Lab',exact:true}).fill(report.listingQuestion);
    const listingResponse=page.waitForResponse(r=>r.url()===new URL('/market-lab/ask',base).href&&r.request().method()==='POST');
    await page.getByRole('button',{name:'Send',exact:true}).click();
    const listing=await (await listingResponse).json();
    const fact=listing.facts.find(f=>f.text.startsWith('Active listings in Alameda County, California:'));
    assert(fact && fact.text.includes(': 1 listing ('),'Singular listing in the real answer');
    await page.locator('.ml-answer .ml-lines a').first().waitFor();
    await page.locator('.ml-chat').evaluate(node=>node.scrollIntoView({block:'start'}));
    await screenshot('listing-brief');
    for(const item of listing.facts){
      const source=await context.newPage();
      assert.equal((await source.goto(new URL(item.href,base).href)).status(),200);
      const row=source.locator(new URL(item.href,base).hash);
      const cells=await row.locator('th,td').allTextContents();
      cells[3]=await row.locator('.ml-source-value').innerText();
      assert(item.text.includes(cells[0]) && item.text.includes(cells[2]));
      assert(item.text.includes(cells[3]) && item.text.includes(cells[4]));
      if(!seenCitations.has(item.href)){
        seenCitations.add(item.href);
        report.citations.push({statement:item.text,href:item.href,storedRow:cells});
      }
      await source.close();
    }
    await page.locator('.ml-answer .ml-lines a').filter({hasText:fact.text}).click();
    const listingRow=page.locator(new URL(fact.href,base).hash);
    await listingRow.evaluate(node=>node.scrollIntoView({block:'center'}));
    const cells=await listingRow.locator('th,td').allTextContents();
    cells[3]=await listingRow.locator('.ml-source-value').innerText();
    assert.equal(cells[1],'Alameda County, California');
    assert.equal(cells[3],'1');
    assert.equal(cells[4],'listing');
    await screenshot('listing-source');
    if(!seenCitations.has(fact.href)){
      seenCitations.add(fact.href);
      report.citations.push({statement:fact.text,href:fact.href,storedRow:cells});
    }
    report.widths.push(width);
    await context.close();
  }
  assert.deepEqual(report.browserErrors,[]);
  assert.deepEqual(report.externalRequests,[]);
  report.result='passed';
  await fs.writeFile(path.join(output,'browser-proof.json'),JSON.stringify(report,null,2)+'\n');
  console.log(`Local fixture page: ${report.screenshots.length} screenshots, ${report.citations.length} exact citations, no external browser requests or errors.`);
} finally { await browser.close(); }
