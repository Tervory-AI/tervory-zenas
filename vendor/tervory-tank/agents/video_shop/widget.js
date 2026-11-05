/* Internal embed. Proposed wording: video-shop REGISTER, design commit be87b92e.
 * Only a Rig publishable key belongs in the browser. */
(function (global) {
  'use strict';
  global.VideoShop = {
    async mount(container, options) {
      const base = new URL(options.rigBase, document.baseURI);
      if (base.protocol !== 'https:' && base.hostname !== 'localhost') throw new Error('https_required');
      if (!/^rk_pub_[A-Za-z0-9_-]+$/.test(options.key)) throw new Error('publishable_key_required');
      const prefix = base.href.replace(/\/$/, '') + '/v1/rig';
      let destroyed = false, blobURL, session = options.cartSession, selected, busy = false;
      const abort = new AbortController();
      const request = async (path, data, blob = false) => {
        const response = await fetch(prefix + path, {
          method: data === undefined ? 'GET' : 'POST', credentials: 'omit',
          headers: {'X-Rig-Key': options.key, ...(data === undefined ? {} : {'Content-Type': 'application/json'})},
          body: data === undefined ? undefined : JSON.stringify(data), signal: abort.signal
        });
        if (!response.ok) throw new Error('request_failed');
        return blob ? response.blob() : response.json();
      };
      const labels = {
        watch: 'Watch video', products: 'Products in this video', view: 'View product',
        productDetails: 'Product details', add: 'Add to cart', cart: 'Open store cart',
        loading: 'Loading…', adding: 'Loading…', added: 'Added to your store’s cart.',
        error: 'The store’s cart could not be updated.', videoError: 'The video could not be loaded.',
        productError: 'The product could not be loaded.', empty: 'No products in this video yet.',
        keepWatching: 'Keep watching', payment: 'Payment is handled by the store.',
        play: 'Play', pause: 'Pause', ...options.labels
      };
      const root = container.attachShadow({mode: 'open'});
      const element = (tag, text) => { const node = document.createElement(tag); if (text) node.textContent = text; return node; };
      const button = (text, action) => { const node = element('button', text); node.type = 'button'; node.addEventListener('click', action); return node; };
      const style = element('style');
      // Shared CRM/Rig button tokens, adapted to an independent shadow-DOM embed.
      style.textContent = ':host{display:block;color:var(--cs-color-text,#001840);font:14px Inter,sans-serif}*{box-sizing:border-box}[hidden]{display:none!important}nav{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:24px}button{font:600 13px Inter,sans-serif;min-height:48px;max-width:100%;padding:8px 24px;border:1px solid var(--cs-color-border,#B8BFD0);border-radius:var(--cs-radius-8,8px);background:var(--cs-color-surface,#fff);color:inherit;cursor:pointer}button.primary{background:var(--cs-color-text,#001840);color:var(--cs-color-inverse,#fff);min-width:160px}button:disabled{opacity:.5;cursor:default}button:focus-visible,input:focus-visible{outline:2px solid #001840;outline-offset:2px}.viewport{position:relative;border:1px solid var(--crm-input-border,#848da3);border-radius:8px;background:#fff;padding:16px}video{display:block;width:100%;max-height:504px;object-fit:contain}aside,section,footer{border:1px solid var(--crm-input-border,#848da3);border-radius:8px;padding:16px;background:#fff;margin-top:16px}aside{position:absolute;bottom:16px;left:16px;right:16px}aside:empty{display:none}h2{font-size:24px;font-weight:600}h3{font-size:16px}img{max-width:160px}input[type=range]{width:100%;accent-color:var(--cs-color-text,#001840)}article+article{border-top:1px solid #B8BFD0;margin-top:16px;padding-top:16px}';
      const heading = element('h2', labels.watch);
      const nav = element('nav');
      const viewport = element('div'); viewport.className = 'viewport';
      const video = element('video'); video.playsInline = true; video.setAttribute('aria-label', options.videoLabel || labels.watch);
      const panel = element('aside'); panel.setAttribute('aria-live', 'polite');
      const controls = element('footer');
      const clock = element('p');
      const play = button(labels.play, async () => { try { if (video.paused) await video.play(); else video.pause(); } catch (_) { setStatus('videoError'); } });
      const seek = element('input'); seek.type = 'range'; seek.min = '0'; seek.max = '0'; seek.step = '.1'; seek.value = '0'; seek.setAttribute('aria-label', 'Video timeline');
      seek.addEventListener('input', () => { video.currentTime = Number(seek.value); });
      controls.append(clock, play, seek);
      video.addEventListener('play', () => { play.textContent = labels.pause; });
      video.addEventListener('pause', () => { play.textContent = labels.play; });
      video.addEventListener('error', () => setStatus('videoError'));
      const listing = element('section'); listing.hidden = true;
      const details = element('section'); details.hidden = true;
      const status = element('p'); status.setAttribute('role', 'status');
      const setStatus = state => { status.textContent = labels[state] || ''; container.dispatchEvent(new CustomEvent('video-shop:state', {detail: {state}})); };
      const showWatch = () => { viewport.hidden = controls.hidden = false; listing.hidden = true; heading.textContent = labels.watch; };
      nav.append(button(labels.watch, showWatch), button(labels.products, () => { viewport.hidden = controls.hidden = true; listing.hidden = false; heading.textContent = labels.products; }));
      viewport.append(video, panel); root.append(style, heading, nav, viewport, controls, listing, details, status);
      const time = seconds => `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
      let storeCartURL;
      if (options.storeCartUrl) { const url = new URL(options.storeCartUrl); if (url.protocol !== 'https:') throw new Error('store_cart_url'); storeCartURL = url.href; }
      const detail = async product => {
        selected = product.id; details.replaceChildren(); details.hidden = false; setStatus('loading');
        try {
          const value = await request('/products/' + encodeURIComponent(product.id));
          if (destroyed || selected !== product.id) return;
          const name = element('h3', value.name || product.name);
          const price = element('p', product.price.amount + ' ' + product.price.currency);
          const picture = element('img'); picture.hidden = true;
          picture.addEventListener('error', () => { picture.hidden = true; });
          try { const source = new URL(product.image); if (source.protocol === 'https:') { picture.src = source.href; picture.hidden = false; } } catch (_) {}
          picture.alt = product.name;
          const attributes = element('p', Object.entries(value.attributes || {}).filter(([, val]) => ['string', 'number'].includes(typeof val)).map(([key, val]) => `${key.replaceAll('_', ' ')}: ${val}`).join(' · '));
          const add = button(labels.add, async () => {
            if (busy) return;
            busy = true; add.disabled = true; setStatus('adding');
            try {
              if (!session) session = (await request('/cart/session', {})).session;
              const cart = await request('/cart/add', {session, product_id: product.id, quantity: 1});
              container.dispatchEvent(new CustomEvent('video-shop:cart', {detail: cart})); setStatus('added');
            } catch (_) { setStatus('error'); }
            finally { busy = false; add.disabled = false; }
          });
          add.className = 'primary';
          const open = button(labels.cart, () => { if (storeCartURL) global.location.assign(storeCartURL); }); open.disabled = !storeCartURL;
          details.replaceChildren(element('h2', labels.productDetails), name, picture, price, attributes,
            element('p', 'Quantity: 1'), element('p', labels.payment), add, open,
            button(labels.keepWatching, () => { details.hidden = true; showWatch(); })); setStatus('ready');
        } catch (_) { setStatus('productError'); }
      };
      try {
        setStatus('loading');
        const timeline = await request('/video-shop/videos/' + encodeURIComponent(options.videoId));
        const media = await request('/video-shop/videos/' + encodeURIComponent(options.videoId) + '/media', undefined, true);
        if (destroyed) return;
        blobURL = URL.createObjectURL(media); video.src = blobURL;
        const groups = new Map();
        for (const moment of timeline.moments) { if (!groups.has(moment.product_id)) groups.set(moment.product_id, []); groups.get(moment.product_id).push(moment); }
        for (const [id, moments] of groups) {
          const product = timeline.products[id], row = element('article');
          moments.sort((a, b) => a.start - b.start);
          row.append(element('h3', product.name), element('p', moments.map(m => `${time(m.start)}–${time(m.end)}`).join(' · ')),
            button(labels.view, () => detail(product)), button(`Watch at ${time(moments[0].start)}`, () => { showWatch(); video.currentTime = moments[0].start; }));
          listing.append(row);
        }
        if (!groups.size) listing.append(element('p', labels.empty));
        let previous = '';
        const update = () => {
          const duration = Number.isFinite(video.duration) ? video.duration : timeline.duration || 0;
          clock.textContent = `${time(video.currentTime)} / ${time(duration)}`; seek.max = String(duration); seek.value = String(video.currentTime);
          const ids = [...new Set(timeline.moments.filter(m => m.start <= video.currentTime && video.currentTime < m.end).map(m => m.product_id))];
          const signature = JSON.stringify(ids);
          if (signature === previous) return;
          previous = signature; panel.replaceChildren();
          if (!ids.includes(selected)) { selected = undefined; details.replaceChildren(); details.hidden = true; }
          for (const id of ids) {
            const product = timeline.products[id], row = element('div');
            const view = button(labels.view, () => detail(product)); view.className = 'primary'; view.setAttribute('aria-label', `${labels.view}: ${product.name}`);
            row.append(element('h3', product.name), view); panel.append(row);
          }
        };
        video.addEventListener('timeupdate', update); video.addEventListener('seeked', update); video.addEventListener('loadedmetadata', update); update(); setStatus('ready');
      } catch (_) { setStatus('videoError'); }
      return {
        getSession() { return session; },
        async checkout() { if (!session) throw new Error('cart_empty'); return request('/cart/checkout', {session}); },
        destroy() { destroyed = true; abort.abort(); video.pause(); video.removeAttribute('src'); video.load(); if (blobURL) URL.revokeObjectURL(blobURL); root.replaceChildren(); }
      };
    }
  };
})(window);
