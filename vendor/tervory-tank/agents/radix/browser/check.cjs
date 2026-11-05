// No clicks, consent, cookie values, HTML evidence, or external tool updates.
const { chromium } = require('playwright');
const AxeBuilder = require('@axe-core/playwright').default;
(async () => {
  const target = new URL(process.argv[2]);
  if (target.protocol !== 'https:' || target.port || target.pathname !== '/' || target.search || target.username || target.password) throw Error('invalid target');
  const browser = await chromium.launch({headless:true, proxy:{server:'http://radix-passive-proxy:8082'}, args:[
    '--proxy-bypass-list=<-loopback>','--disable-quic','--disable-background-networking','--disable-component-update',
    '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
    '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE radix-passive-proxy'
  ]});
  try {
    const context = await browser.newContext({serviceWorkers:'block', ignoreHTTPSErrors:true});
    const observed = [];
    const firstParty = domain => { const d=domain.toLowerCase().replace(/^\./,''); return target.hostname===d || target.hostname.endsWith('.'+d); };
    const metadata = (raw, url) => {
      const parts=raw.split(';').map(p=>p.trim()), u=new URL(url), attrs={};
      for (const p of parts.slice(1)) {const at=p.indexOf('='); attrs[(at<0?p:p.slice(0,at)).toLowerCase()]=at<0?'':p.slice(at+1);}
      const domain=attrs.domain || u.hostname;
      return {name:parts[0].split('=',1)[0],domain,path:attrs.path || u.pathname.slice(0,u.pathname.lastIndexOf('/')+1) || '/',
        origin:'response_header',accepted:null,expiry:attrs['max-age'] ? Date.now()/1000+Number(attrs['max-age']) : attrs.expires ? Date.parse(attrs.expires)/1000 : -1,
        Secure:'secure' in attrs,HttpOnly:'httponly' in attrs,SameSite:attrs.samesite || 'Lax',first_party:firstParty(domain)};
    };
    await context.exposeBinding('__radix_cookie_set', (_source,c) => {
      if (!c || typeof c.name!=='string' || typeof c.domain!=='string' || typeof c.path!=='string') return;
      observed.push({name:c.name,domain:c.domain,path:c.path,expiry:Number.isFinite(c.expiry)?c.expiry:-1,
        origin:'script_observation',accepted:null,Secure:c.Secure===true,HttpOnly:false,SameSite:typeof c.SameSite==='string'?c.SameSite:'Lax',first_party:firstParty(c.domain)});
    });
    await context.addInitScript(() => {
      const original=Object.getOwnPropertyDescriptor(Document.prototype,'cookie');
      if (window.cookieStore) window.cookieStore.addEventListener('change', event => {
        for (const c of [...event.changed,...event.deleted]) window.__radix_cookie_set({
          name:c.name,domain:c.domain||location.hostname,path:c.path||'/',
          expiry:Number.isFinite(c.expires)?c.expires/1000:-1,Secure:c.secure,HttpOnly:false,SameSite:c.sameSite||'Lax'
        }).catch(()=>{});
      });
      Object.defineProperty(document,'cookie',{get(){return original.get.call(this);},set(raw){
        original.set.call(this,raw);
        const parts=String(raw).split(';').map(p=>p.trim()),attrs={};
        for(const p of parts.slice(1)){const at=p.indexOf('=');attrs[(at<0?p:p.slice(0,at)).toLowerCase()]=at<0?'':p.slice(at+1);}
        window.__radix_cookie_set({name:parts[0].split('=',1)[0],domain:attrs.domain||location.hostname,
          path:attrs.path||location.pathname.slice(0,location.pathname.lastIndexOf('/')+1)||'/',
          expiry:attrs['max-age']?Date.now()/1000+Number(attrs['max-age']):attrs.expires?Date.parse(attrs.expires)/1000:-1,
          Secure:'secure' in attrs,HttpOnly:false,SameSite:attrs.samesite||'Lax'}).catch(()=>{});
      }});
    });
    const page = await context.newPage();
    const responses=[];
    page.on('response', response=>responses.push((async()=>{
      for(const h of await response.headersArray()) if(h.name.toLowerCase()==='set-cookie') observed.push(metadata(h.value,response.url()));
    })()));
    let refused = 0, clientRefused = 0;
    await context.routeWebSocket('**/*', route => {clientRefused++; route.close();});
    await context.route('**/*', async route => {
      const u = new URL(route.request().url());
      if (u.protocol !== 'https:' || (u.port && u.port !== '443') || u.username || u.password) {
        clientRefused++; return route.abort();
      }
      return route.continue(); // Other hosts are attempted only via the exact-host proxy.
    });
    page.on('requestfailed', request => {
      const u = new URL(request.url());
      if (u.hostname !== target.hostname && u.protocol === 'https:' && !u.port) refused++;
    });
    const response = await page.goto(target.href, {waitUntil:'domcontentloaded',timeout:20000});
    if (!response || response.status() !== 200 || new URL(page.url()).hostname !== target.hostname) throw Error('page incomplete');
    await page.waitForTimeout(1000);
    const result = process.argv[3] === 'notice-only' ? {violations:[]} : await new AxeBuilder({page}).analyze();
    await Promise.all(responses);
    const cookies = [...observed,...(await context.cookies()).map(c => ({name:c.name, domain:c.domain, path:c.path,
      origin:'browser_store',accepted:true,expiry:c.expires, Secure:c.secure, HttpOnly:c.httpOnly, SameSite:c.sameSite,
      first_party:target.hostname === c.domain.replace(/^\./,'') || target.hostname.endsWith('.'+c.domain.replace(/^\./,''))}))];
    const findings = result.violations.filter(v=>['serious','critical'].includes(v.impact)).flatMap(v=>v.nodes.map(n=>({
      tool:'axe-core',rule:v.id,severity:v.impact,item:JSON.stringify(n.target),file:target.href,line:0,
      explanation:v.help,remediation:v.helpUrl,facts:{page:target.href,rule:v.id,element:n.target,help:v.help,helpUrl:v.helpUrl}
    })));
    process.stdout.write(JSON.stringify({findings,cookies,third_party_refused:refused,client_refused:clientRefused}));
    await context.close();
  } finally { await browser.close(); }
})().catch(()=>{process.stderr.write('browser check incomplete\n');process.exitCode=1;});
