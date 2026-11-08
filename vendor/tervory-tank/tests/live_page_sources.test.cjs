/** Offline transport tests of the actual route components; never a live data source. */
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '..');
const workspace = path.join(root, 'workspace');
const deps = createRequire(path.join(workspace, 'package.json'));
const ts = deps('typescript');
const React = deps('react');
const { renderToStaticMarkup } = deps('react-dom/server');
const fixtureContent = /Fixture|\[TEST\]|CVE-2024-0000|00000000-0000-0000-0000/;
const forbidden = new RegExp(`${fixtureContent.source}|ThreatPayload`);
const id = 'c634c3df-a3e3-4d25-86ea-0286d63ba4c9';
const otherId = 'b634c3df-a3e3-4d25-86ea-0286d63ba4c9';
const match = { matchId: id, published: true, title: 'Published civil appeal', fairnessReading: 'split', judgmentOrder: 'appeal_dismissed', reveal: '' };

// Compile with the workspace's installed compiler, replacing only framework/transport boundaries.
function harness(fetch, env = {}) {
  const cache = new Map();
  const calls = [];
  const notFound = () => { throw new Error('NEXT_NOT_FOUND'); };
  const action = async () => null;
  function load(filename) {
    filename = path.resolve(filename);
    if (cache.has(filename)) return cache.get(filename).exports;
    const module = { exports: {} };
    cache.set(filename, module);
    const js = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      fileName: filename,
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    }).outputText;
    function requireSource(spec) {
      if (spec === '@/lib/trigger-signin-fetch') return {signinFetch: async (url, options) => {
        calls.push({url: String(url), method: options?.method}); return fetch(url, options);
      }};
      if (spec === 'server-only' || spec.endsWith('.css')) return {};
      if (spec === 'next/link') return { __esModule: true, default: ({ children, ...props }) => React.createElement('a', props, children) };
      if (spec === 'next/navigation') return { notFound };
      if (spec === '@/lib/trigger-signin') return { auth: async () => ({ userId: 'offline-founder' }) };
      if (spec === '@/lib/admin') return { isFounder: () => true };
      if (spec === './actions') return { look: action, resolve: action, shieldAction: action };
      if (spec === './quarantine-actions') return { releaseBatch: action, rejectBatch: action, removeBatch: action };
      if (spec.startsWith('.') || spec.startsWith('@/')) {
        const base = spec.startsWith('@/') ? path.join(workspace, spec.slice(2)) : path.resolve(path.dirname(filename), spec);
        const resolved = ['', '.ts', '.tsx'].map(ext => base + ext).find(p => fs.existsSync(p) && fs.statSync(p).isFile());
        if (!resolved) throw new Error(`Module not found: ${spec}`);
        return load(resolved);
      }
      return deps(spec);
    }
    new Function('require', 'module', 'exports', 'fetch', 'process', js)(requireSource, module, module.exports,
      async (url, options) => { calls.push({ url: String(url), method: options?.method }); return fetch(url, options); },
      { env });
    return module.exports;
  }
  return { load: name => load(path.join(workspace, name)), calls };
}
const response = (body, status = 200) => new Response(JSON.stringify(body), { status });
const failed = () => response({}, 503);
const render = async (h, page, props = {}) => renderToStaticMarkup(await h.load(page).default(props));

// These replace the old tests that asserted a production fixture store was public.
test('Moot index reads published room matches through Tank, with US home and no cache', async () => {
  const h = harness((url, options) => {
    assert.equal(options.cache, 'no-store');
    assert.equal(new URL(url).pathname, '/v1/zenas/moot/published');
    assert.equal(new URL(url).searchParams.get('home_region'), 'us-east');
    return response({ matches: [match, { ...match, matchId: otherId, published: false }, { ...match, title: 'Fixture appeal [TEST]' }] });
  });
  const html = await render(h, 'app/moot/page.tsx');
  assert.match(html, new RegExp(`/moot/${id}`));
  assert.doesNotMatch(html, new RegExp(otherId));
  assert.doesNotMatch(html, forbidden);
});

test('Moot transport bounds a hung read and preserves caller cancellation', async () => {
  const abortingFetch = (_url, options) => new Promise((resolve, reject) => {
    assert.ok(options.signal instanceof AbortSignal);
    if (options.signal.aborted) reject(options.signal.reason);
    else options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true });
  });
  const controller = new AbortController();
  const callerRead = harness(abortingFetch).load('lib/tank.ts').mootPublishedMatches(undefined, controller.signal);
  controller.abort();
  await assert.rejects(callerRead, { name: 'AbortError' });
  // AbortSignal.timeout is unref'ed; retain a handle for this mocked transport.
  const keepAlive = setTimeout(() => {}, 12000);
  try {
    const start = Date.now();
    const result = await harness(abortingFetch).load('app/moot/store.ts').listPublishedMatches();
    assert.deepEqual(result, { status: 'unavailable' });
    assert.ok(Date.now() - start >= 9900);
  } finally {
    clearTimeout(keepAlive);
  }
});

test('Moot reader distinguishes empty, malformed, and unavailable replies without leaking details', async () => {
  const cases = [
    [() => response({ matches: [] }), 'empty'],
    [() => response({ matches: [{}] }), 'malformed'],
    [() => response({}), 'malformed'],
    [() => response(null), 'malformed'],
    [() => new Response('{broken'), 'malformed'],
    [failed, 'unavailable'],
    [() => { throw new Error('private upstream details'); }, 'unavailable'],
    [() => { throw new DOMException('timed out', 'TimeoutError'); }, 'unavailable'],
  ];
  for (const [fetch, status] of cases) {
    const reader = harness(fetch).load('app/moot/store.ts');
    assert.equal((await reader.listPublishedMatches()).status, status);
    assert.equal((await reader.getMatch(id)).status, status === 'empty' ? 'missing' : status);
    if (status !== 'empty') await assert.rejects(reader.isPublicWatch(id), /Moot published matches/);
    assert.doesNotMatch(JSON.stringify(await reader.listPublishedMatches()), /private upstream details|timed out/);
  }
});

test('Moot pages reserve the empty and missing states for successful reads', async () => {
  const empty = await render(harness(() => response({ matches: [] })), 'app/moot/page.tsx');
  assert.match(empty, /No published matches yet\./);
  assert.doesNotMatch(empty, /role="alert"/);
  for (const [fetch, state] of [[failed, 'unavailable'], [() => response({ matches: [{}] }), 'malformed']]) {
    for (const page of ['app/moot/page.tsx', 'app/moot/[match]/page.tsx']) {
      const html = await render(harness(fetch), page, { params: Promise.resolve({ match: id }) });
      assert.match(html, /role="alert"/);
      assert.match(html, new RegExp(`data-moot-state="${state}"`));
      assert.match(html, /Room unavailable/);
      assert.doesNotMatch(html, /No published matches yet|Published civil appeal/);
      assert.doesNotMatch(html, forbidden);
    }
  }
});

test('Moot readiness distinguishes failures from healthy empty without returning match content', async () => {
  for (const [fetch, status, state] of [
    [() => response({ matches: [] }), 200, 'empty'],
    [() => response({ matches: [match] }), 200, 'available'],
    [failed, 503, 'unavailable'],
    [() => response({}), 502, 'malformed'],
  ]) {
    const result = await harness(fetch).load('app/moot/status/route.ts').GET();
    assert.equal(result.status, status);
    assert.equal(result.headers.get('cache-control'), 'no-store');
    assert.equal(result.headers.get('x-robots-tag'), 'noindex');
    assert.deepEqual(await result.json(), { status: state });
  }
});

test('Moot validates every rendered field and excludes private/offline content', async () => {
  const invalid = [
    { ...match, matchId: 'invalid' }, { ...match, title: '' },
    { ...match, transcript: [{}] }, { ...match, judgmentReasons: [null] },
    { ...match, authorities: [{ name: 'Unsafe', source_url: 'javascript:alert(1)' }] },
    { ...match, fairnessReading: null }, { ...match, judgmentOrder: [] },
    { ...match, reveal: {} }, { ...match, costUsd: '0' },
  ];
  for (const row of invalid) {
    assert.deepEqual(await harness(() => response({ matches: [row] })).load('app/moot/store.ts').listPublishedMatches(), { status: 'malformed' });
  }
  const reader = harness(() => response({ matches: [
    { ...match, published: false, transcript: 'private' },
    { ...match, title: '[TEST]' },
    { ...match, matchId: '00000000-0000-0000-0000-000000000001' },
  ] })).load('app/moot/store.ts');
  assert.deepEqual(await reader.listPublishedMatches(), { status: 'empty', matches: [] });
  assert.deepEqual(await reader.getMatch(id), { status: 'missing' });
  assert.equal(await reader.isPublicWatch(id), false);
  const publicReader = harness(() => response({ matches: [{ ...match, authorities: [{ name: 'Court', source_url: '' }], privateNotes: 'never serialize', transcript: [{ role: 'judge', text: 'Order', secret: 'hidden' }] }] })).load('app/moot/store.ts');
  const found = await publicReader.getMatch(id);
  assert.equal(found.status, 'available');
  assert.equal(found.match.matchId, id);
  assert.equal(await publicReader.isPublicWatch(id), true);
  assert.doesNotMatch(JSON.stringify(found), /never serialize|hidden|privateNotes|secret/);
});

test('Moot detail calls the real reader and refuses private, fixture, missing, and invalid matches', async () => {
  const h = harness(url => {
    assert.equal(new URL(url).searchParams.get('match_id'), id);
    return response({ matches: [match] });
  });
  const html = await render(h, 'app/moot/[match]/page.tsx', { params: Promise.resolve({ match: id }) });
  assert.match(html, /Published civil appeal/);
  assert.doesNotMatch(html, forbidden);
  for (const rows of [[], [{ ...match, published: false }], [{ ...match, title: '[TEST]' }], [{ ...match, matchId: otherId }]]) {
    await assert.rejects(render(harness(() => response({ matches: rows })), 'app/moot/[match]/page.tsx', { params: Promise.resolve({ match: id }) }), /NEXT_NOT_FOUND/);
  }
  const offline = harness(() => { throw new Error('must not fetch'); });
  for (const matchId of ['fixture-published', '../private']) {
    await assert.rejects(render(offline, 'app/moot/[match]/page.tsx', { params: Promise.resolve({ match: matchId }) }), /NEXT_NOT_FOUND/);
  }
  assert.equal(offline.calls.length, 0);
});

test('public Autumbra renders product description without fixture data or internal terms', async () => {
  const html = await render(harness(() => { throw new Error('must not fetch'); }), 'app/autumbra/page.tsx');
  assert.match(html, /Watch the edge\./);
  assert.match(html, /Autumbra Watch finds availability, DNS, certificate, and header problems/);
  assert.match(html, /<article><h3>Autumbra Watch<\/h3><\/article>/);
  assert.doesNotMatch(html, forbidden);
  assert.doesNotMatch(html, /ghost\.threat\.v1|burn-on-fail|On watch|Open investigations/i);
});

test('founder Autumbra renders existing unavailable states for failed and incomplete room replies', async () => {
  for (const fetch of [failed, () => response({}), () => { throw new Error('offline'); }]) {
    const html = await render(harness(fetch), 'app/admin/autumbra/page.tsx');
    assert.match(html, /Room unavailable/);
    assert.match(html, /Shield numbers are unavailable\./);
    assert.match(html, /Investigations will appear when Autumbra’s room is connected\./);
    // The founder console names its real receipt schema; public pages must not.
    assert.match(html, /ThreatPayload \/ advisory receipts/);
    assert.doesNotMatch(html, fixtureContent);
  }
});

test('shield reads reject explicit offline boards and taps use the actual room without a fixture fallback', async () => {
  const env = { TANK_API_KEY: 'placeholder-shield-test-key' };
  const h = harness(() => response({ fixture: true, shields: [] }), env);
  await assert.rejects(h.load('app/admin/autumbra/shields-data.ts').shieldBoard('offline-founder'), /unavailable/);
  assert.equal(h.calls.length, 2);
  const missingKey = harness(failed);
  await assert.rejects(missingKey.load('app/admin/autumbra/shields-data.ts').shieldTap('offline-founder', 'a1111111-1111-4111-8111-111111111111', 'raise'), /TANK_API_KEY/);
  assert.equal(missingKey.calls.length, 0);
  const down = harness(failed, env);
  await assert.rejects(down.load('app/admin/autumbra/shields-data.ts').shieldTap('offline-founder', 'a1111111-1111-4111-8111-111111111111', 'raise'));
  assert.equal(down.calls.length, 1);
  assert.equal(down.calls[0].method, 'POST');
});

test('feed review displays supplied feed and state label and never serializes the raw series id', () => {
  const h = harness(failed);
  const { quarantineFromResponse, QUARANTINE_ERROR } = h.load('app/market-lab/quarantine.ts');
  const row = { id: 'offline-batch', series_id: '00000000-0000-0000-0000-000000000001:check_counts:total:AL', series_name: 'Monthly background-check counts by state and type · Alabama', source: 'check_counts', region: 'us-east', currency: '', unit: 'checks', fetched_at: '2026-10-27T00:00:00Z', status: 'held', reason: '' };
  const loaded = quarantineFromResponse(200, { batches: [row] });
  assert.equal(loaded.error, null);
  const { QuarantinePanel } = h.load('app/market-lab/quarantine-view.tsx');
  const html = renderToStaticMarkup(React.createElement(QuarantinePanel, loaded));
  assert.match(html, /Monthly background-check counts by state and type · Alabama/);
  assert.doesNotMatch(html, forbidden);
  assert.doesNotMatch(JSON.stringify(loaded), /00000000-0000-0000-0000/);
  const missing = quarantineFromResponse(200, { batches: [{ ...row, series_name: undefined }] });
  assert.equal(missing.error, QUARANTINE_ERROR);
  assert.deepEqual(missing.batches, []);
});


test('ZA public reads use the existing regional connection and never fall back to Boston', async () => {
  const env = { TANK_URL_ZA: 'https://za.example', TANK_API_KEY_ZA: 'offline-za' };
  const h = harness((url, options) => {
    assert.equal(options.cache, 'no-store');
    assert.equal(options.redirect, 'error');
    assert.equal(options.headers['x-tank-key'], 'offline-za');
    return response({ matches: [match, { ...match, matchId: otherId, published: false }] });
  }, env);
  const result = await h.load('app/moot/store.ts').listPublishedMatches(undefined, 'za');
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { status: 'available', matches: [match] });
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].url, 'https://za.example/v1/zenas/moot/published?home_region=za');
  const html = await render(h, 'app/moot/page.tsx', { searchParams: Promise.resolve({ region: 'za' }) });
  assert.match(html, new RegExp(`/moot/${id}\\?region=za`));
  assert.doesNotMatch(html, new RegExp(otherId));
  assert.equal((await h.load('app/moot/store.ts').getMatch(otherId, 'za')).status, 'missing');
});

test('missing ZA connection returns nothing and makes no request', async () => {
  const h = harness(() => { throw new Error('must not fetch'); });
  assert.deepEqual(await h.load('app/moot/store.ts').listPublishedMatches(undefined, 'za'), { status: 'empty', matches: [] });
  assert.equal(h.calls.length, 0);
});

test('US public read keeps its existing endpoint', async () => {
  const h = harness(() => response({ matches: [] }));
  await h.load('app/moot/store.ts').listPublishedMatches();
  assert.equal(h.calls[0].url, 'http://tank:8000/v1/zenas/moot/published?home_region=us-east');
});

test('canonical Moot redirect preserves the ZA query', () => {
  const { mootCanonicalRedirectUrl } = harness(() => { throw new Error('must not fetch'); }).load('lib/product-hosts.ts');
  assert.equal(mootCanonicalRedirectUrl('app.tervory.com', '/moot', '?region=za'), 'https://zenas.tervory.com/moot?region=za');
});
