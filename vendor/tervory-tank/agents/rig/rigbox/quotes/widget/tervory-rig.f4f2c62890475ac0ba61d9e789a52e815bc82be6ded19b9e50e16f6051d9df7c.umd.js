(function(e,t){typeof exports==`object`&&typeof module<`u`?t(exports):typeof define==`function`&&define.amd?define([`exports`],t):(e=typeof globalThis<`u`?globalThis:e||self,t(e.TervoryRig={}))})(this,function(e){Object.defineProperty(e,Symbol.toStringTag,{value:`Module`});var t=Object.defineProperty,n=(e,t,n)=>()=>{if(n)throw n[0];try{return e&&(t=e(e=0)),t}catch(e){throw n=[e],e}},r=(e,n)=>{let r={};for(var i in e)t(r,i,{get:e[i],enumerable:!0});return n||t(r,Symbol.toStringTag,{value:`Module`}),r},i=e=>String(e??``).replace(/[&<>"']/g,e=>({"&":`&amp;`,"<":`&lt;`,">":`&gt;`,'"':`&quot;`,"'":`&#39;`})[e]),a=(e,t)=>new Intl.NumberFormat(`en-US`,{style:`currency`,currency:t}).format(Number(e)),o=e=>e.reduce((e,t)=>e+Math.round(Number(t.price)*100)*t.quantity,0)/100;function s(e){return Object.fromEntries(Object.entries(e).filter(([e,t])=>[`min_days`,`max_days`,`ship_days`].includes(e)?typeof t==`number`&&Number.isFinite(t):[`eta`,`status`].includes(e)&&typeof t==`string`))}function c(e){let t=s(e.delivery||{}),n={id:String(e.id),product_id:String(e.product_id),title:String(e.title),quantity:Number(e.quantity),price:String(e.price),currency:String(e.currency),kind:String(e.kind),delivery:t};if(e.unavailable===!0&&(n.unavailable=!0),e.previous&&typeof e.previous==`object`){let t=e.previous;n.previous={price:String(t.price),delivery:s(t.delivery||{})}}return n}function l(e){let t={id:String(e.id),state:String(e.state),expires_at:String(e.expires_at),lines:Array.isArray(e.lines)?e.lines.map(e=>c(e)):[]};for(let n of[`number`,`total`,`currency`])typeof e[n]==`string`&&(t[n]=e[n]);if(typeof e.checkout_url==`string`){let n=new URL(e.checkout_url);n.protocol===`https:`&&!n.username&&!n.password&&(t.checkout_url=n.href)}return t.payment_confirmed=e.payment_confirmed===!0,t.late_payment=e.late_payment===!0,t}function u(e){return e.unavailable?`No longer available. Back order it or remove it.`:e.kind===`back_order`?`Back order · Delivery date not confirmed`:d(e.delivery)}function d(e){return e.min_days!==void 0&&e.max_days!==void 0?`Delivery: ${e.min_days}–${e.max_days} business days`:e.ship_days===void 0?e.eta?`Delivery: ${e.eta}`:``:`Delivery: ${e.ship_days} business days`}var f=(e,t,n=!1,r=!1,a=``)=>`<button type="button" class="cart-btn${n?` cart-secondary`:``}" data-action="cart-${t}" data-id="${i(a)}"${r?` disabled`:``}>${i(e)}</button>`,p=(e,t)=>`<div class="cart-notice" role="status"><strong>${i(e)}</strong>${t?`<p>${i(t)}</p>`:``}</div>`;function m(e,t=!1,n=!1,r=!1){let o=`<div class="cart-product"><h2>${i(e.title)}</h2><p class="cart-code">${i(e.product_id)}</p>`;return n||(o+=`<p class="cart-price">${i(a(e.price,e.currency))}</p>`),e.previous&&(o+=`<div class="cart-change">${e.previous.price===e.price?``:`<p>Price changed: ${i(a(e.previous.price,e.currency))} → ${i(a(e.price,e.currency))}.</p>`}${JSON.stringify(e.previous.delivery)===JSON.stringify(e.delivery)?``:`<p>Delivery changed: ${i(d(e.previous.delivery).replace(`Delivery: `,``).replace(` business days`,``))} → ${i(d(e.delivery).replace(`Delivery: `,``))}.</p>`}</div>`),n&&(o+=`<p class="cart-line-total">${e.quantity} × ${i(a(e.price,e.currency))} each<br>Line total: ${i(a(Number(e.price)*e.quantity,e.currency))}</p>`),o+=`<p>${i(u(e))}</p></div>`,t&&(o+=`<div class="cart-details"><label class="cart-quantity">Quantity<input type="number" min="1" max="100" step="1" value="${e.quantity}" id="cart-quantity-${i(e.id)}" data-cart-quantity="${i(e.id)}"${r?` disabled`:``}></label><p class="cart-line-total">Line total: ${i(a(Number(e.price)*e.quantity,e.currency))}</p><div class="cart-actions">${e.unavailable?f(`Back order`,`backorder`,!1,r,e.id):``}${f(e.unavailable?`Remove item`:`Remove`,`remove`,!0,r,e.id)}</div></div>`),`<article class="cart-card${t?` cart-line`:``}" data-line="${i(e.id)}">${o}</article>`}function ee(e){let{screen:t,lines:n,product:r,order:s,busy:c}=e;if(t===`search`)return``;let l=t===`product`?`FIND A PRODUCT`:t===`added`?`ITEM ADDED TO CART`:t===`handoff`?`GOING TO STORE CHECKOUT`:t===`return`||t===`error`?`STORE CHECKOUT`:t===`paid`?`ORDER RECEIVED`:`YOUR CART`,u;if(t===`product`&&r)u=`${m(r)}${r.kind===`back_order`?p(``,`No delivery date is available. You can back order this item or remove it.`):``}<div class="cart-actions">${f(r.kind===`back_order`?`Back order`:`Add to cart`,`add`,!1,c)}${r.kind===`back_order`?f(`Remove item`,`shop`,!0):``}</div>`;else if(t===`added`&&r)u=`${p(`Item added`,`The item is in the Rig cart on this store’s site.`)}${m(r)}<p>Quantity: ${r.quantity} · Cart total: ${i(a(o(n),r.currency))}</p><div class="cart-actions">${f(`View cart`,`view`)}${f(`Keep shopping`,`shop`,!0)}</div>`;else if(t===`handoff`)u=`<div class="cart-card cart-window"><h2>PAYMENT WINDOW</h2><p class="cart-duration">5 minutes</p><p>You’re going to ${i(e.store)}’s own checkout. The 5-minute window starts there. This store’s shop platform takes payment; Rig never does.</p><p>When the window ends, prices and stock must be checked again before checkout can continue.</p><div class="cart-actions">${f(`Continue to store checkout`,`checkout`,!1,c)}${f(`Back to cart`,`view`,!0)}</div></div>`;else if(t===`error`)u=`<div class="cart-card cart-window"><h2>This store’s checkout is unavailable</h2><strong>Your cart is saved</strong><p>We could not open this store’s checkout. You can try again or return to your cart.</p><p>Prices and stock will be checked again before the next checkout. The 5-minute window has not started.</p><div class="cart-actions">${f(`Try again`,`checkout`,!1,c)}${f(`Back to cart`,`view`,!0)}</div></div>`;else if(t===`return`&&s){let t=Math.max(0,Math.ceil((Date.parse(s.expires_at)-e.now)/1e3));u=`${p(`Checkout was not completed`,`No completed payment has been confirmed by the store. Your cart is still here.`)}<div class="cart-card cart-timer"><h2>TIME LEFT</h2><p class="cart-duration" data-cart-timer>${String(Math.floor(t/60)).padStart(2,`0`)}:${String(t%60).padStart(2,`0`)}</p></div><p>If this payment window ends, we’ll check prices and stock again before you continue.</p><div class="cart-actions">${f(`Return to store checkout`,`checkout`)}${f(`View cart`,`view`,!0)}</div>`}else if(t===`paid`&&s){let t=s.lines.filter(e=>e.kind===`back_order`).map(e=>e.title).join(`, `),n=s.late_payment?`${e.store} received your payment after the 5-minute window ended. Your order is going through.${t?` Your ${t} remains back ordered.`:``}`:`Payment was received by ${e.store}.${t?` Your ${t} is back ordered; its delivery date is not confirmed.`:``}`;u=`${p(s.late_payment?`Payment received after the window`:`Payment confirmed`,n)}<div class="cart-card cart-paid"><h2>ORDER #${i(s.number||s.id)}</h2><p class="cart-paid-total">Total paid: ${i(a(s.total||o(s.lines),s.currency||s.lines[0]?.currency||`USD`))}</p>${s.lines.map(e=>m(e,!1,!0)).join(``)}</div><div class="cart-actions">${f(`Keep shopping`,`shop`)}</div>`}else if(!n.length)u=`${p(`Your cart is empty`,`Add an item to this store’s cart to get started.`)}<div class="cart-actions">${f(`Keep shopping`,`shop`)}</div>`;else{let e={rechecking:[`Your 5-minute window ended`,`The unpaid store order was cancelled. We’re checking current prices and stock before checkout can continue.`],changed:[`Prices and delivery updated`,`Review the changes below before continuing to this store’s checkout. A new 5-minute payment window starts when checkout begins again.`],unchanged:[`Prices and stock are unchanged`,`Your cart is ready. A new 5-minute payment window starts at this store’s checkout.`],unavailable:[`An item is no longer available`,`Choose Back order or Remove item below before continuing.`]}[t];u=`${e?p(...e):``}<div class="cart-layout"><div class="cart-lines">${n.map(e=>m(e,!0,!1,c||t===`rechecking`||!!s)).join(``)}</div><aside class="cart-card cart-summary"><h2>SUMMARY</h2><p class="cart-price">Total: ${i(a(o(n),n[0].currency))}</p><p>${t===`unavailable`?`Choose back order or remove item before checkout.`:`${n.length} lines · ${n.reduce((e,t)=>e+t.quantity,0)} items`}</p>${f(t===`rechecking`?`Checking prices and stock…`:t===`unavailable`?`Choose back order or remove item`:t===`cart`?`Checkout`:`Continue to store checkout`,`handoff`,!1,c||t===`rechecking`||t===`unavailable`)}<p>Payment is completed at this store’s own checkout. Rig never takes payment.</p></aside></div>`}return`<div class="rig-cart"><header><strong>${i(e.store)}</strong><span>Rig</span></header><h1 tabindex="-1">${l}</h1>${u}</div>`}var te=`
.rig-cart{box-sizing:border-box;display:flex;flex-direction:column;gap:24px;width:100%;max-width:1200px;padding:40px 48px 48px;margin:auto;color:var(--rig-ink,#1c1e21);font:14px/20px var(--rig-font-body,'Source Sans 3',sans-serif)}.rig-cart *{box-sizing:border-box}.rig-cart header{display:flex;gap:12px;align-items:start;font-size:12px;line-height:17px}.rig-cart header strong{flex:1}.rig-cart header span{width:60px;text-align:right;font-size:11px;line-height:15px;color:var(--rig-muted,#5c6370)}.rig-cart h1{font:600 56px/62px var(--rig-font-heading,Newsreader,serif);margin:0;overflow-wrap:anywhere}.rig-cart h2{font:600 28px/39px var(--rig-font-heading,Newsreader,serif);margin:0}.rig-cart p{margin:0;overflow-wrap:anywhere}.cart-card{display:flex;flex-direction:column;align-items:stretch;gap:10px;padding:18px;border:1px solid var(--rig-line,#d5d8de);border-radius:var(--rig-radius,10px);background:var(--rig-card,#fff);min-width:0}.cart-code{font-size:13px;line-height:18px;color:var(--rig-muted,#5c6370)}.rig-cart .cart-price{font:600 30px/42px var(--rig-font-heading,Newsreader,serif)}.cart-actions{display:flex;gap:16px;flex-wrap:wrap}.rig-cart .cart-btn{display:flex;align-items:center;justify-content:center;min-height:48px;min-width:150px;padding:0 22px;border:2px solid var(--rig-accent,#1d4e89);border-radius:var(--rig-radius,10px);background:var(--rig-accent,#1d4e89);color:var(--rig-on-accent,#fff);font:600 13px/18px var(--rig-font-body,'Source Sans 3',sans-serif);cursor:pointer}.rig-cart .cart-secondary{background:var(--rig-card,#fff);border:1px solid var(--rig-ink,#1c1e21);color:var(--rig-ink,#1c1e21)}.rig-cart button:disabled{background:var(--rig-canvas,#f4f5f7);border-color:var(--rig-line,#d5d8de);color:var(--rig-ink,#1c1e21);opacity:1;cursor:wait}.cart-btn:focus-visible,.cart-quantity:focus-within{outline:2px solid var(--rig-accent,#1d4e89);outline-offset:3px}.cart-notice{display:flex;flex-direction:column;gap:8px;border:1px solid var(--rig-line,#d5d8de);border-radius:var(--rig-radius,10px);background:var(--rig-card,#fff);padding:16px}.cart-notice strong{font:600 20px/22px var(--rig-font-heading)}.cart-notice p{color:var(--rig-muted,#5c6370)}.cart-layout{display:grid;grid-template-columns:minmax(0,760fr) minmax(0,320fr);gap:24px;align-items:start}.cart-lines{display:flex;flex-direction:column;gap:16px}.cart-line{padding:16px;gap:12px}.cart-product{display:flex;flex-direction:column;gap:10px}.cart-details{display:flex;flex-direction:column;gap:8px}.cart-line-total{font-size:18px;line-height:25px;font-weight:600}.cart-quantity{display:flex;flex-direction:column;gap:7px;font-size:12px;line-height:17px}.cart-quantity input{box-sizing:border-box;width:100%;height:44px;min-height:44px;padding:10px 12px;border:1px solid var(--rig-field-border,#8b919a);border-radius:var(--rig-radius,10px);background:var(--rig-card,#fff);color:var(--rig-ink,#1c1e21);font:400 14px/20px var(--rig-font-body)}.cart-quantity input:focus{outline:0}.cart-summary{padding:20px;gap:16px}.cart-summary .cart-btn{width:100%;min-width:0}.cart-summary>p:last-child{font-size:13px;line-height:18px;color:var(--rig-muted,#5c6370)}.cart-change{font-weight:600}.cart-window{padding:24px;gap:16px}.cart-window>p{font-size:16px;line-height:22px}.cart-window>p:last-of-type{font-size:14px;line-height:20px;color:var(--rig-muted,#5c6370)}.rig-cart .cart-duration{font:600 48px/53px var(--rig-font-heading)}.cart-window>h2{font-size:26px;line-height:29px}.cart-window .cart-actions>*{flex:1}.cart-paid{padding:16px;gap:16px}.rig-cart .cart-paid-total{font:600 30px/33px var(--rig-font-heading)}.cart-timer{padding:20px;gap:16px}.cart-timer h2{font-size:26px;line-height:29px}.cart-paid .cart-card{padding:18px}.cart-paid>h2{font-size:26px;line-height:29px}@media(max-width:700px){.rig-cart{padding:24px 16px 16px}.rig-cart h1{font-size:34px;line-height:37px}.cart-layout{display:flex;flex-direction:column;gap:24px}.cart-lines,.cart-summary{width:100%}.cart-actions{flex-direction:column;gap:16px}.rig-cart .cart-btn{width:100%;min-width:0}.cart-window{padding:16px}.cart-window .cart-actions{gap:12px}}
`,h=class{request;changed;storageKey;enabled=!1;view={store:``,screen:`search`,lines:[],product:null,order:null,busy:!1,now:0};session=``;timer=0;polling=!1;generation=0;constructor(e,t,n){this.request=e,this.changed=t,this.storageKey=n}async start(){let e=this.generation;try{let t=await this.request(`session`,{});if(e!==this.generation||t.mode!==`cart`||typeof t.session!=`string`)return;this.enabled=!0,this.view.store=typeof t.store_name==`string`?t.store_name:``;try{this.session=sessionStorage.getItem(this.storageKey)||t.session,sessionStorage.setItem(this.storageKey,this.session)}catch{this.session=t.session}if(await this.read(),e!==this.generation)return;this.timer=window.setInterval(()=>{this.view.now=Date.now(),this.view.order&&!this.view.order.payment_confirmed&&this.poll()},1e3)}catch{}}stop(){window.clearInterval(this.timer),this.generation++}render(){return this.enabled?ee(this.view):``}call(e,t={}){return this.request(e,{session:this.session,...t})}lines(e){this.view.lines=Array.isArray(e.lines)?e.lines.map(e=>c(e)):[]}async read(){let e=await this.call(`view`);this.lines(e),typeof e.checkout_id==`string`&&(this.view.order=l(await this.call(`order`,{checkout_id:e.checkout_id})),await this.poll()),this.changed()}async preview(e){let t=++this.generation;this.view.busy=!0;try{let n=await this.call(`view`,{product_id:e});if(t!==this.generation)return;this.lines(n),this.view.product=c(n.product),this.view.screen=`product`}catch{t===this.generation&&(this.view.screen=`error`)}finally{t===this.generation&&(this.view.busy=!1,this.changed())}}async poll(){if(this.view.order&&!this.polling){this.polling=!0;try{if(this.view.order=l(await this.call(`order`,{checkout_id:this.view.order.id})),this.view.order.payment_confirmed)this.view.screen=`paid`;else if(this.view.order.state===`expired`){this.view.screen=`rechecking`,this.changed();let e=await this.call(`view`,{recheck:!0});this.lines(e),this.view.screen=e.recheck_state===`changed`?`changed`:e.recheck_state===`unavailable`?`unavailable`:`unchanged`,this.view.order=null}else[`pending`,`cancel_unknown`].includes(this.view.order.state)&&this.view.screen!==`cart`&&(this.view.screen=`return`);this.changed()}catch{}finally{this.polling=!1}}}async quantity(e,t){if(!Number.isInteger(t)||t<1||t>100||this.view.busy)return;let n=this.view.lines.find(t=>t.id===e);n&&await this.run(async()=>{this.lines(await this.call(`add`,{line_id:e,product_id:n.product_id,quantity:t,back_order:n.kind===`back_order`}))})}async run(e){if(!this.view.busy){this.view.busy=!0,this.changed();try{await e()}catch{this.view.screen=`error`}finally{this.view.busy=!1,this.changed()}}}async action(e,t=``){if(!this.enabled)return;if(e===`shop`||e===`handoff`||e===`view`){this.view.screen=e===`shop`?`search`:e===`handoff`?`handoff`:`cart`,this.changed();return}let n=e===`checkout`&&!this.view.busy?window.open(`about:blank`,`_blank`):null;n&&(n.opener=null),await this.run(async()=>{if(e===`add`&&this.view.product){let e=this.view.product;this.lines(await this.call(`add`,{product_id:e.product_id,quantity:1,back_order:e.kind===`back_order`})),this.view.product=this.view.lines.find(t=>t.product_id===e.product_id)||null,this.view.screen=`added`}else if(e===`remove`)this.lines(await this.call(`remove`,{line_id:t})),this.view.screen=`cart`;else if(e===`backorder`){let e=this.view.lines.find(e=>e.id===t);if(!e)return;this.lines(await this.call(`add`,{line_id:t,product_id:e.product_id,quantity:e.quantity,back_order:!0})),this.view.screen=this.view.lines.some(e=>e.unavailable)?`unavailable`:`cart`}else if(e===`checkout`){let e=l(await this.call(`checkout`));if(this.view.order={...e,lines:this.view.lines},!e.checkout_url||e.state!==`pending`){this.view.screen=`error`,n?.close();return}this.view.screen=`return`,n?n.location.replace(e.checkout_url):window.location.assign(e.checkout_url)}}),this.view.screen===`error`&&n?.close()}};function ne(){if(typeof crypto.randomUUID==`function`)return crypto.randomUUID();let e=crypto.getRandomValues(new Uint8Array(16));e[6]=e[6]&15|64,e[8]=e[8]&63|128;let t=Array.from(e,e=>e.toString(16).padStart(2,`0`)).join(``);return`${t.slice(0,8)}-${t.slice(8,12)}-${t.slice(12,16)}-${t.slice(16,20)}-${t.slice(20)}`}var g,_=n((()=>{g=class extends Error{code;status;constructor(e,t,n){super(t),this.name=`RigError`,this.code=e,this.status=n}}})),re=r({ask:()=>S,fits:()=>y,graph:()=>x,linkQuote:()=>C,product:()=>ae,quote:()=>w,search:()=>ie,setups:()=>oe,swap:()=>b});function v(){throw new g(`mock_not_bundled`,`Set api-base to your Rig API.`,400)}function y(){return v()}async function ie(){return v()}async function ae(){return v()}async function oe(){return v()}async function b(){return v()}async function x(){return v()}async function S(){return v()}async function C(){return v()}async function w(){return v()}var se=n((()=>{_()}));_();var T=class{apiBase;key;fetchImpl;mockModule=null;constructor(e){this.apiBase=e.apiBase.replace(/\/$/,``),this.key=e.key,this.fetchImpl=e.fetchImpl??globalThis.fetch.bind(globalThis)}get usesMock(){return this.apiBase===`mock`}loadMock(){return this.mockModule??=Promise.resolve().then(()=>(se(),re)),this.mockModule}async cart(e,t){return this.request(`/v1/rig/cart/${e}`,{method:`POST`,body:JSON.stringify(t)})}async privacyNotice(){return this.usesMock?null:(await this.request(`/v1/rig/privacy-notice`)).notice}async search(e,t=12){if(this.usesMock)return(await this.loadMock()).search(e,t);let n=new URLSearchParams({q:e,limit:String(t)});return this.request(`/v1/rig/search?${n.toString()}`)}async product(e){return this.usesMock?(await this.loadMock()).product(e):this.request(`/v1/rig/products/${encodeURIComponent(e)}`)}async settledSearch(e,t){this.usesMock||await this.request(`/v1/rig/search-events`,{method:`POST`,body:JSON.stringify({event_id:e,no_results:t})})}async setups(e){return this.usesMock?(await this.loadMock()).setups(e):this.request(`/v1/rig/setups`,{method:`POST`,body:JSON.stringify({product_id:e})})}async swap(e,t,n=!1){return this.usesMock?(await this.loadMock()).swap(e,t):this.request(`/v1/rig/swap`,{method:`POST`,body:JSON.stringify({setup:E(e),role:t,commit:n})})}async graph(e){return this.usesMock?(await this.loadMock()).graph(e):this.request(`/v1/rig/graph/${encodeURIComponent(e)}`)}async ask(e){return this.usesMock?(await this.loadMock()).ask(e):this.request(`/v1/rig/ask`,{method:`POST`,body:JSON.stringify({product_id:e.product_id,...e.setup?{setup:E(e.setup)}:{},question:e.question})})}async linkQuote(e){return this.usesMock?(await this.loadMock()).linkQuote(e):this.request(`/v1/rig/link-quote`,{method:`POST`,body:JSON.stringify({url:e.url,note:e.note,...e.notice_version?{notice_version:e.notice_version}:{},...e.contact?{contact:e.contact}:{}})})}async quote(e){if(this.usesMock)return(await this.loadMock()).quote(e);let t={contact:e.contact,...e.notice_version?{notice_version:e.notice_version}:{},...e.setup?{setup:E(e.setup)}:{},...e.product_id?{product_id:e.product_id}:{}};return this.request(`/v1/rig/quote`,{method:`POST`,body:JSON.stringify(t)})}async request(e,t={}){let n=new Headers(t.headers);n.set(`Accept`,`application/json`),n.set(`X-Rig-Key`,this.key),t.body&&n.set(`Content-Type`,`application/json`);let r=await this.fetchImpl(`${this.apiBase}${e}`,{...t,headers:n});if(!r.ok){let e=null;try{e=await r.json()}catch{}throw e?.error?.code?new g(e.error.code,e.error.message,r.status):new g(`http_error`,`The request failed.`,r.status)}return await r.json()}};function E(e){return{tier:e.tier,label:e.label,parts:e.parts,total:e.total,why:e.why}}function D(e){let t=Number.isInteger(e.amount)?0:2,[n,r]=e.amount.toFixed(t).split(`.`),i=n.replace(/\B(?=(\d{3})+(?!\d))/g,` `),a=r?`${i}.${r}`:i;if(e.currency===`ZAR`)return`R ${a}`;try{return new Intl.NumberFormat(`en-US`,{style:`currency`,currency:e.currency,minimumFractionDigits:t,maximumFractionDigits:t}).format(e.amount)}catch{return`${e.currency} ${a}`}}function O(e){return typeof e.attributes.summary==`string`?e.attributes.summary:e.category}function ce(e){return typeof e.attributes.detail==`string`?e.attributes.detail:O(e)}function k(e){return typeof e.attributes.match_note==`string`?e.attributes.match_note:``}function A(e){return e.map(e=>e.trim()).filter(Boolean)}var j={id:`custom-nation`,colors:{canvas:`#f2efe7`,ink:`#242722`,accent:`#ad322c`,muted:`#686b60`,faint:`#9a9c92`,line:`#e3dfd4`,lineStrong:`#d5d0c3`,card:`#ffffff`,badge:`#e8e4da`,onAccent:`#ffffff`,featured:`#242722`,featuredText:`#fcfaf4`,featuredMuted:`#b8baab`},graph:{canvas:`#121510`,raised:`#1a1d18`,text:`#fcfaf4`,muted:`#b8baab`,line:`rgba(255,255,255,0.12)`,chip:`#1a1d18`,chipOn:`#2a1a18`,glow:`rgba(31,42,34,1)`},fonts:{heading:`"Barlow Condensed", "Arial Narrow", sans-serif`,body:`Inter, "Helvetica Neue", sans-serif`,href:`https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600&family=Inter:wght@400;500;600&display=swap`},radius:`0px`,radiusGraph:`8px`,copy:{eyebrow:`Sourced from the USA`,title:`Find it. We source it.`,placeholder:`Search a make, model or part`,popularLabel:`Popular:`,popular:[`Leupold`,`Nightforce`,`MDT`,`TriggerTech`,`Proof Research`],steps:[{title:`Search`,body:`Every SKU from our US suppliers, as you type.`},{title:`Complete your setup`,body:`We suggest the parts that fit what you chose.`},{title:`Request a quote`,body:`One indicative price. We confirm and import it for you.`}],setupKicker:`Complete your setup`,optionWord:`option`,optionsWord:`options`,youChose:`You chose`,finePrint:`One indicative total per setup. We confirm stock, permits and the final quotation in your client space.`,missingTitle:`Not in our feeds yet? We can still get it.`,missingBody:`Paste the link to the product on any US store and we will quote it for you.`,linkPlaceholder:`https://…`,linkLabel:`Link`,requestQuote:`Request a quote`,quoteSetup:`Request a quote for this setup`,search:`Search`,complete:`Complete my setup`,quoteOnly:`Quote this only`,selected:`Selected`,explore:`Explore`,indicative:`Indicative`,setupIndicative:`Setup, indicative`,swap:`Swap`,noSwap:`No other part fits this role.`,back:`Back to setups`,askPlaceholder:`Ask a question… “Will this fit a weapon light too?”`,ask:`Ask`,whyLabel:`Why this setup`,partsFit:`all fit`,partWord:`part`,partsWord:`parts`,moreWord:`more`,fittedWord:`fitted`,showMore:`Show`,showLess:`Show fewer`,fits:`fits`,yourSetup:`Your setup`,quoteTitle:`Request a quote`,quoteName:`Name`,quoteEmail:`Email`,quotePhone:`Phone`,linkRequired:`Enter the product link.`,linkInvalid:`Enter a valid web link, starting with http:// or https://.`,nameRequired:`Enter your name.`,nameInvalid:`Use 120 characters or fewer for your name.`,emailRequired:`Enter your email address.`,emailInvalid:`Enter a valid email address, such as name@example.com.`,phoneRequired:`Enter your phone number.`,phoneInvalid:`Enter a valid phone number with 7 to 15 digits.`,quoteSend:`Send request`,quoteSent:`Request received. Reference`,quotePending:`We've got your request. The store will be in touch. Reference`,quoteNeedsPerson:`We've got your request. The store will be in touch. Reference`,graphIndicative:`indicative`,photoLabel:`Product photo (from feed)`,chosenPhotoLabel:`PHOTO`,loading:`Loading…`,noSetups:`No setups came back for this product. You can still quote it on its own.`,swappedNote:`Updated with a part that still fits.`,resultWord:`result`,resultsWord:`results`,forWord:`for`,mark:`RIG · by Tervory`,close:`Close`,notAlternative:`That part is not an alternative that fits.`,errorGeneric:`Something went wrong.`,unavailable:`That setup is no longer available.`}},M={id:`neutral`,colors:{canvas:`#f4f5f7`,ink:`#1c1e21`,accent:`#1d4e89`,muted:`#5c6370`,faint:`#8b919a`,line:`#d5d8de`,lineStrong:`#c5c9d1`,card:`#ffffff`,badge:`#e6e8ec`,onAccent:`#ffffff`,featured:`#1c1e21`,featuredText:`#f7f8fa`,featuredMuted:`#c5c9d1`},graph:{canvas:`#12141a`,raised:`#1b1e27`,text:`#f7f8fa`,muted:`#c5c9d1`,line:`rgba(255,255,255,0.14)`,chip:`#1b1e27`,chipOn:`#172033`,glow:`rgba(29,78,137,0.35)`},fonts:{heading:`Newsreader, Georgia, serif`,body:`"Source Sans 3", "Helvetica Neue", sans-serif`,href:`https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,600&family=Source+Sans+3:wght@400;500;600&display=swap`},radius:`10px`,radiusGraph:`16px`,copy:{eyebrow:`From specialist suppliers`,title:`Find it. Complete the kit.`,placeholder:`Search a make, model or part — e.g. Sony, 70-200, Peak Design`,popularLabel:`Popular:`,popular:[`Sony`,`Canon`,`Nikon`,`Peak Design`,`SmallRig`,`Godox`],steps:[{title:`Search`,body:`Every item from the suppliers, as you type.`},{title:`Complete your setup`,body:`Parts that fit what you chose.`},{title:`Request a quote`,body:`One indicative price. The shop confirms it.`}],setupKicker:`Complete your setup`,optionWord:`option`,optionsWord:`options`,youChose:`You chose`,finePrint:`One indicative total per setup. The shop confirms stock and the final quotation.`,missingTitle:`Not in the catalogue yet? We can still quote it.`,missingBody:`Paste a link to the product and your contact details, and the shop will quote it.`,linkPlaceholder:`https://…`,linkLabel:`Link`,requestQuote:`Request a quote`,quoteSetup:`Request a quote for this setup`,search:`Search`,complete:`Complete my setup`,quoteOnly:`Quote this only`,selected:`Selected`,explore:`Explore`,indicative:`Indicative`,setupIndicative:`Setup, indicative`,swap:`Swap`,noSwap:`No other part fits this role.`,back:`Back to setups`,askPlaceholder:`Ask a question… “Does this work with the rest of the kit?”`,ask:`Ask`,whyLabel:`Why this setup`,partsFit:`all fit`,partWord:`part`,partsWord:`parts`,moreWord:`more`,fittedWord:`fitted`,showMore:`Show`,showLess:`Show fewer`,fits:`fits`,yourSetup:`Your setup`,quoteTitle:`Request a quote`,quoteName:`Name`,quoteEmail:`Email`,quotePhone:`Phone`,linkRequired:`Enter the product link.`,linkInvalid:`Enter a valid web link, starting with http:// or https://.`,nameRequired:`Enter your name.`,nameInvalid:`Use 120 characters or fewer for your name.`,emailRequired:`Enter your email address.`,emailInvalid:`Enter a valid email address, such as name@example.com.`,phoneRequired:`Enter your phone number.`,phoneInvalid:`Enter a valid phone number with 7 to 15 digits.`,quoteSend:`Send request`,quoteSent:`Request received. Reference`,quotePending:`We've got your request. The store will be in touch. Reference`,quoteNeedsPerson:`We've got your request. The store will be in touch. Reference`,graphIndicative:`indicative`,photoLabel:`Photo`,chosenPhotoLabel:`Photo`,loading:`Loading…`,noSetups:`No setups came back for this product. You can still quote it on its own.`,swappedNote:`Updated with a part that still fits.`,resultWord:`result`,resultsWord:`results`,forWord:`for`,mark:`RIG · by Tervory`,close:`Close`,notAlternative:`That part is not an alternative that fits.`,errorGeneric:`Something went wrong.`,unavailable:`That setup is no longer available.`}};function N(e){if(e===`custom-nation`)return j;if(!e||e===`neutral`)return M;if(e.trim().startsWith(`{`))try{let t=JSON.parse(e);return{...M,...t,id:typeof t.id==`string`?t.id:`custom`,colors:{...M.colors,...t.colors},graph:{...M.graph,...t.graph},fonts:{...M.fonts,...t.fonts},copy:{...M.copy,...t.copy,popular:t.copy?.popular??M.copy.popular,steps:t.copy?.steps??M.copy.steps}}}catch{return M}return M}function P(e){return e.screen===`explore`?de(e):`<div class="shell">${F(e)}</div>${q(e)}`}function F(e){let t=e.screen===`home`?B(e):e.screen===`results`?V(e):e.screen===`missing`?U(e):G(e);return`${I(e,!1)}${L(e)}${R(e,`q`)}${z(e)}${t}`}function I(e,t){let n=e.theme.copy;return`<header class="${t?`explore-header`:`header`}">
    ${t?``:`<span class="dash" aria-hidden="true"></span>`}
    ${t?``:`<p class="eyebrow">${Z(n.eyebrow)}</p>`}
    <p class="${t?`explore-mark`:`mark`}">${Z(n.mark)}</p>
  </header>`}function L(e){return`<h1 class="title">${Z(e.theme.copy.title)}</h1>`}function R(e,t){let n=e.theme.copy;return`<form class="search" role="search">
    <label class="sr" for="${t}">${Z(n.search)}</label>
    <span class="search-icon" aria-hidden="true">⌕</span>
    <input id="${t}" type="search" name="q" autocomplete="off" enterkeyhint="search" placeholder="${Z(n.placeholder)}" value="${Z(e.query)}">
    <button class="btn btn-accent" type="submit">${Z(n.search)}</button>
  </form>`}function z(e){return e.message?`<p class="status" role="status">${Z(e.message)}</p>`:``}function B(e){let t=e.theme.copy,n=t.popular.map(e=>`<button type="button" class="chip" data-action="popular" data-q="${Z(e)}">${Z(e)}</button>`).join(``),r=t.steps.map((e,t)=>`<section class="step">
        <span class="step-no">${String(t+1).padStart(2,`0`)}</span>
        <h2>${Z(e.title)}</h2>
        <p>${Z(e.body)}</p>
      </section>`).join(``);return`<div class="popular"><span class="popular-label">${Z(t.popularLabel)}</span>${n}</div>
    <div class="steps">${r}</div>`}function V(e){let t=e.theme.copy,n=e.results.length===1?t.resultWord:t.resultsWord,r=e.results.map(t=>H(e,t)).join(``);return`<div class="meta-row">
      <strong>${e.results.length} ${Z(n)} ${Z(t.forWord)} “${Z(e.query)}”</strong>
    </div>
    <div class="cards">${r}</div>`}function H(e,t){let n=e.theme.copy,r=e.selectedId===t.id,i=r?`<button type="button" class="btn btn-selected" data-action="complete" data-id="${Z(t.id)}">${Z(n.selected)} ✓</button>`:`<button type="button" class="btn btn-ghost" data-action="complete" data-id="${Z(t.id)}">${Z(n.complete)}</button>`;return`<article class="card${r?` selected`:``}">
    ${Y(t,e,`photo-lg`)}
    <div class="brand-row"><p class="brand">${Z(t.brand)}</p></div>
    <h2 class="card-name">${Z(t.name)}</h2>
    <p class="summary">${Z(O(t))}</p>
    ${J(n.indicative,t.price,``)}
    <div class="actions">
      ${i}
      <button type="button" class="btn btn-ghost" data-action="quote-product" data-id="${Z(t.id)}">${Z(n.quoteOnly)}</button>
    </div>
  </article>`}function U(e){let t=e.theme.copy,n=e.linkRequestId?X(e.linkAck??{request_id:e.linkRequestId},e.theme):``,r=e.linkError?`<p class="status" role="status">${Z(e.linkError)}</p>`:``;return`<section class="missing">
    <h2>${Z(t.missingTitle)}</h2>
    <p class="missing-copy">${Z(t.missingBody)}</p>
    <form class="link-form" novalidate>
      ${W(e,`url`,t.linkLabel,`url`,e.linkUrl,`inputmode="url" maxlength="2048" placeholder="${Z(t.linkPlaceholder)}"`)}
      ${W(e,`name`,t.quoteName,`text`,e.linkContact?.name??``,`autocomplete="name" maxlength="120"`)}
      ${W(e,`email`,t.quoteEmail,`email`,e.linkContact?.email??``,`autocomplete="email" maxlength="254"`)}
      ${W(e,`phone`,t.quotePhone,`tel`,e.linkContact?.phone??``,`autocomplete="tel" inputmode="tel" maxlength="40"`)}
      <button class="btn btn-accent" type="submit" ${e.linkPending||e.linkRequestId||e.noticeReady===!1?`disabled`:``}>${Z(t.requestQuote)}</button>
      ${K(e)}
    </form>
    ${r}${n}
  </section>`}function W(e,t,n,r,i,a){let o=e.linkErrors?.[t],s=`link-${t}`;return`<div class="note">
    <label for="${s}">${Z(n)}</label>
    <input class="field" id="${s}" name="${t}" type="${r}" required ${a}
      ${e.linkPending||e.linkRequestId||e.noticeReady===!1?`disabled`:``}
      ${o?`aria-invalid="true" aria-describedby="${s}-error"`:``} value="${Z(i)}">
    ${o?`<p class="field-error" id="${s}-error">${Z(o)}</p>`:``}
  </div>`}function G(e){let t=e.theme.copy,n=e.product;if(!n)return``;let r=k(n),i=e.setups.map((t,n)=>le(e,t,n)).join(``),a=e.setups.length===0?`<p class="fine">${Z(t.noSetups)}</p>`:``;return`<section class="chosen">
      ${Y(n,e,`photo-sm`)}
      <div class="chosen-copy">
        <p class="you-chose">${Z(t.youChose)}</p>
        <h2>${Z(n.name)} · ${Z(ce(n))}</h2>
        ${r?`<p class="chosen-note">${Z(r)}</p>`:``}
      </div>
      <div class="chosen-side">
      </div>
    </section>
    ${e.setups.length?`<div class="kicker-row"><span class="kicker-bar" aria-hidden="true"></span><h2 class="kicker">${Z(t.setupKicker)} — ${e.setups.length} ${Z(e.setups.length===1?t.optionWord:t.optionsWord)}</h2><button type="button" class="text-btn explore-link" data-action="explore" data-id="${Z(n.id)}">${Z(t.explore)}</button></div>`:``}
    <div class="setups">${i}</div>
    ${a}
    ${e.setups.length?`<p class="fine">${Z(t.finePrint)}</p>`:``}`}function le(e,t,n){let r=e.theme.copy,i=!!t.badge,a=A(t.why),o=e.customizedTiers.includes(t.tier)?r.swappedNote:a[0]??``,s=t.parts.map(n=>ue(e,t,n)).join(``);return`<article class="setup${i?` featured`:``}">
    <div class="setup-head">
      <span class="setup-no">${String(n+1).padStart(2,`0`)}</span>
      <h3 class="setup-name">${Z(t.tier)}</h3>
      ${t.badge?`<span class="badge-hot">${Z(t.badge)}</span>`:``}
    </div>
    <p class="label">${Z(t.label)}</p>
    ${s}
    ${o?`<p class="fit-line">✓ ${Z(o.replace(/^(?:[✓✔]\s*)+/,``))}</p>`:``}
    ${J(r.setupIndicative,t.total,`price-lg`)}
    <button type="button" class="btn btn-accent quote-wide" data-action="quote-setup" data-tier="${Z(t.tier)}">${Z(r.quoteSetup)} →</button>
  </article>`}function ue(e,t,n){let r=e.theme.copy,i=n.product.id===e.selectedId,a=e.swap?.role===n.role&&e.swap.tier===t.tier,o=a?`<div class="options">${e.swap?.options.length?e.swap.options.map(e=>`<button type="button" class="option" data-action="pick-swap" data-tier="${Z(t.tier)}" data-id="${Z(e.id)}">${Z(e.name)}</button>`).join(``):`<p class="fit-line">${Z(r.noSwap)}</p>`}</div>`:``;return`<div class="part-block">
    <div class="part">
      ${Y(n.product,e,`photo-xs`)}
      <div class="part-copy">
        <span class="role">${Z(n.role)}</span>
        <span class="part-name">${Z(n.product.name)}</span>
      </div>
      ${i?``:`<button type="button" class="swap-btn" data-action="swap" data-tier="${Z(t.tier)}" data-role="${Z(n.role)}" aria-expanded="${a?`true`:`false`}">${Z(r.swap)} ⇄</button>`}
    </div>
    ${o}
  </div>`}function de(e){let t=e.theme.copy,n=e.graph,r=n?.centre??e.product,i=me(e),a=(n?.branches??[]).map(n=>{let r=n.products.some(e=>i?.parts.some(t=>t.product.id===e.id)),a=e.expanded.includes(n.category),o=e.expanded.includes(`more:${n.category}`),s=o?n.products:n.products.slice(0,4),c=a?s.map(e=>`<button type="button" class="g-chip${i?.parts.some(t=>t.product.id===e.id)??!1?` on`:``}" data-action="pick-graph" data-id="${Z(e.id)}">
                <span class="swatch" aria-hidden="true"></span>
                <span class="g-copy">
                  <span class="g-name">${Z(e.name)}</span>
                  <span class="g-meta"><span class="amount">${Z(D(e.price))}</span> · ${Z(t.fits)} ✓</span>
                </span>
              </button>`).join(``):``,l=n.category.toLowerCase()===`part`?n.count===1?t.partWord:t.partsWord:n.category,u=n.count-Math.min(4,n.products.length),d=`+ ${u} ${Z(t.moreWord)} ${Z(t.fittedWord)} ${Z(l.toLowerCase())}`,f=a&&u>0?n.products.length>4?`<button type="button" class="more text-btn" data-action="branch" data-category="${Z(`more:${n.category}`)}" aria-expanded="${o}">${d}</button>`:`<span class="more">${d}</span>`:``;return`<div class="branch-row">
        <button type="button" class="branch${r?` on`:``}" data-action="branch" data-category="${Z(n.category)}" aria-expanded="${a?`true`:`false`}">
          <span class="count">${n.count}</span>
          <span class="cat">${Z(l)}</span>
        </button>
        <div class="products">${c}${f}</div>
      </div>`}).join(``),o=r?O(r):``,s=r?`<div class="centre">
        <div class="orb-wrap">
          <span class="orb-label">${Z(t.youChose)}</span>
          <span class="orb" aria-hidden="true"></span>
        </div>
        <h2>${Z(r.name)}</h2>
        <p>${o?`${Z(o)} · `:``}${Z(t.indicative)} <span class="amount">${Z(D(r.price))}</span></p>
      </div>`:``;return`<div class="shell shell-explore"><div class="explore">
    <header class="explore-header">
      <h1><button type="button" data-action="back-setup">${Z(t.title)}<span class="sr"> ${Z(t.back)}</span></button></h1>
      <form class="explore-search" role="search">
        <label class="sr" for="q">${Z(t.search)}</label>
        <span aria-hidden="true">⌕</span>
        <input id="q" type="search" name="q" autocomplete="off" value="${Z(e.query)}" placeholder="${Z(t.placeholder)}">
      </form>
      <p class="explore-mark">${Z(t.mark)}</p>
    </header>
    ${z(e)}
    <div class="explore-grid">
      <div class="graph-canvas"><svg class="connectors" aria-hidden="true"></svg>${s}
      <div class="graph">${a}</div></div>
      <div class="side">${fe(e,i)}${pe(e)}</div>
    </div>
  </div></div>${q(e)}`}function fe(e,t){let n=e.theme.copy;if(!t)return`<aside class="panel"><p class="panel-body">${Z(n.noSetups)}</p></aside>`;let r=e.customizedTiers.includes(t.tier),i=A(t.why),a=r?n.swappedNote:i[1]??i[0]??t.label,o=r?n.swappedNote:i[2]??i[1]??i[0]??t.label,s=t.parts.map(e=>e.product.name).join(` + `),c=e.setups.map(e=>{let n=e.tier===t.tier;return`<button type="button" class="tier${n?` on`:``}" data-action="tier" data-tier="${Z(e.tier)}" aria-pressed="${n?`true`:`false`}">${Z(he(e))}</button>`}).join(``),l=t.parts.length===1?n.partWord:n.partsWord;return`<aside class="panel">
    <p class="panel-kicker">${Z(n.yourSetup)} · ${Z(t.tier)}</p>
    <h2>${Z(s)}</h2>
    <p class="panel-body">${Z(a)}</p>
    <div class="stats">
      <div class="stat"><strong>${t.parts.length}</strong><span>${Z(l)}, ${Z(n.partsFit)}</span></div>
      <div class="stat"><strong class="amount">${Z(D(t.total))}</strong><span>${Z(n.graphIndicative)}</span></div>
    </div>
    <div class="tier-row">${c}</div>
    <div class="why"><strong>✦ ${Z(n.whyLabel)}</strong><p>${Z(o)}</p></div>
    <button type="button" class="btn btn-accent quote-wide" data-action="quote-setup" data-tier="${Z(t.tier)}">${Z(n.quoteSetup)} →</button>
  </aside>`}function pe(e){let t=e.theme.copy;return`${e.askAnswer?`<div class="answer" role="status"><p>${Z(e.askAnswer)}</p>${e.askCites?`<span>${Z(e.askCites)}</span>`:``}</div>`:``}<form class="ask">
    <label class="sr" for="ask-q">${Z(t.ask)}</label>
    <textarea id="ask-q" name="question" rows="2" placeholder="${Z(t.askPlaceholder)}">${Z(e.askQuestion)}</textarea>
    <button class="ask-go" type="submit" aria-label="${Z(t.ask)}">→</button>
  </form>`}function K(e){let t=e.notice;return!t||!/^https:\/\//i.test(t.url)?`<div data-quote-privacy hidden></div>`:`<p data-quote-privacy><a href="${Z(t.url)}" target="_blank" rel="noopener noreferrer">${Z(t.text)}</a></p>`}function q(e){let t=e.quote;if(!t)return``;let n=e.theme.copy;return t.requestId?`<div class="backdrop">
      <div class="quote" role="dialog" aria-modal="true" aria-labelledby="quote-title">
        <h2 id="quote-title">${Z(n.quoteTitle)}</h2>
        ${X(t.ack??{request_id:t.requestId},e.theme)}
        ${t.totalLabel?`<p class="summary">${Z(t.totalLabel)}</p>`:``}
        <button type="button" class="btn btn-accent" data-action="close-quote">${Z(n.close)}</button>
      </div>
    </div>`:`<div class="backdrop">
    <form class="quote" role="dialog" aria-modal="true" aria-labelledby="quote-title">
      <h2 id="quote-title">${Z(n.quoteTitle)}</h2>
      <p>${Z(t.title)}</p>
      ${t.totalLabel?`<p class="price-row"><span class="price-cap">${Z(e.theme.copy.indicative)}</span><span class="price">${Z(t.totalLabel)}</span></p>`:``}
      <label>${Z(n.quoteName)}
        <input class="field" id="quote-name" name="name" autocomplete="name" required ${t.pending?`disabled`:``} value="${Z(t.name)}">
      </label>
      <label>${Z(n.quoteEmail)}
        <input class="field" id="quote-email" name="email" type="email" autocomplete="email" required ${t.pending?`disabled`:``} value="${Z(t.email)}">
      </label>
      ${t.error?`<p class="status" role="status">${Z(t.error)}</p>`:``}
      <button class="btn btn-accent" type="submit" ${t.pending||e.noticeReady===!1?`disabled`:``}>${Z(n.quoteSend)}</button>
      ${K(e)}
      <button type="button" class="text-btn" data-action="close-quote">${Z(n.close)}</button>
    </form>
  </div>`}function me(e){return e.setups.find(t=>t.tier===e.activeTier)??e.setups[0]??null}function he(e){return e.tier.charAt(0).toUpperCase()+e.tier.slice(1)}function J(e,t,n){return`<div class="price-row"><span class="price-cap">${Z(e)}</span><span class="price ${n}">${Z(D(t))}</span></div>`}function Y(e,t,n){let r=ge(e.image),i=n===`photo-sm`?t.theme.copy.chosenPhotoLabel:t.theme.copy.photoLabel;return`<div class="photo ${n}" aria-hidden="true">${r?`<img src="${Z(r)}" alt="">`:n===`photo-xs`?``:`<span>${Z(i)}</span>`}</div>`}function X(e,t){return`<p class="ack" role="status">${Z(e.delivery===`pending`||e.delivery===`sending`?t.copy.quotePending:e.delivery===`needs_person`?t.copy.quoteNeedsPerson:t.copy.quoteSent)} ${Z(e.request_id)}.</p>`}function ge(e){return/^https?:\/\//i.test(e)||e.startsWith(`data:image/`)?e:``}function Z(e){return e.replace(/[&<>"']/g,e=>{switch(e){case`&`:return`&amp;`;case`<`:return`&lt;`;case`>`:return`&gt;`;case`"`:return`&quot;`;default:return`&#39;`}})}var _e=`
:host {
  all: initial;
  display: block;
  width: 100%;
  max-width: 100%;
  overflow-x: hidden;
  color: var(--rig-ink, #242722);
  background: var(--rig-canvas, #f2efe7);
  font-family: var(--rig-font-body, Inter, sans-serif);
  font-size: 16px;
  line-height: 1.4;
  box-sizing: border-box;
}
:host(.is-explore) {
  background: var(--rig-graph-canvas, #121510);
  color: var(--rig-graph-text, #fcfaf4);
}
*, *::before, *::after { box-sizing: border-box; }
button, input, textarea { font: inherit; color: inherit; }
button { cursor: pointer; }
img { max-width: 100%; display: block; }
p, h1, h2, h3 { margin: 0; }
.sr {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
:focus-visible {
  outline: 2px solid var(--rig-accent);
  outline-offset: 2px;
}
.shell {
  width: min(1200px, 100%);
  margin: 0 auto;
  padding: 40px 48px 48px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}
.shell-explore {
  width: 100%;
  max-width: none;
  margin: 0;
  padding: 0;
  gap: 0;
}
.header, .brand-row, .meta-row, .price-row, .part, .actions, .tier-row, .link-row, .popular {
  display: flex;
  align-items: center;
}
.header { gap: 12px; }
.dash { width: 28px; height: 2px; background: var(--rig-accent); flex: none; }
.eyebrow {
  margin: 0;
  color: var(--rig-muted);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}
.mark {
  margin-left: auto;
  color: var(--rig-faint);
  font-size: 11px;
  font-weight: 500;
}
h1.title {
  font-family: var(--rig-font-heading);
  font-weight: 600;
  font-size: 56px;
  line-height: 1;
  letter-spacing: 0;
  text-transform: uppercase;
  color: var(--rig-ink);
}
.search {
  display: flex;
  align-items: center;
  gap: 14px;
  background: var(--rig-card);
  border: 2px solid var(--rig-ink);
  border-radius: var(--rig-radius);
  padding: 8px 8px 8px 22px;
}
.search-icon { color: var(--rig-muted); font-size: 22px; line-height: 1; }
.search input {
  flex: 1;
  min-width: 0;
  border: 0;
  background: transparent;
  font-size: 18px;
  padding: 8px 0;
  outline: none;
}
.search input::placeholder { color: var(--rig-faint); }
.search:focus-within {
  outline: 2px solid var(--rig-accent);
  outline-offset: 2px;
}
.btn {
  border-radius: var(--rig-radius);
  font-size: 13px;
  font-weight: 600;
  line-height: 1;
  padding: 14px 22px;
  white-space: nowrap;
}
.btn-accent {
  background: var(--rig-accent);
  color: var(--rig-on-accent);
  border: 2px solid var(--rig-accent);
}
.btn:disabled { opacity: 0.5; cursor: default; }
.btn-ghost {
  background: transparent;
  color: var(--rig-ink);
  border: 1px solid var(--rig-ink);
}
.btn-selected {
  background: var(--rig-accent);
  color: var(--rig-on-accent);
  border: 2px solid var(--rig-accent);
}
.text-btn {
  background: none;
  border: 0;
  padding: 0;
  color: var(--rig-muted);
  font-size: 13px;
  font-weight: 600;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.popular { flex-wrap: wrap; gap: 8px; }
.popular-label { color: var(--rig-muted); font-size: 12px; font-weight: 600; }
.chip {
  background: transparent;
  color: var(--rig-ink);
  border: 1px solid var(--rig-ink);
  border-radius: var(--rig-radius);
  padding: 7px 12px;
  font-size: 12px;
  font-weight: 500;
}
.steps {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.step {
  border-top: 2px solid var(--rig-accent);
  padding-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.step-no {
  font-family: var(--rig-font-heading);
  font-size: 24px;
  font-weight: 600;
  color: var(--rig-accent);
  line-height: 1;
}
.step h2, .kicker, .card-name, .setup-name, .panel h2, .centre h2, .missing h2, .quote h2 {
  font-family: var(--rig-font-heading);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0;
}
.step h2 { font-size: 22px; line-height: 1.15; }
.step p, .fine, .missing-copy, .label, .summary, .fit-line { color: var(--rig-muted); }
.step p { font-size: 14px; }
.meta-row { gap: 10px; flex-wrap: wrap; }
.meta-row strong { font-size: 13px; }
.cards, .setups {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.card, .setup {
  background: var(--rig-card);
  border: 2px solid transparent;
  border-radius: var(--rig-radius);
  padding: 16px 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}
.card.selected { border-color: var(--rig-accent); }
.photo {
  background: var(--rig-card);
  border: 1px solid var(--rig-line);
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: var(--rig-faint);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  overflow: hidden;
}
.photo-lg { height: 200px; width: 100%; }
.photo-sm { width: 120px; height: 80px; flex: none; }
.photo-xs { width: 64px; height: 48px; flex: none; }
.photo img { width: 100%; height: 100%; object-fit: contain; }
.brand-row { justify-content: space-between; gap: 8px; }
.brand {
  color: var(--rig-accent);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}
.card-name { font-size: 28px; line-height: 1; }
.summary { font-size: 13px; }
.price-row { gap: 8px; align-items: baseline; flex-wrap: wrap; }
.price-cap {
  color: var(--rig-muted);
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
}
.amount, .price { white-space: nowrap; }
.price {
  font-family: var(--rig-font-heading);
  font-weight: 600;
  font-size: 30px;
  line-height: 1;
}
.price-lg { font-size: 36px; }
.actions { gap: 8px; flex-wrap: wrap; }
.chosen {
  background: var(--rig-card);
  border-radius: var(--rig-radius);
  padding: 14px 16px;
  display: flex;
  gap: 20px;
  align-items: center;
}
.chosen-copy { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.you-chose {
  color: var(--rig-muted);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}
.chosen h2 {
  font-family: var(--rig-font-heading);
  font-size: 26px;
  font-weight: 600;
  text-transform: uppercase;
  line-height: 1.05;
}
.chosen-note { color: var(--rig-muted); font-size: 13px; }
.chosen-side { display: flex; flex-direction: column; align-items: flex-end; gap: 8px; }
.explore-link { margin-left: auto; font-weight: 500; flex: none; }
.kicker-row { display: flex; align-items: center; gap: 12px; }
.kicker-bar { width: 4px; height: 28px; background: var(--rig-accent); flex: none; }
.kicker { font-size: 32px; line-height: 1; }
.setup { padding: 20px 20px 22px; gap: 12px; }
.setup.featured {
  background: var(--rig-featured);
  color: var(--rig-featured-text);
}
.setup.featured .label,
.setup.featured .fit-line,
.setup.featured .price-cap,
.setup.featured .swap-btn { color: var(--rig-featured-muted); }
.setup-head { display: flex; align-items: center; gap: 8px; }
.setup-no {
  font-family: var(--rig-font-heading);
  font-size: 22px;
  font-weight: 600;
  color: var(--rig-accent);
}
.setup-name { font-size: 26px; line-height: 1; }
.setup-head .badge-hot,
.badge-hot {
  background: var(--rig-accent);
  color: var(--rig-on-accent);
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  padding: 4px 7px;
  border-radius: var(--rig-radius);
}
.label { font-size: 13px; }
.part {
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid var(--rig-line);
}
.setup.featured .part { border-bottom-color: rgba(252, 250, 244, 0.15); }
.part-copy { flex: 1; min-width: 0; display: flex; flex-direction: column; align-items: flex-start; gap: 2px; }
.role {
  color: var(--rig-accent);
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.part-name { font-size: 14px; font-weight: 500; }
.swap-btn {
  background: none;
  border: 0;
  color: var(--rig-muted);
  font-size: 11px;
  font-weight: 600;
  padding: 4px;
  white-space: nowrap;
}
.fit-line { font-size: 11px; }
.quote-wide { width: 100%; }
.fine { font-size: 12px; }
.options {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-bottom: 8px;
}
.option {
  text-align: left;
  background: transparent;
  border: 1px solid var(--rig-line-strong);
  border-radius: var(--rig-radius);
  padding: 8px 10px;
  font-size: 13px;
  font-weight: 500;
}
.setup.featured .option { border-color: rgba(252, 250, 244, 0.3); color: var(--rig-featured-text); }
.missing {
  background: var(--rig-card);
  border-radius: var(--rig-radius);
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.missing h2 { font-size: 30px; line-height: 1.05; }
.missing-copy { font-size: 15px; }
.field, .link-url {
  width: 100%;
  background: var(--rig-card);
  border: 1px solid var(--rig-line-strong);
  border-radius: var(--rig-radius);
  padding: 13px 14px;
  font-size: 14px;
  outline: none;
}
.field::placeholder, .link-url::placeholder { color: var(--rig-faint); }
.field:focus,
.field:focus-visible,
.link-url:focus,
.link-url:focus-visible {
  outline: 2px solid var(--rig-accent);
  outline-offset: 2px;
}
.link-row { gap: 10px; align-items: center; }
.link-url { flex: 1; min-width: 0; }
.note { display: flex; flex-direction: column; gap: 6px; }
.note > label { font-size: 12px; font-weight: 600; color: var(--rig-muted); }
.field-error { color: var(--rig-accent); font-size: 12px; }
.field[aria-invalid="true"] { border-color: var(--rig-accent); }
.link-form { display: flex; flex-direction: column; gap: 14px; }
.link-form > .btn { align-self: flex-start; }
.status { color: var(--rig-accent); font-size: 14px; }
.ack { color: var(--rig-ink); font-size: 14px; font-weight: 600; }
.backdrop {
  position: fixed;
  inset: 0;
  background: rgba(36, 39, 34, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  z-index: 5;
}
.quote {
  width: min(420px, 100%);
  background: var(--rig-card);
  color: var(--rig-ink);
  border-radius: var(--rig-radius);
  padding: 22px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.quote h2 { font-size: 28px; }
.quote label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; font-weight: 600; }
.explore { position: relative; min-height: 640px; }
.explore-header {
  display: flex;
  align-items: center;
  gap: 16px;
  min-height: 56px;
  padding: 10px 32px;
  background: color-mix(in srgb, var(--rig-graph-canvas) 60%, transparent);
}
.explore-header h1 {
  font-family: var(--rig-font-heading);
  font-size: 22px;
  font-weight: 600;
  text-transform: uppercase;
  white-space: nowrap;
}
.explore-header h1 button { font: inherit; color: inherit; text-transform: inherit; text-align: inherit; background: none; border: 0; padding: 0; }
.explore-search {
  margin: 0 auto;
  width: min(420px, 100%);
  display: flex;
  align-items: center;
  gap: 8px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid var(--rig-graph-line);
  border-radius: 999px;
  padding: 6px 8px 6px 14px;
}
.explore-search input {
  flex: 1;
  min-width: 0;
  border: 0;
  background: transparent;
  color: var(--rig-graph-text);
  font-size: 14px;
  outline: none;
}
.explore-search input::placeholder { color: var(--rig-graph-muted); }
.explore-search:focus-within {
  outline: 2px solid var(--rig-accent);
  outline-offset: 2px;
}
.explore-mark { color: var(--rig-graph-muted); font-size: 11px; font-weight: 500; white-space: nowrap; }
.back {
  background: none;
  border: 0;
  color: var(--rig-graph-muted);
  font-size: 13px;
  font-weight: 600;
  padding: 0;
}
.explore-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(300px, 380px);
  gap: 16px 20px;
  padding: 28px 32px 24px;
  align-items: start;
  min-height: 720px;
}
.graph-canvas { position: relative; display: grid; grid-template-columns: minmax(150px, 220px) minmax(0, 1fr); gap: 36px; min-width: 0; align-items: center; }
.connectors { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; overflow: visible; }
.connectors path.on { stroke: var(--rig-accent); }
.connectors path { fill: none; stroke: var(--rig-graph-line); stroke-width: 1.5; }
.graph-canvas .centre, .graph-canvas .graph { position: relative; }
.graph { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.branch-row {
  display: grid;
  grid-template-columns: 170px minmax(0, 280px);
  gap: 28px;
  align-items: center;
}
.centre { display: flex; flex-direction: column; align-items: center; text-align: center; }
.orb-wrap { position: relative; width: 220px; height: 220px; display: grid; place-items: center; }
.orb {
  width: 84px;
  height: 84px;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 35%, color-mix(in srgb, var(--rig-accent) 65%, white), var(--rig-accent) 58%, color-mix(in srgb, var(--rig-accent) 35%, black));
  box-shadow:
    0 0 0 22px color-mix(in srgb, var(--rig-accent) 28%, transparent),
    0 0 0 48px color-mix(in srgb, var(--rig-accent) 14%, transparent),
    0 0 0 78px color-mix(in srgb, var(--rig-accent) 7%, transparent),
    0 0 40px color-mix(in srgb, var(--rig-accent) 50%, transparent);
}
.orb-label {
  position: absolute;
  top: 18px;
  color: var(--rig-accent);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}
.centre h2 { font-size: 26px; margin-top: 8px; color: var(--rig-graph-text); }
.centre p { color: var(--rig-graph-muted); font-size: 11px; }
.branches, .products { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.branch, .g-chip {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  text-align: left;
  background: var(--rig-graph-chip);
  color: var(--rig-graph-text);
  border: 1px solid var(--rig-graph-line);
  border-radius: var(--rig-radius-graph);
  padding: 6px 10px;
}
.branch.on, .g-chip.on {
  background: var(--rig-graph-chip-on);
  border-color: var(--rig-accent);
}
.branch .count {
  font-family: var(--rig-font-heading);
  font-size: 24px;
  font-weight: 600;
  line-height: 1;
  min-width: 1.4em;
}
.branch .cat {
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  white-space: nowrap;
  color: var(--rig-graph-muted);
}
.swatch {
  width: 30px;
  height: 30px;
  border-radius: 4px;
  background: var(--rig-card);
  flex: none;
}
.g-copy { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.g-name { font-size: 12px; font-weight: 500; }
.g-meta { font-size: 10px; color: var(--rig-graph-muted); }
.more { color: var(--rig-graph-muted); font-size: 11px; margin: 0 0 8px; }
.side { display: flex; flex-direction: column; gap: 16px; min-height: 100%; }
.panel {
  background: color-mix(in srgb, var(--rig-graph-raised) 92%, transparent);
  border: 1px solid var(--rig-graph-line);
  border-radius: calc(var(--rig-radius-graph) + 4px);
  padding: 22px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.panel-kicker {
  color: var(--rig-accent);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}
.panel h2 {
  font-size: 26px;
  line-height: 1.05;
  text-transform: none;
  color: var(--rig-graph-text);
}
.panel-body { color: var(--rig-graph-muted); font-size: 13px; line-height: 1.5; }
.stats { display: grid; grid-template-columns: repeat(2, minmax(max-content, 1fr)); gap: 10px; }
.stat {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: var(--rig-radius-graph);
  padding: 10px 12px;
}
.stat strong {
  display: block;
  font-family: var(--rig-font-heading);
  font-size: 26px;
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
}
.stat span { color: var(--rig-graph-muted); font-size: 11px; }
.tier-row { gap: 6px; flex-wrap: wrap; }
.tier {
  background: transparent;
  color: var(--rig-graph-text);
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 999px;
  padding: 7px 12px;
  font-size: 11px;
  font-weight: 600;
}
.tier.on { background: var(--rig-accent); border-color: transparent; }
.why {
  background: rgba(255, 255, 255, 0.04);
  border-radius: var(--rig-radius-graph);
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.why strong { font-size: 12px; }
.why p { color: var(--rig-graph-muted); font-size: 12px; }
.panel .btn { border-radius: var(--rig-radius-graph); }
.ask {
  display: flex;
  align-items: center;
  gap: 8px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 999px;
  padding: 8px 8px 8px 16px;
  margin-top: auto;
}
.ask textarea {
  flex: 1;
  min-width: 0;
  border: 0;
  background: transparent;
  color: var(--rig-graph-text);
  font-size: 13px;
  line-height: 1.5;
  resize: none;
  outline: none;
}
.ask textarea::placeholder { color: var(--rig-graph-muted); }
.ask:focus-within {
  outline: 2px solid var(--rig-accent);
  outline-offset: 2px;
}
.ask-go {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: 0;
  background: var(--rig-accent);
  color: var(--rig-on-accent);
  font-weight: 600;
  flex: none;
}
.answer { color: var(--rig-graph-text); font-size: 13px; display: flex; flex-direction: column; gap: 4px; }
.answer span { color: var(--rig-graph-muted); font-size: 11px; }
@media (max-width: 1100px) {
  .explore-grid { grid-template-columns: 1fr; min-height: 0; padding: 16px; }
  .graph-canvas { grid-template-columns: 1fr; gap: 40px; }
  .graph { gap: 24px; }
  .branch-row { grid-template-columns: 160px minmax(0, 1fr); gap: 20px; padding-left: 12px; }
  .explore-header { flex-wrap: wrap; padding: 12px 16px; }
  .explore-header h1 { font-size: 18px; white-space: normal; }
  .explore > .status { padding: 0 16px; }
  .explore-search { order: 3; width: 100%; margin: 0; }
  .centre { padding-top: 8px; }
  .side { min-height: 0; }
  .ask { margin-top: 0; }
}
@media (max-width: 720px) {
  .shell { padding: 20px 16px 28px; gap: 18px; }
  .shell-explore { padding: 0; gap: 0; }
  h1.title { font-size: 34px; }
  .steps, .cards, .setups { grid-template-columns: 1fr; }
  .search { padding-left: 12px; gap: 8px; }
  .search input { font-size: 16px; }
  .actions { flex-direction: column; align-items: stretch; }
  .actions .btn { width: 100%; text-align: center; }
  .chosen { flex-wrap: wrap; }
  .chosen-side { flex-direction: row; align-items: center; width: 100%; justify-content: space-between; }
  .kicker { font-size: 26px; }
  .link-row { flex-direction: column; align-items: stretch; }
  .price-lg { font-size: 32px; }
  .card-name { overflow-wrap: anywhere; }
}
@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; }
}
`;_();var ve=0,Q=class extends HTMLElement{static get observedAttributes(){return[`api-base`,`rig-key`,`theme`,`fonts`,`kiosk-id`]}threeActive=!1;threeGeneration=0;threeDispose=null;threeEntitled=!1;cartController=null;client=new T({apiBase:``,key:``});notice=null;noticeReady=!1;themeOverride=null;root=null;searchTimer=0;settleTimer=0;countedSearchQuery=``;pendingSearch=null;loadGen=0;pendingFocus=null;quoteOpener=null;focusOwed=!1;graphObserver=new ResizeObserver(()=>this.drawConnectors());historyKey=`__tervoryRig${++ve}`;historyViews=new Map;historyEntry=0;nextHistoryEntry=0;onPopState=e=>{let t=e.state?.[this.historyKey];typeof t==`number`&&t!==this.historyEntry&&this.restoreView(t)};query=``;screen=`home`;results=[];selectedId=null;product=null;setups=[];activeTier=null;graph=null;expanded=[];swap=null;askQuestion=``;askAnswer=null;askCites=``;askPending=!1;linkUrl=``;linkRequestId=null;linkError=null;linkErrors={};linkContact={name:``,email:``};linkAck;linkPending=!1;quote=null;quoteTarget=null;message=null;customizedTiers=[];set themeData(e){this.themeOverride=e,this.shadowRoot&&(this.applyTheme(),this.render())}connectedCallback(){if(window.addEventListener(`popstate`,this.onPopState),this.shadowRoot)return;let e=this.attachShadow({mode:`open`}),t=document.createElement(`style`);t.textContent=_e+te,e.append(t),this.root=document.createElement(`div`),e.append(this.root),e.addEventListener(`click`,e=>this.onClick(e)),e.addEventListener(`submit`,e=>this.onSubmit(e)),e.addEventListener(`change`,e=>{let t=e.target;t instanceof HTMLInputElement&&t.dataset.cartQuantity&&this.cartController?.quantity(t.dataset.cartQuantity,Number(t.value))}),e.addEventListener(`input`,e=>this.onInput(e)),e.addEventListener(`keydown`,e=>this.onKey(e)),this.syncClient(),this.applyTheme(),this.render()}disconnectedCallback(){window.removeEventListener(`popstate`,this.onPopState),window.clearTimeout(this.searchTimer),window.clearTimeout(this.settleTimer),this.graphObserver.disconnect(),this.cartController?.stop(),this.stopThree()}attributeChangedCallback(e){this.shadowRoot&&(this.stopThree(),(e===`api-base`||e===`rig-key`)&&this.syncClient(),e===`theme`&&(this.themeOverride=null),this.applyTheme(),this.render())}syncClient(){this.stopThree(),this.threeEntitled=!1;let e=this.getAttribute(`api-base`);this.notice=null,this.noticeReady=!1,this.client=new T({apiBase:e===null?``:e.trim(),key:this.getAttribute(`rig-key`)??``});let t=this.threeGeneration;fetch((e?.trim()||``).replace(/\/$/,``)+`/v1/rig/3d/capabilities`,{credentials:`omit`,cache:`no-store`,headers:{"X-Rig-Key":this.getAttribute(`rig-key`)||``}}).then(async e=>{if(!e.ok)return;let n=await e.json();t===this.threeGeneration&&(this.threeEntitled=n.rig_3d===!0,this.render())}).catch(()=>{}),this.refreshNotice(),this.cartController?.stop();let n=this.client;this.cartController=new h((e,t)=>n.cart(e,t),()=>this.render(),`rig-cart:${this.getAttribute(`rig-key`)||``}`),this.cartController.start()}refreshNotice(){this.noticeReady=!1;let e=this.client;e.privacyNotice().then(t=>{e===this.client&&(this.notice=t,this.noticeReady=!0,this.render())}).catch(t=>{e===this.client&&(this.message=this.errorText(t),this.render())})}currentTheme(){return this.themeOverride??N(this.getAttribute(`theme`))}applyTheme(){let e=this.currentTheme(),t={"--rig-canvas":e.colors.canvas,"--rig-ink":e.colors.ink,"--rig-accent":e.colors.accent,"--rig-muted":e.colors.muted,"--rig-faint":e.colors.faint,"--rig-line":e.colors.line,"--rig-line-strong":e.colors.lineStrong,"--rig-card":e.colors.card,"--rig-badge":e.colors.badge,"--rig-on-accent":e.colors.onAccent,"--rig-featured":e.colors.featured,"--rig-featured-text":e.colors.featuredText,"--rig-featured-muted":e.colors.featuredMuted,"--rig-font-heading":e.fonts.heading,"--rig-font-body":e.fonts.body,"--rig-radius":e.radius,"--rig-radius-graph":e.radiusGraph,"--rig-graph-canvas":e.graph.canvas,"--rig-graph-raised":e.graph.raised,"--rig-graph-text":e.graph.text,"--rig-graph-muted":e.graph.muted,"--rig-graph-line":e.graph.line,"--rig-graph-chip":e.graph.chip,"--rig-graph-chip-on":e.graph.chipOn,"--rig-graph-glow":e.graph.glow};for(let[e,n]of Object.entries(t))this.style.setProperty(e,n);let n=this.getAttribute(`fonts`),r=n&&n!==`on`&&n!==`off`?n:e.fonts.href;n!==`off`&&r&&ye(r)}view(){return{theme:this.currentTheme(),notice:this.notice,noticeReady:this.noticeReady,query:this.query,screen:this.screen,results:this.results,selectedId:this.selectedId,product:this.product,setups:this.setups,activeTier:this.activeTier,graph:this.graph,expanded:this.expanded,swap:this.swap,askQuestion:this.askQuestion,askAnswer:this.askAnswer,askCites:this.askCites,linkUrl:this.linkUrl,linkRequestId:this.linkRequestId,linkError:this.linkError,linkErrors:this.linkErrors,linkContact:this.linkContact,linkAck:this.linkAck,linkPending:this.linkPending,quote:this.quote,message:this.message,customizedTiers:this.customizedTiers}}render(){if(!this.root||this.threeActive)return;this.rememberView();let e=this.captureFocus(),t=this.pendingFocus;this.pendingFocus=null;let n=e!==null||t!==null||this.focusOwed;if(this.classList.toggle(`is-explore`,this.screen===`explore`),this.root.innerHTML=this.cartController?.render()||P(this.view()),this.threeEntitled&&this.product&&this.setups.length&&!this.quote){let e=document.createElement(`div`);e.className=`rig-three-entry`;for(let[t,n]of[[`open-three`,`Open 3D`],[`open-kiosk`,`Open terminal`]]){if(t===`open-kiosk`&&!/^[0-9a-f-]{36}$/.test(this.getAttribute(`kiosk-id`)||``))continue;let r=document.createElement(`button`);r.type=`button`,r.className=`btn`,r.dataset.action=t,r.textContent=n,e.append(r)}this.root.append(e)}this.graphObserver.disconnect();let r=this.root.querySelector(`.graph-canvas`);r&&(this.graphObserver.observe(r),this.drawConnectors());let i=this.restoreFocus(t??e);!i&&t&&e&&(i=this.restoreFocus(e)),!i&&n&&(i=this.focusFallback()),this.focusOwed=n&&!i}rememberView(){let e={screen:this.screen,query:this.query,results:this.results,selectedId:this.selectedId,product:this.product,setups:[...this.setups],activeTier:this.activeTier,graph:this.graph,expanded:[...this.expanded],customizedTiers:[...this.customizedTiers],askQuestion:this.askQuestion,askAnswer:this.askAnswer,askCites:this.askCites,linkUrl:this.linkUrl,linkRequestId:this.linkRequestId,linkError:this.linkError,linkErrors:this.linkErrors,linkContact:this.linkContact,linkAck:this.linkAck,linkPending:this.linkPending},t=this.historyViews.get(this.historyEntry);if(t?.view.screen===e.screen&&t.view.selectedId===e.selectedId){t.view=e;return}if(t&&e.screen!==`setup`&&e.screen!==`explore`){t.view=e,t.focus=null,this.writeHistory(!1);return}t&&(t.focus=this.captureFocus());let n=t?this.historyEntry:null;this.historyEntry=++this.nextHistoryEntry,this.historyViews.set(this.historyEntry,{view:e,previous:n,focus:null}),this.writeHistory(!!t)}writeHistory(e){let t=window.history.state;if(t===null||typeof t==`object`&&!Array.isArray(t))try{let n={...t,[this.historyKey]:this.historyEntry};e?window.history.pushState(n,``):window.history.replaceState(n,``)}catch{}}restoreView(e){this.stopThree();let t=this.historyViews.get(e);if(!t)return;this.rememberView();let n=this.historyViews.get(this.historyEntry);n&&(n.focus=this.captureFocus()),this.cancelSearch(),this.historyEntry=e,Object.assign(this,t.view),this.setups=[...t.view.setups],this.customizedTiers=[...t.view.customizedTiers],this.swap=null,this.quote=null,this.quoteTarget=null,this.askPending=!1,this.message=null,this.pendingFocus=t.focus??{kind:`fallback`},this.render(),this.selectedId&&this.screen===`explore`&&!this.graph&&this.openExplore(this.selectedId),this.selectedId&&this.screen===`setup`&&!this.setups.length&&this.openSetup(this.selectedId)}backToSetups(){let e=this.historyViews.get(this.historyEntry)?.previous;e&&this.historyViews.get(e)?.view.screen===`setup`?window.history.state?.[this.historyKey]===this.historyEntry?window.history.back():(this.restoreView(e),this.writeHistory(!1)):this.selectedId&&this.openSetup(this.selectedId)}drawConnectors(){let e=this.root?.querySelector(`.graph-canvas`),t=e?.querySelector(`svg`),n=e?.querySelector(`.orb`);if(!e||!t||!n)return;let r=e.getBoundingClientRect(),i=[],a=[],o=(e,t,n,o=!1)=>{let s=e.getBoundingClientRect(),c=t.getBoundingClientRect(),l=(o?s.left+s.width/2:s.right)-r.left,u=(o?s.bottom:s.top+s.height/2)-r.top,d=(o?c.left+c.width/2:c.left)-r.left,f=(o?c.top:c.top+c.height/2)-r.top,p=o?(u+f)/2:(l+d)/2,m=o?`M${l},${u} V${u+16} H4 V${f+c.height/2} H${d-c.width/2}`:`M${l},${u} C${p},${u} ${p},${f} ${d},${f}`;(n?a:i).push(`<path${n?` class="on"`:``} d="${m}"/>`)};for(let t of e.querySelectorAll(`.branch-row`)){let r=t.querySelector(`.branch`);if(!r)continue;let i=getComputedStyle(e).gridTemplateColumns.split(` `).length===1;o(i?e.querySelector(`.centre`)??n:n,r,r.classList.contains(`on`),i);for(let e of t.querySelectorAll(`.g-chip`))o(r,e,e.classList.contains(`on`))}t.setAttribute(`viewBox`,`0 0 ${r.width} ${r.height}`),t.innerHTML=[...i,...a].join(``)}focusFallback(){if(!this.root)return!1;let e=this.root.querySelector(`[role='dialog'] h2`)??this.root.querySelector(`h2`);return e?(e.setAttribute(`tabindex`,`-1`),e.dataset.focusFallback=``,e.focus(),this.shadowRoot?.activeElement===e):!1}captureFocus(){let e=this.shadowRoot?.activeElement;if(!(e instanceof HTMLElement))return null;if(e.id&&(e instanceof HTMLInputElement||e instanceof HTMLTextAreaElement))return{kind:`id`,id:e.id,start:e.selectionStart,end:e.selectionEnd};let t=e.closest(`[data-action]`);return t instanceof HTMLElement&&t.dataset.action?{kind:`action`,action:t.dataset.action,id:t.dataset.id??``,tier:t.dataset.tier??``,role:t.dataset.role??``,category:t.dataset.category??``,q:t.dataset.q??``}:e.id?{kind:`id`,id:e.id,start:null,end:null}:e.hasAttribute(`data-focus-fallback`)?{kind:`fallback`}:null}restoreFocus(e){if(!e||!this.root||e.kind===`fallback`)return!1;if(e.kind===`id`){let t=this.root.querySelector(`#${CSS.escape(e.id)}`);return t instanceof HTMLElement&&(t.focus(),(t instanceof HTMLInputElement||t instanceof HTMLTextAreaElement)&&e.start!==null&&e.end!==null&&t.setSelectionRange(e.start,e.end),!0)}let t=this.root.querySelectorAll(`[data-action]`);for(let n of t)if((n.dataset.action??``)===e.action&&(n.dataset.id??``)===e.id&&(n.dataset.tier??``)===e.tier&&(n.dataset.role??``)===e.role&&(n.dataset.category??``)===e.category&&(n.dataset.q??``)===e.q)return n.focus(),!0;return!1}swapButtonKey(e,t){return{kind:`action`,action:`swap`,id:``,tier:e,role:t,category:``,q:``}}stopThree(){++this.threeGeneration,this.threeDispose?.(),this.threeDispose=null,this.threeActive=!1}async openThree(e){let t=this.setups.find(e=>e.tier===this.activeTier)??this.setups[0];if(!this.root||!this.product||!t||!this.threeEntitled)return;this.stopThree(),this.threeActive=!0;let n=this.threeGeneration,r=(this.getAttribute(`api-base`)||``).trim().replace(/\/$/,``),i=document.createElement(`div`);this.root.replaceChildren(i);let a=()=>{this.stopThree(),this.render()};try{let o=await import(new URL(r+`/3d/widget-entry.js`,location.href).href);if(n!==this.threeGeneration||!this.isConnected)return;let s=await o.mount(i,r,this.getAttribute(`rig-key`)||``,{product_id:this.product.id,tier:t.tier,choices:Object.fromEntries(t.parts.filter(e=>e.product.id!==this.product.id).map(e=>[e.role,e.product.id]))},e?this.getAttribute(`kiosk-id`):null,{storeName:this.currentTheme().id===`custom-nation`?`Custom Nation`:this.currentTheme().copy.mark,productName:this.product.name,finePrint:this.currentTheme().copy.finePrint,back:a,menu:()=>this.dispatchEvent(new CustomEvent(`rig-menu`,{bubbles:!0,composed:!0})),quote:()=>{a(),this.openQuoteSetup(t.tier)}});n!==this.threeGeneration||!this.isConnected?s():this.threeDispose=s}catch{n===this.threeGeneration&&(a(),this.message=`The 3D view could not load. Please use the product photos.`,this.render())}}onClick(e){let t=e.target;if(!(t instanceof Element))return;let n=t.closest(`[data-action]`);if(!(n instanceof HTMLElement))return;if(this.cartController?.enabled&&n.dataset.action?.startsWith(`cart-`)){e.preventDefault(),this.cartController.action(n.dataset.action.slice(5),n.dataset.id);return}if(this.cartController?.enabled&&[`complete`,`quote-product`,`pick-graph`].includes(n.dataset.action||``)){e.preventDefault(),this.cartController.preview(n.dataset.id||``);return}let r=n.dataset.id??``,i=n.dataset.tier??``;switch(n.dataset.action){case`open-three`:case`open-kiosk`:e.preventDefault(),this.openThree(n.dataset.action===`open-kiosk`);break;case`back-setup`:e.preventDefault(),this.backToSetups();break;case`popular`:this.query=n.dataset.q??``,this.searchNow();break;case`complete`:this.settleSearch(),this.openSetup(r);break;case`explore`:this.openExplore(r);break;case`quote-product`:this.settleSearch(),this.openQuoteProduct(r);break;case`quote-setup`:this.openQuoteSetup(i);break;case`swap`:this.openSwap(i,n.dataset.role??``);break;case`pick-swap`:this.pickSwap(i,r);break;case`tier`:this.activeTier=i,this.swap=null,this.render();break;case`branch`:this.toggleBranch(n.dataset.category??``);break;case`pick-graph`:this.pickGraph(r);break;case`close-quote`:this.closeQuote()}}onSubmit(e){if(this.threeActive)return;e.preventDefault();let t=e.target;t instanceof HTMLFormElement&&(t.classList.contains(`link-form`)?this.submitLink(t):t.classList.contains(`ask`)?this.submitAsk():t.classList.contains(`quote`)?this.submitQuote(t):this.searchNow())}onInput(e){if(this.threeActive)return;let t=e.target;if(!(t instanceof HTMLInputElement||t instanceof HTMLTextAreaElement))return;t.id===`q`?(this.query=t.value,this.scheduleSearch()):t.id===`link-url`?this.linkUrl=t.value:t.id===`link-name`?this.linkContact.name=t.value:t.id===`link-email`?this.linkContact.email=t.value:t.id===`link-phone`?this.linkContact.phone=t.value:t.id===`ask-q`?this.askQuestion=t.value:t.id===`quote-name`&&this.quote?this.quote={...this.quote,name:t.value}:t.id===`quote-email`&&this.quote&&(this.quote={...this.quote,email:t.value});let n=t.id.replace(/^link-/,``);if(t instanceof HTMLInputElement&&this.linkErrors[n]){let e=this.linkFieldError(n,t);e?this.linkErrors[n]=e:delete this.linkErrors[n],this.render()}}onKey(e){this.threeActive?e instanceof KeyboardEvent&&e.key===`Escape`&&(this.stopThree(),this.render()):e instanceof KeyboardEvent&&(e.key===`Enter`&&!e.shiftKey&&!e.isComposing&&e.target instanceof HTMLTextAreaElement&&e.target.id===`ask-q`?(e.preventDefault(),e.repeat||e.target.form?.requestSubmit()):e.key===`Tab`&&this.quote?this.trapQuoteFocus(e):e.key===`Escape`&&(this.quote?this.closeQuote():this.swap?(this.pendingFocus=this.swapButtonKey(this.swap.tier,this.swap.role),this.swap=null,this.render()):this.screen===`explore`&&(e.preventDefault(),this.backToSetups())))}trapQuoteFocus(e){let t=this.root?.querySelector(`[role='dialog']`);if(!(t instanceof HTMLElement))return;let n=[...t.querySelectorAll(`button, input, textarea, select, a[href]`)].filter(e=>!e.hasAttribute(`disabled`));if(n.length===0)return;let r=n.findIndex(e=>e===this.shadowRoot?.activeElement);e.preventDefault(),r===-1?(e.shiftKey?n[n.length-1]:n[0])?.focus():n[(r+(e.shiftKey?-1:1)+n.length)%n.length]?.focus()}closeQuote(){this.pendingFocus=this.quoteOpener,this.quoteOpener=null,this.quote=null,this.quoteTarget=null,this.render()}scheduleSearch(){window.clearTimeout(this.searchTimer),window.clearTimeout(this.settleTimer),this.pendingSearch=null,this.askPending=!1,this.loadGen+=1,this.query.trim()?this.searchTimer=window.setTimeout(()=>void this.searchNow(!1),250):(this.loadGen+=1,this.screen=`home`,this.results=[],this.message=null,this.linkError=null,this.render())}cancelSearch(){window.clearTimeout(this.searchTimer),window.clearTimeout(this.settleTimer),this.pendingSearch=null,this.askPending=!1,this.loadGen+=1}settleSearch(){window.clearTimeout(this.settleTimer);let e=this.pendingSearch;if(!e||e.query===this.countedSearchQuery)return;let t=()=>{this.dispatchEvent(new CustomEvent(`rig-analytics-error`,{detail:{code:`unavailable`}}))};try{let n=ne();this.countedSearchQuery=e.query,this.client.settledSearch(n,e.noResults).catch(t)}catch{t()}}async searchNow(e=!0){window.clearTimeout(this.searchTimer),window.clearTimeout(this.settleTimer);let t=this.query.trim();if(this.pendingSearch=null,!t){this.screen=`home`,this.results=[],this.message=null,this.render();return}let n=++this.loadGen;this.message=this.currentTheme().copy.loading,this.render();try{let r=await this.client.search(t,12);if(n!==this.loadGen)return;this.results=r.results,this.pendingSearch={query:t,noResults:r.results.length===0},e?this.settleSearch():this.settleTimer=window.setTimeout(()=>this.settleSearch(),750),this.screen=r.results.length?`results`:`missing`,this.swap=null,this.quote=null,this.message=null,this.screen===`missing`&&(this.linkUrl=``,this.linkRequestId=null,this.linkPending=!1,this.linkError=null,this.linkErrors={},this.linkAck=void 0,this.linkContact={name:``,email:``})}catch(e){if(n!==this.loadGen)return;this.message=this.errorText(e)}this.render()}async openSetup(e){this.cancelSearch();let t=this.findProduct(e);this.selectedId=e,this.product=t,this.screen=`setup`,this.swap=null,this.quote=null,this.graph=null,this.setups=[],this.message=this.currentTheme().copy.loading,this.render();let n=++this.loadGen;try{let[t,r]=await Promise.all([this.client.product(e),this.client.setups(e)]);if(n!==this.loadGen)return;this.product=t,this.setups=r.setups,this.activeTier=r.setups.find(e=>e.badge)?.tier??r.setups[0]?.tier??null,this.customizedTiers=[],this.message=null}catch(e){if(n!==this.loadGen)return;this.message=this.errorText(e)}this.render()}async openExplore(e){this.cancelSearch(),this.selectedId=e,this.screen=`explore`,this.swap=null,this.quote=null,this.askAnswer=null,this.askCites=``,this.askPending=!1,this.graph=null,this.message=this.currentTheme().copy.loading,this.render();let t=++this.loadGen;try{let[n,r]=await Promise.all([this.client.graph(e),this.client.setups(e)]);if(t!==this.loadGen)return;this.graph=n,this.product=n.centre,this.setups=r.setups,this.customizedTiers=[],this.activeTier=r.setups.find(e=>e.badge)?.tier??r.setups[0]?.tier??null;let i=this.setups.find(e=>e.tier===this.activeTier)??this.setups[0],a=new Set(i?.parts.map(e=>e.product.id)??[]);this.expanded=n.branches.filter(e=>e.products.some(e=>a.has(e.id))).map(e=>e.category),this.message=null}catch(e){if(t!==this.loadGen)return;this.message=this.errorText(e)}this.render()}async openSwap(e,t){if(this.swap?.tier===e&&this.swap.role===t){this.swap=null,this.render();return}let n=this.setups.find(t=>t.tier===e);if(!n)return;this.message=this.currentTheme().copy.loading,this.render();let r=this.loadGen;try{let i=await this.client.swap(n,t);if(r!==this.loadGen)return;if(!i.setup?.total){this.message=this.currentTheme().copy.errorGeneric,this.render();return}this.replaceSetup(i.setup),this.swap={tier:e,role:t,options:i.options},this.message=null}catch(e){if(r!==this.loadGen)return;this.message=this.errorText(e)}this.render()}async pickSwap(e,t){let n=this.setups.find(t=>t.tier===e),r=this.swap?.role??``,i=this.swap?.options.find(e=>e.id===t);if(!n||!i||this.swap?.tier!==e||!r)return;let a=$(n,r,i),o=this.loadGen;try{let t=await this.client.swap(a,r,!0);if(o!==this.loadGen)return;t.setup?.total?(this.replaceSetup(t.setup),this.customizedTiers.includes(e)||this.customizedTiers.push(e),this.message=null):this.message=this.currentTheme().copy.errorGeneric}catch(e){if(o!==this.loadGen)return;this.message=this.errorText(e)}this.swap=null,this.pendingFocus=this.swapButtonKey(e,r),this.render()}replaceSetup(e){let t=this.setups.findIndex(t=>t.tier===e.tier);t>=0&&(this.setups[t]=e)}async pickGraph(e){let t=this.setups.find(e=>e.tier===this.activeTier)??this.setups[0],n=this.findProduct(e);if(!t||!n||n.id===this.selectedId||t.parts.some(e=>e.product.id===n.id))return;let r=t.parts.find(e=>e.product.category===n.category);if(!r)return;let i=this.loadGen;try{let e=await this.client.swap(t,r.role);if(i!==this.loadGen)return;let a=e.options.find(e=>e.id===n.id);if(!a){this.message=this.currentTheme().copy.notAlternative,this.render();return}let o=await this.client.swap($(t,r.role,a),r.role,!0);if(i!==this.loadGen)return;if(!o.setup?.total){this.message=this.currentTheme().copy.errorGeneric,this.render();return}this.replaceSetup(o.setup),this.customizedTiers.includes(t.tier)||this.customizedTiers.push(t.tier),this.message=null}catch(e){if(i!==this.loadGen)return;this.message=this.errorText(e)}this.render()}toggleBranch(e){this.expanded=this.expanded.includes(e)?this.expanded.filter(t=>t!==e):[...this.expanded,e],this.render()}openQuoteProduct(e){this.cancelSearch();let t=this.findProduct(e);t&&(this.refreshNotice(),this.quoteOpener=this.captureFocus(),this.quoteTarget={kind:`product`,productId:e},this.quote={kind:`product`,title:t.name,totalLabel:D(t.price),name:``,email:``,error:null,requestId:null},this.pendingFocus={kind:`id`,id:`quote-name`,start:null,end:null},this.render())}openQuoteSetup(e){this.cancelSearch();let t=this.setups.find(t=>t.tier===e);t&&(this.refreshNotice(),this.quoteOpener=this.captureFocus(),this.quoteTarget={kind:`setup`,tier:e},this.quote={kind:`setup`,title:t.parts.map(e=>e.product.name).join(` + `),totalLabel:D(t.total),name:``,email:``,error:null,requestId:null},this.pendingFocus={kind:`id`,id:`quote-name`,start:null,end:null},this.render())}async submitQuote(e){if(!this.noticeReady||!this.quote||!this.quoteTarget||this.quote.pending||this.quote.requestId)return;let t=new FormData(e),n=String(t.get(`name`)??``).trim(),r=String(t.get(`email`)??``).trim();this.quote={...this.quote,name:n,email:r,error:null,pending:!0};let i={name:n,email:r},a=this.quoteTarget,o=this.quote;this.render();try{let e=a.kind===`setup`?this.setups.find(e=>e.tier===a.tier):void 0;if(a.kind===`setup`&&!e)throw new g(`invalid_quote`,this.currentTheme().copy.unavailable,400);let t=a.kind===`product`?await this.client.quote({contact:i,notice_version:this.notice?.version,product_id:a.productId}):await this.client.quote({contact:i,notice_version:this.notice?.version,setup:e});if(this.quote!==o)return;this.quote={...o,requestId:t.request_id,ack:t,pending:!1},this.pendingFocus={kind:`action`,action:`close-quote`,id:``,tier:``,role:``,category:``,q:``}}catch(e){if(this.quote!==o)return;this.quote={...o,error:this.errorText(e),pending:!1},e instanceof g&&e.code===`notice_changed`&&this.refreshNotice()}this.render()}linkFieldError(e,t){let n=t.value.trim(),r=this.currentTheme().copy,i={url:r.linkRequired,name:r.nameRequired,email:r.emailRequired,phone:r.phoneRequired},a={url:r.linkInvalid,name:r.nameInvalid,email:r.emailInvalid,phone:r.phoneInvalid};if(!n)return i[e];if(e===`url`)try{let e=new URL(n);if(![`http:`,`https:`].includes(e.protocol)||!e.hostname||e.username||e.password||n.length>2048)return a.url}catch{return a.url}else if(e===`email`){if(t.validity.typeMismatch||n.length>254||!/^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$/.test(n))return a.email}else if(e===`phone`){let e=n.replace(/\D/g,``);if(!/^\+?(?:\d|\(\d{1,5}\)|[ .-])+$/.test(n)||e.length<7||e.length>15||n.length>40)return a.phone}else if(n.length>120)return a.name}async submitLink(e){if(!this.noticeReady||this.linkPending||this.linkRequestId)return;this.linkError=null,this.linkErrors={};for(let t of[`url`,`name`,`email`,`phone`]){let n=e.querySelector(`#link-${t}`);if(!n)return;let r=this.linkFieldError(t,n);r&&(this.linkErrors[t]=r)}let t=Object.keys(this.linkErrors)[0];if(t){this.pendingFocus={kind:`id`,id:`link-${t}`,start:null,end:null},this.render();return}this.linkContact={name:this.linkContact.name.trim(),email:this.linkContact.email.trim(),phone:this.linkContact.phone?.trim()},this.linkPending=!0,this.render();let n=this.linkContact,r=e=>{let t={...e,linkPending:!1};for(let e of this.historyViews.values())e.view.linkContact===n&&Object.assign(e.view,t);this.linkContact===n&&(Object.assign(this,t),this.render())};try{let e=await this.client.linkQuote({notice_version:this.notice?.version,url:this.linkUrl.trim(),note:``,contact:n});r({linkRequestId:e.request_id,linkAck:e})}catch(e){r({linkError:this.errorText(e)}),e instanceof g&&e.code===`notice_changed`&&this.refreshNotice()}}async submitAsk(){let e=this.graph?.centre.id??this.selectedId;if(!e||!this.askQuestion.trim()||this.askPending)return;let t=this.loadGen;this.askPending=!0;let n=this.setups.find(e=>e.tier===this.activeTier)??this.setups[0];this.message=this.currentTheme().copy.loading,this.render();try{let r=await this.client.ask({product_id:e,...n?{setup:n}:{},question:this.askQuestion});if(t!==this.loadGen)return;this.askAnswer=r.answer,this.askCites=this.citeLabel(r.cites),this.message=null}catch(e){if(t!==this.loadGen)return;this.message=this.errorText(e)}this.askPending=!1,this.render()}citeLabel(e){let t=new Map;if(this.graph){t.set(this.graph.centre.id,this.graph.centre.name);for(let e of this.graph.branches)for(let n of e.products)t.set(n.id,n.name)}return e.map(e=>t.get(e)??e).join(`, `)}findProduct(e){if(this.product?.id===e)return this.product;let t=this.results.find(t=>t.id===e);if(t)return t;for(let t of this.setups){let n=t.parts.find(t=>t.product.id===e);if(n)return n.product}if(this.graph){if(this.graph.centre.id===e)return this.graph.centre;for(let t of this.graph.branches){let n=t.products.find(t=>t.id===e);if(n)return n}}return this.swap?.options.find(t=>t.id===e)??null}errorText(e){return e instanceof g||e instanceof Error&&e.message?e.message:this.currentTheme().copy.errorGeneric}};function $(e,t,n){let r={tier:e.tier,label:e.label,parts:e.parts.map(e=>e.role===t?{role:t,product:n}:e),total:e.total,why:e.why};return e.badge&&(r.badge=e.badge),r}function ye(e){let t=document.querySelectorAll(`link[data-tervory-rig-font]`);for(let n of t)if(n.getAttribute(`data-tervory-rig-font`)===e)return;let n=document.createElement(`link`);n.rel=`stylesheet`,n.href=e,n.setAttribute(`data-tervory-rig-font`,e),document.head.appendChild(n)}customElements.get(`tervory-rig`)||customElements.define(`tervory-rig`,Q),e.RigClient=T,e.TervoryRig=Q,e.customNation=j,e.neutral=M,e.resolveTheme=N});