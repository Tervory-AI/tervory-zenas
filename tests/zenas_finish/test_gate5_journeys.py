"""Real React/Chromium journeys; only HTTP, auth and Next navigation are fakes."""
import asyncio
import gzip
import json
from pathlib import Path
import subprocess
from time import monotonic

import pytest
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
ERROR = 'Zenas could not be reached. Check your connection and try again.'


@pytest.fixture(scope='module')
def client_bundle(tmp_path_factory):
    folder = tmp_path_factory.mktemp('zenas-browser')
    built = subprocess.run(['node', str(ROOT/'tests/zenas_finish/gate5_bundle.mjs'), str(folder)], cwd=ROOT, capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    script = (folder/'client.js').read_text()
    css = (folder/'client.css').read_text()
    # Includes the real React runtime and every Zenas screen, not a test-only shell.
    assert len(gzip.compress(script.encode())) < 160_000
    return script, css


async def screen(browser, bundle, mode, props=None, api=None, width=390, slow=False):
    context = await browser.new_context(viewport={'width': width, 'height': 844}, reduced_motion='reduce')
    page = await context.new_page()
    if slow:
        cdp = await context.new_cdp_session(page)
        await cdp.send('Emulation.setCPUThrottlingRate', {'rate': 4})
        await context.add_init_script("window.layoutShifts=[];new PerformanceObserver(list=>window.layoutShifts.push(...list.getEntries().filter(e=>!e.hadRecentInput).map(e=>e.value))).observe({type:'layout-shift',buffered:true});")
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    script, css = bundle
    async def route(request):
        path = request.request.url.split('placeholder.test',1)[-1]
        if path.startswith('/api/'):
            if api:
                return await api(request)
            return await request.fulfill(json={'hearings': [], 'sources': [], 'participants': []})
        if path == '/client.js':
            if slow: await asyncio.sleep(.15+len(gzip.compress(script.encode()))/50_000)
            return await request.fulfill(content_type='application/javascript', body=script)
        if path.startswith('/brand/'):
            return await request.fulfill(status=404)
        await request.fulfill(content_type='text/html',body='<html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0}'+css+'</style><div id="root"></div><script>window.screenMode='+json.dumps(mode)+';window.screenProps='+json.dumps(props or {})+'</script><script src="/client.js"></script></html>')
    await page.route('**/*', route)
    await page.goto('https://placeholder.test/')
    return context, page, errors


@pytest.mark.asyncio
async def test_signup_day_zero_first_action_result_sources_and_receipt(client_bundle):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        context, page, errors = await screen(browser, client_bundle, 'signup')
        await page.get_by_role('button',name='Continue to Trigger sign-in').press('Enter')
        await page.wait_for_function("window.signinRequest")
        signin = await page.evaluate('window.signinRequest')
        assert signin[0] == 'trigger-signin' and signin[2]['prompt'] == 'create'
        await page.evaluate('window.renderScreen("view")')
        await expect(page.get_by_text('0 · Open a matter', exact=False).first).to_be_visible()
        requests = []
        async def answer(route):
            requests.append(route.request)
            await route.fulfill(json={'kind':'answer','text':'The recorded source supports this civil rule.','sources':[
                {'citation':'Fed. R. Civ. P. 1','url':'https://example.invalid/rule','quote':'just, speedy, and inexpensive determination', 'paragraph':'1'}], 'matterId':'placeholder-matter'})
        await page.route('**/api/zenas/answers', answer)
        await page.get_by_role('textbox',name='Ask Zenas',exact=True).fill('What is the purpose of the civil rules?')
        await page.get_by_role('button',name='Send',exact=True).press('Enter')
        await expect(page.get_by_text('The recorded source supports this civil rule.')).to_be_visible()
        await expect(page.get_by_role('link',name='Fed. R. Civ. P. 1')).to_be_visible()
        assert len(requests)==1 and requests[0].headers.get('idempotency-key')
        await page.evaluate('window.renderScreen("matters",{initial:[{id:"placeholder-matter",title:"Civil rules",jurisdiction:"us",status:"open",area:"civil",documents:[{id:"placeholder-doc",kind:"memo",version:1,body:"The recorded source supports this civil rule."}]}]})')
        await expect(page.get_by_text('memo · version 1 · released')).to_be_visible()
        assert not errors
        await context.close(); await browser.close()


@pytest.mark.asyncio
async def test_network_error_retry_and_settings_missing_never_lock_the_form(client_bundle):
    requests=[]
    async def answer(route):
        requests.append(route.request.headers.get('idempotency-key'))
        if len(requests)==1:
            return await route.abort('failed')
        return await route.fulfill(status=503,json={'kind':'error','message':'Missing setting: TANK_KEY'})
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
        context,page,errors=await screen(browser,client_bundle,'view',api=answer)
        for _ in range(2):
            await page.get_by_role('textbox',name='Ask Zenas',exact=True).fill('What is the civil rule?')
            await page.get_by_role('button',name='Send',exact=True).click()
            await expect(page.get_by_role('button',name='Send',exact=True)).to_be_enabled()
            await expect(page.get_by_role('alert')).to_be_visible()
        assert len(requests)==2 and requests[0]==requests[1]
        await expect(page.get_by_text('Missing setting: TANK_KEY')).to_be_visible()
        assert not errors
        await context.close(); await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['matters','review','feeds','moot','deadlines','view'])
async def test_each_screen_is_keyboard_accessible_and_fits_a_slow_phone(client_bundle,mode):
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
        started=monotonic()
        context,page,errors=await screen(browser,client_bundle,mode,width=360,slow=True)
        await page.wait_for_selector('button, a, input, textarea')
        assert monotonic()-started<10
        await page.keyboard.press('Tab')
        assert await page.evaluate('document.activeElement.tagName') in {'BUTTON','A','INPUT','TEXTAREA','SELECT'}
        unnamed=await page.locator('input:not([type=hidden]),textarea,select').evaluate_all('nodes=>nodes.filter(n=>!n.getAttribute("aria-label")&&!n.labels?.length).map(n=>n.outerHTML)')
        assert not unnamed
        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert await page.evaluate('window.layoutShifts.reduce((a,b)=>a+b,0)') < .1
        contrast = await page.locator('body *').evaluate_all(r"""nodes => {
          function rgba(s) { const m=s.match(/[\d.]+/g)?.map(Number);return m?.length>=3?[m[0],m[1],m[2],m[3]??1]:[255,255,255,0]; }
          function luminance(c) { return c.slice(0,3).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0); }
          return nodes.filter(n=>[...n.childNodes].some(c=>c.nodeType===3&&c.textContent.trim())&&n.getClientRects().length&&!n.closest(':disabled,[aria-disabled=true]')).flatMap(n=>{
            const st=getComputedStyle(n);if(st.visibility==='hidden')return [];
            let bg=[255,255,255,1];let layers=[];
            for(let p=n;p;p=p.parentElement){const c=rgba(getComputedStyle(p).backgroundColor);layers.push(c);if(c[3]===1)break;}
            for(const c of layers.reverse())bg=c.map((v,i)=>i===3?1:v*c[3]+bg[i]*(1-c[3]));
            const fg=rgba(st.color);const color=fg.map((v,i)=>i===3?1:v*fg[3]+bg[i]*(1-fg[3]));
            const a=luminance(color),b=luminance(bg),ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
            const large=parseFloat(st.fontSize)>=24||(parseFloat(st.fontSize)>=18.66&&Number(st.fontWeight)>=700);
            return ratio<(large?3:4.5)-.01?[{text:n.textContent.trim().slice(0,50),ratio,color:st.color,bg}]:[];
          });
        }""")
        assert not contrast, contrast
        # Reserve intrinsic image size so failed/late logos do not move the form.
        assert await page.locator('img').evaluate_all('nodes=>nodes.every(n=>n.hasAttribute("width")&&n.hasAttribute("height"))')
        assert not errors
        await context.close(); await browser.close()


@pytest.mark.asyncio
async def test_matters_upload_and_review_retry_recover_from_transport_failure(client_bundle):
    calls=[]
    async def api(route):
        calls.append(route.request.url)
        return await route.abort('failed')
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
        context,page,errors=await screen(browser,client_bundle,'matters',api=api)
        await page.get_by_label('What is this matter about?').fill('Civil contract')
        await page.get_by_role('button',name='Open',exact=True).click()
        await expect(page.get_by_role('alert')).to_have_text(ERROR)
        await expect(page.get_by_role('button',name='Open',exact=True)).to_be_enabled()
        await page.evaluate('window.renderScreen("matters",{initial:[{id:"placeholder",title:"Civil contract",jurisdiction:"us",status:"open",area:"civil",documents:[]}]})')
        await page.get_by_label('Kind, for example notes').fill('notes')
        await page.get_by_label('Document text').fill('Private offline notes')
        await page.get_by_role('button',name='Upload',exact=True).click()
        await expect(page.get_by_role('button',name='Upload',exact=True)).to_be_enabled()
        await page.evaluate('window.renderScreen("review",{items:[{documentId:"placeholder",matterId:"placeholder-matter",title:"Draft",jurisdiction:"us",area:"civil",state:"in_review",body:"Civil draft",sources:[],matches:[]}]})')
        await page.get_by_role('button',name='Release to client',exact=True).click()
        await expect(page.get_by_role('alert')).to_have_text(ERROR)
        await expect(page.get_by_role('button',name='Release to client',exact=True)).to_be_enabled()
        assert len(calls)==3 and not errors
        await context.close(); await browser.close()


@pytest.mark.asyncio
async def test_dialog_keyboard_trap_escape_and_focus_return(client_bundle):
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
        context,page,errors=await screen(browser,client_bundle,'view',width=1280)
        plugins=page.get_by_role('button',name='Plugins',exact=True)
        await plugins.click()
        dialog=page.get_by_role('dialog')
        await expect(dialog).to_be_visible()
        controls=dialog.locator('button:visible,a[href]:visible,input:visible,textarea:visible,select:visible').filter(has_not=page.locator(':disabled'))
        await controls.first.focus()
        await page.keyboard.press('Shift+Tab')
        await expect(controls.last).to_be_focused()
        await page.keyboard.press('Tab')
        await expect(controls.first).to_be_focused()
        await page.keyboard.press('Escape')
        await expect(dialog).not_to_be_visible()
        await expect(plugins).to_be_focused()
        await expect(page.locator('input[type=file]').first).to_be_disabled()
        assert not errors
        await context.close();await browser.close()


@pytest.mark.asyncio
async def test_request_deadline_unblocks_without_replaying_a_mutation(client_bundle):
    entered=asyncio.Event();release=asyncio.Event();calls=[]
    async def hanging(route):
        calls.append(route.request)
        entered.set()
        await release.wait()
        await route.abort('failed')
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
        context,page,errors=await screen(browser,client_bundle,'view',api=hanging)
        await page.clock.install()
        await page.get_by_role('textbox',name='Ask Zenas',exact=True).fill('What is the civil rule?')
        await page.get_by_role('button',name='Send',exact=True).click()
        await entered.wait()
        await page.clock.fast_forward(120_001)
        await expect(page.get_by_role('alert')).to_have_text(ERROR)
        await expect(page.get_by_role('button',name='Send',exact=True)).to_be_enabled()
        assert len(calls)==1 and not errors
        release.set()
        await context.close();await browser.close()


@pytest.mark.asyncio
async def test_feed_read_retries_are_limited_and_failure_is_visible(client_bundle):
    calls=[]
    async def failed(route):
        calls.append(route.request)
        await route.fulfill(status=503,json={'error':ERROR})
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
        context,page,errors=await screen(browser,client_bundle,'feeds',api=failed)
        await page.get_by_role('button',name='South Africa',exact=True).click()
        await expect(page.get_by_role('status')).to_have_text('Feeds could not be loaded.')
        assert len(calls)==8 and all(request.method=='GET' for request in calls)
        assert not errors
        await context.close();await browser.close()
