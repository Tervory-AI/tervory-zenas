import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { run, Tank, originFor, receiptLines, safe, events, StreamSanitizer, showTurn, followThread } from '../src/client.mjs';

// Synthetic credentials exist only in this fake-Tank harness, never live data.
const TOKEN = `tv1.us-east.${'0'.repeat(32)}.${'x'.repeat(43)}`;
const row = { step_id: 's1', model: 'fake-model', tokens_in: 20, tokens_out: 8,
  cost_usd: '0.000028', baseline_cost_usd: '0.000056', saved_usd: '0.000028', ledger_id: 'fake-ledger-1' };
const encode = (kind, data) => `event: ${kind}\ndata: ${JSON.stringify(data)}\n\n`;

function harness({ answers = [], token = TOKEN, refusal = false } = {}) {
  let saved = token; let output = ''; const calls = []; const stored = [];
  const io = {
    interactive: true,
    write: value => { output += value; },
    prompt: async () => answers.shift() ?? null,
  };
  const transport = async (url, init) => {
    calls.push({ url, ...init, body: init.body ? JSON.parse(init.body) : null });
    assert.equal(init.redirect, 'manual');
    assert.equal(init.headers.Authorization, `Bearer ${token}`);
    assert.equal(new URL(url).origin, originFor(token));
    if (refusal) return new Response('must not print secret detail', { status: 401 });
    const path = new URL(url).pathname;
    if (path.endsWith('/tools')) return Response.json(['base.batch', 'base.search']);
    if (path.endsWith('/models')) return Response.json([{ name: 'fake-model', input_usd_per_m: 1, output_usd_per_m: 2 }]);
    if (path.endsWith('/threads')) return Response.json(init.method === 'GET'
      ? [{ thread_id: 'fake-thread', title: 'Fake Tank evidence', spent_usd: '0.000028', saved_usd: '0.000028' }]
      : { thread_id: 'fake-thread' });
    if (path.endsWith('/turn')) {
      return new Response(encode('step', { status: 'running', actor: 'Tank', label: 'Fake step' })
        + encode('token', { text: 'Fake Tank answer.' })
        + encode('receipt', { rows: [{ ...row, requested_model: calls.at(-1).body.model }] })
        + encode('card', { card_id: 'fake-card', summary: 'Fake action', status: 'pending' })
        + encode('done', { message_id: 'fake-message' }), { headers: { 'Content-Type': 'text/event-stream' } });
    }
    if (path.endsWith('/actions') || path.endsWith('/build')) return Response.json({ card_id: 'fake-card', summary: 'Fake action', status: 'pending' });
    if (path.includes('/cards/')) return Response.json({ card_id: 'fake-card', status: calls.at(-1).body.action === 'confirm' ? 'confirmed' : 'cancelled' });
    if (path.endsWith('/fake-thread')) return Response.json({ receipts: [row], steps: [{ step_id: 'fake-card', status: 'done', actor: 'base', label: 'Fake work done' }] });
    throw new Error(`Unexpected fake route ${path}`);
  };
  return { io, transport, calls, stored,
    store: { get: async () => saved, set: async value => { saved = value; stored.push(value); }, remove: async () => { saved = null; } },
    output: () => output };
}

const TENANT_HEX = '0'.repeat(32);
const dummyToken = (prefix, region, n) => `${prefix}.${region}.${TENANT_HEX}.${String(n).repeat(43).slice(0, 43)}`;
const grantFor = (region, n, now = Date.now()) => ({
  token_type: 'Bearer', access_token: dummyToken('tv1', region, n),
  refresh_token: dummyToken('tvr1', region, n + 1),
  expires_at: new Date(now + 3600_000).toISOString(), refresh_expires_at: new Date(now + 30 * 86400_000).toISOString(),
});

function deviceHarness({ region = 'us-east', outcomes = ['authorization_pending', 'slow_down', 'grant'] } = {}) {
  let saved = null; let output = ''; const calls = []; const opened = []; const slept = [];
  const transport = async (url, init) => {
    const call = { url, path: new URL(url).pathname, origin: new URL(url).origin, body: JSON.parse(init.body), headers: init.headers };
    calls.push(call);
    assert.equal(init.redirect, 'manual'); assert.equal(init.method, 'POST');
    assert.equal(init.headers.Authorization, undefined);
    if (call.path.endsWith('/device/code')) {
      return Response.json({ device_code: `tzd1.offline-${region}`, user_code: 'BCDF-GHJK', interval: 5, expires_in: 600,
        identity_provider: 'trigger-signin', verification_uri: 'https://auth.tervory.com/device', verification_uri_complete: 'https://auth.tervory.com/device?code=BCDF-GHJK' });
    }
    if (call.path.endsWith('/device/token')) {
      const next = outcomes.shift();
      if (typeof next === 'object') return Response.json(next.body, { status: next.status });
      return next === 'grant' ? Response.json(grantFor(region, 1)) : Response.json({ error: next, detail: 'must not print' }, { status: 400 });
    }
    if (call.path.endsWith('/device/refresh')) return Response.json(grantFor(region, 3));
    if (call.path.endsWith('/device/revoke')) return Response.json({ revoked: true });
    throw new Error(`Unexpected fake route ${call.path}`);
  };
  const io = { write: value => { output += value; }, open: url => { opened.push(url); }, sleep: async ms => { slept.push(ms); },
    deviceName: () => 'zenas-laptop\x1b[31m' };
  return { transport, io, calls, opened, slept, output: () => output,
    store: { get: async () => saved, set: async value => { saved = value; }, remove: async () => { saved = null; } },
    saved: () => saved };
}

test('login is a browser device approval: no paste prompt, polls, stores a refreshable credential', async () => {
  const h = deviceHarness();
  await run(['login'], h);
  assert.deepEqual(h.calls.map(call => call.path), ['/v1/command/device/code', ...Array(3).fill('/v1/command/device/token')]);
  assert(h.calls.every(call => call.origin === 'https://app.tervory.com'));
  assert.deepEqual(h.calls[0].body, { device_name: 'zenas-laptop[31m', approval: 'trigger-signin' });
  // The browser opens the Workspace page built by the CLI, never a server-chosen URL.
  assert.deepEqual(h.opened, ['https://auth.tervory.com/device?code=BCDF-GHJK']);
  assert.deepEqual(h.slept, [5000, 5000, 10000]);
  const saved = JSON.parse(h.saved());
  assert.equal(saved.region, 'us-east'); assert.match(saved.access_token, /^tv1\.us-east\./); assert.match(saved.refresh_token, /^tvr1\.us-east\./);
  assert.match(h.output(), /BCDF-GHJK/);
  for (const secret of [saved.access_token, saved.refresh_token, 'tvd1.']) assert(!h.output().includes(secret));
  assert(!h.output().includes('must not print'));
});

test('ZA login goes straight to the ZA Tank and opens ZA Trigger sign-in', async () => {
  const h = deviceHarness({ region: 'za', outcomes: ['grant'] });
  await run(['login', '--region', 'za'], h);
  assert(h.calls.every(call => call.origin === 'https://za.tervory.com'));
  assert.deepEqual(h.opened, ['https://auth.tervory.com/device?code=BCDF-GHJK']);
  assert.equal(h.calls[0].body.approval, 'trigger-signin');
  assert.equal(JSON.parse(h.saved()).region, 'za');
  await assert.rejects(run(['login', '--region', 'eu'], h), /only --region/);
  await assert.rejects(run(['models', '--region', 'za'], h), /Invalid command option/);
});

test('denied and expired approvals store nothing', async () => {
  for (const [outcome, message] of [['access_denied', /denied in the browser/], ['expired_token', /code expired/], ['invalid_grant', /refused by Tank/]]) {
    const h = deviceHarness({ outcomes: [outcome] });
    await assert.rejects(run(['login'], h), message);
    assert.equal(h.saved(), null);
  }
});

test('a 401 or 403 while polling stops login at once instead of polling to expiry', async () => {
  for (const status of [401, 403]) {
    const h = deviceHarness({ region: 'za', outcomes: ['authorization_pending', { status, body: { error: 'authorization_pending' } }, 'grant'] });
    await assert.rejects(run(['login', '--region', 'za'], h), new RegExp(`Login refused by Tank \\(HTTP ${status}\\)`));
    assert.equal(h.calls.filter(call => call.path.endsWith('/device/token')).length, 2);
    assert.equal(h.saved(), null);
  }
});

test('expiring access token refreshes once, rotates the keychain copy, then calls Tank', async () => {
  const h = deviceHarness();
  await h.store.set(JSON.stringify({ version: 1, region: 'us-east', ...grantFor('us-east', 1, Date.now() - 3570_000) }));
  const before = JSON.parse(h.saved());
  const tankCalls = [];
  const transport = async (url, init) => {
    if (new URL(url).pathname.startsWith('/v1/command/device/')) return h.transport(url, init);
    tankCalls.push(init.headers.Authorization);
    return Response.json([{ name: 'fake-model' }]);
  };
  await run(['models'], { ...h, transport });
  const after = JSON.parse(h.saved());
  assert.deepEqual(h.calls.map(call => call.path), ['/v1/command/device/refresh']);
  assert.deepEqual(h.calls[0].body, { refresh_token: before.refresh_token });
  assert.notEqual(after.refresh_token, before.refresh_token);
  assert.deepEqual(tankCalls, [`Bearer ${after.access_token}`]);
  // A fresh token is used without another refresh.
  await run(['models'], { ...h, transport });
  assert.equal(h.calls.length, 1);
});

test('expired or revoked refresh asks for login; logout revokes on the server and clears the keychain', async () => {
  const h = deviceHarness();
  await h.store.set(JSON.stringify({ version: 1, region: 'us-east', ...grantFor('us-east', 1, Date.now() - 31 * 86400_000) }));
  await assert.rejects(run(['models'], h), /Login expired/);
  assert.equal(h.calls.length, 0);
  const value = { version: 1, region: 'za', ...grantFor('za', 1) };
  await h.store.set(JSON.stringify(value));
  await run(['logout'], h);
  assert.equal(h.saved(), null);
  assert.equal(h.calls.at(-1).url, 'https://za.tervory.com/v1/command/device/revoke');
  assert.deepEqual(h.calls.at(-1).body, { refresh_token: value.refresh_token });
  assert(!h.output().includes(value.refresh_token));
  await assert.rejects(run(['models'], h), /Login required/);
  const revoked = deviceHarness();
  await revoked.store.set(JSON.stringify({ version: 1, region: 'us-east', ...grantFor('us-east', 1, Date.now() - 3600_000) }));
  const refuse = async (url, init) => { revoked.calls.push(url); return Response.json({ error: 'invalid_grant' }, { status: 400 }); };
  await assert.rejects(run(['models'], { ...revoked, transport: refuse }), /expired or revoked/);
});

test('revoked token refused before command execution', async () => {
  const h = harness({ refusal: true });
  await assert.rejects(run(['ask', 'hello'], h), /Personal token refused/);
  assert(!h.output().includes('secret detail'));
});

test('refresh and device codes are redacted from terminal output', () => {
  const refresh = `tvr1.za.${TENANT_HEX}.${'r'.repeat(43)}`;
  const device = `tvd1.us-east.${'d'.repeat(43)}`;
  assert.equal(safe(`a ${refresh} b ${device} c`), 'a [redacted] b [redacted] c');
  const sanitizer = new StreamSanitizer();
  const out = [...`x ${refresh} y`].map(char => sanitizer.write(char)).join('') + sanitizer.end();
  assert.equal(out, 'x [redacted] y');
});

test('chat streams steps tokens receipt and N cancels without confirm', async () => {
  const h = harness({ answers: ['hello', 'N', '/exit'] });
  await run(['chat', 'fake-thread'], h);
  assert.match(h.output(), /\[running\] Tank: Fake step/);
  assert.match(h.output(), /Fake Tank answer/);
  assert.match(h.output(), /fake-ledger-1/);
  assert.match(h.output(), /baseline 0.000056/);
  assert(h.calls.some(c => c.body?.action === 'cancel'));
  assert(!h.calls.some(c => c.body?.action === 'confirm'));
});

test('one-shot defaults model to Auto and EOF cancels', async () => {
  const h = harness(); await run(['ask', 'hello'], h);
  assert.equal(h.calls.find(c => c.url.endsWith('/turn')).body.model, 'auto');
  assert.equal(h.calls.at(-1).body.action, 'cancel');
});

test('pinned model shows Tank-priced Auto comparison and never computes one', async () => {
  const h = harness(); await run(['ask', 'hello', '--model', 'fake-model'], h);
  assert.equal(h.calls.find(c => c.url.endsWith('/turn')).body.model, 'fake-model');
  assert.match(h.output(), /Auto comparison pending/);
  const priced = receiptLines({ rows: [{ ...row, requested_model: 'fake-model', auto_model: 'fake-auto', auto_cost_status: 'priced', auto_cost_usd: 0.05 }] });
  assert.match(priced.join(''), /Auto would have cost 0\.05 \(fake-auto, same tokens at policy model prices\)/);
  const unpriced = receiptLines({ rows: [{ ...row, requested_model: 'fake-model', auto_cost_status: 'pending', auto_cost_usd: 42 }] });
  assert(!unpriced.join('').includes('42'));
});

test('customer receipt (no model names from Tank) shows dollar figures and the Auto label only', () => {
  const { model, baseline_model, ...customer } = row;
  const lines = receiptLines({ rows: [{ ...customer, requested_model: 'fake-model', auto_cost_status: 'priced', auto_cost_usd: 0.05 }] }).join('\n');
  assert.match(lines, /^Step 1 \| cost .* \| would have cost .* \| saved .* \| What Auto would have cost: 0\.05$/);
  for (const leak of [model, 'fake-auto', 'policy model', 'ledger', 'tokens', 'fake-ledger']) assert(!lines.includes(leak), leak);
  const pending = receiptLines({ rows: [{ ...customer, requested_model: 'fake-model', auto_cost_status: 'pending', auto_cost_usd: 42 }] }).join('');
  assert.match(pending, /What Auto would have cost: comparison pending/);
  assert(!pending.includes('42'));
  assert(!receiptLines({ rows: [customer] }).join('').includes('Auto'));
});

test('client cannot propose founder tools', async () => {
  const h = harness();
  await assert.rejects(run(['run', 'skala', 'approve', '--args', '{}'], h), /not available/);
  assert(!h.calls.some(c => c.url.endsWith('/actions')));
});

test('known squad action uses code, creates pending card, requires explicit yes', async () => {
  const h = harness({ answers: ['yes'] });
  await run(['run', 'base', 'search', '--args', '{"q":"synthetic"}'], h);
  const action = h.calls.find(c => c.url.endsWith('/actions'));
  assert.deepEqual(action.body, { tool: 'base.search', arguments: { q: 'synthetic' } });
  assert(!h.calls.some(c => c.url.endsWith('/turn')));
  assert.equal(h.calls.find(c => c.url.includes('/cards/')).body.action, 'confirm');
});

test('CSV file intake maps existing batch records without model', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'cli-fake-'));
  try {
    const file = join(directory, 'synthetic.csv');
    await writeFile(file, 'name,city\n"Fake, Name",Testville\n');
    const h = harness(); await run(['run', 'base', 'batch', '--file', file], h);
    assert.deepEqual(h.calls.find(c => c.url.endsWith('/actions')).body.arguments,
      { batch: 'synthetic', records: [{ name: 'Fake, Name', city: 'Testville' }] });
    assert(!h.calls.some(c => c.url.endsWith('/turn')));
  } finally { await rm(directory, { recursive: true }); }
});

test('build prints brief and declines without worker action', async () => {
  const h = harness({ answers: ['no'] }); await run(['build', 'Synthetic test brief'], h);
  assert.match(h.output(), /Synthetic test brief/);
  assert.equal(h.calls.find(c => c.url.endsWith('/build')).body.brief_markdown, 'Synthetic test brief');
  assert.equal(h.calls.at(-1).body.action, 'cancel');
});

test('token region alone chooses endpoint; no mutable origin option', async () => {
  assert.equal(originFor(TOKEN), 'https://app.tervory.com');
  assert.equal(originFor(TOKEN.replace('us-east', 'za')), 'https://za.tervory.com');
  const h = harness({ token: TOKEN.replace('us-east', 'za') }); await run(['models'], h);
  assert(h.calls.every(c => c.url.startsWith('https://za.tervory.com/')));
  await assert.rejects(run(['models', '--origin', 'https://other.invalid'], h), /Invalid command option/);
  assert.throws(() => new Tank('malformed'), /refused/);
});

test('threads and receipts preserve ledger numbers without rounding or estimates', async () => {
  const h = harness(); await run(['threads'], h); await run(['receipts', 'fake-thread'], h);
  assert.match(h.output(), /spent 0.000028/); assert.match(h.output(), /cost 0.000028/);
  assert.match(h.output(), /saved 0.000028/);
});

test('transport errors do not expose token or response body and never retry', async () => {
  let count = 0;
  const tank = new Tank(TOKEN, async () => { count++; throw new Error(TOKEN); });
  await assert.rejects(tank.request('/v1/command/tools'), /^Error: Tank connection failed\.$/);
  assert.equal(count, 1);
  await assert.rejects(tank.request('https://other.invalid/'), /path refused/);
});

test('terminal controls and complete credential strings are redacted', () => {
  assert.equal(safe(`\x1b[2J${TOKEN}`), '[redacted]');
  assert(!safe('\x1b]52;c;ZXZpbA==\x07').includes('ZXZpbA'));
});

test('SSE handles UTF-8 and event boundaries split across reads', async () => {
  const bytes = new TextEncoder().encode(encode('token', { text: 'héllo' }));
  const body = new ReadableStream({ start(controller) {
    for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
    controller.close();
  } });
  const out = []; for await (const item of events(new Response(body))) out.push(item);
  assert.deepEqual(out, [{ kind: 'token', data: { text: 'héllo' } }]);
});

test('stream truncation is not silently accepted or retried', async () => {
  const response = new Response('event: token\ndata: {"text":"hello"}');
  await assert.rejects(async () => { for await (const _ of events(response)) {} }, /interrupted/);
});

test('stateful sanitizer redacts synthetic credentials and controls at every character boundary', () => {
  const cases = [
    [TOKEN, '[redacted]'],
    [`before ${TOKEN} after`, 'before [redacted] after'],
    ['héllo \x1b[2Jworld', 'héllo world'],
    ['before\x1b]52;c;ZXZpbA==\x07after', 'beforeafter'],
    ['before\x1b]52;c;ZXZpbA==\x1b\\after', 'beforeafter'],
    ['before\x9d52;c;ZXZpbA==\x9cafter', 'beforeafter'],
    [`${TOKEN.slice(0, 20)}\x1b[31m${TOKEN.slice(20)}`, '[redacted]'],
    ['before\x1b]unfinished', 'before'],
  ];
  for (const [raw, expected] of cases) {
    for (let cut = 0; cut <= raw.length; cut++) {
      const sanitizer = new StreamSanitizer();
      assert.equal(sanitizer.write(raw.slice(0, cut)) + sanitizer.write(raw.slice(cut)) + sanitizer.end(), expected,
        `cut ${cut}`);
    }
    const sanitizer = new StreamSanitizer();
    assert.equal([...raw].map(char => sanitizer.write(char)).join('') + sanitizer.end(), expected);
  }
});

test('actual showTurn path redacts split tokens and controls while streaming ordinary text', async () => {
  const attack = `héllo ${TOKEN} \x1b]52;c;ZXZpbA==\x1b\\ safe`;
  for (let cut = 0; cut <= attack.length; cut++) {
    let output = '';
    const tank = { request: async () => new Response(
      encode('token', { text: attack.slice(0, cut) }) + encode('token', { text: attack.slice(cut) }) + encode('done', {})) };
    await showTurn(tank, 'fake-thread', 'test', 'auto', [], { write: text => { output += text; } });
    assert.equal(output, 'héllo [redacted]  safe', `SSE split ${cut}`);
  }
  const sanitizer = new StreamSanitizer();
  assert.equal(sanitizer.write('ordinary streamed answer'), 'ordinary streamed answer');
});

test('UTF-8 transport splits at every byte preserve sanitizer state', async () => {
  const wire = encode('token', { text: `héllo ${TOKEN}` }) + encode('token', { text: '\x1b[2J world' });
  const bytes = new TextEncoder().encode(wire);
  for (let cut = 0; cut <= bytes.length; cut++) {
    const response = new Response(new ReadableStream({ start(controller) {
      controller.enqueue(bytes.slice(0, cut)); controller.enqueue(bytes.slice(cut)); controller.close();
    } }));
    const sanitizer = new StreamSanitizer(); let output = '';
    for await (const event of events(response)) output += sanitizer.write(event.data.text);
    output += sanitizer.end(); assert.equal(output, 'héllo [redacted] world', `byte ${cut}`);
  }
});

test('authoritative replace discards preview and seq duplicates never duplicate the final answer', async () => {
  let output = ''; const previews = []; let requests = 0;
  const tank = { request: async () => { requests++; return new Response(
    encode('token', { text: 'Provisional text', seq: 1 })
    + encode('token', { text: 'Provisional text', seq: 1 })
    + encode('token', { text: 'Validated final answer.', replace: true, seq: 2 })
    + encode('token', { text: 'Validated final answer.', replace: true, seq: 2 })
    + encode('done', { seq: 3 })); } };
  await showTurn(tank, 'fake-thread', 'test', 'auto', [], { write: text => { output += text; }, preview: text => previews.push(text) });
  assert.equal(output, 'Validated final answer.');
  assert(previews.some(value => value.startsWith('Provisional tex'))); assert.equal(requests, 1);
});

test('whole-string output strips C1 controls in titles cards models and receipts', () => {
  assert.equal(safe('title\x9d52;c;bad\x9c visible\x9b2J'), 'title visible');
  assert.equal(safe(`card ${TOKEN}`), 'card [redacted]');
});

test('job watcher observes steps and receipts until terminal state with read-only polls', async () => {
  let polls = 0; let waits = 0; let output = '';
  const tank = { json: async path => {
    assert.equal(path, '/v1/command/threads/fake-thread'); polls++;
    return { steps: [{ step_id: 'build', status: polls === 1 ? 'running' : 'done', actor: 'worker', label: 'Fake build', link: 'https://example.invalid/fake-pr' }],
      receipts: polls === 1 ? [] : [{ ...row, step_id: 'build' }] };
  } };
  await followThread(tank, 'fake-thread', { card_id: 'card', build_id: 'build' }, {
    write: text => { output += text; }, wait: async () => { waits++; },
  });
  assert.equal(polls, 2); assert.equal(waits, 1);
  assert.match(output, /\[running\]/); assert.match(output, /\[done\]/);
  assert.match(output, /fake-ledger-1/); assert.match(output, /fake-pr/);
});

test('job watcher stops at its bound and does not repeat action or turn', async () => {
  let polls = 0; let output = '';
  await followThread({ json: async () => { polls++; return { steps: [{ step_id: 'card', status: 'running', actor: 'base', label: 'Fake work' }] }; } },
    'fake-thread', { card_id: 'card' }, { write: text => { output += text; }, wait: async () => {} }, { limit: 2, interval: 0 });
  assert.equal(polls, 2); assert.match(output, /Reopen thread fake-thread/);
  assert.equal(output.match(/Fake work/g).length, 1);
});

test('job watcher cancellation and failed state stop without retry', async () => {
  const controller = new AbortController(); controller.abort(); let polls = 0;
  await assert.rejects(followThread({ json: async () => { polls++; } }, 'fake-thread', {},
    { signal: controller.signal, write() {} }), /cancelled/);
  assert.equal(polls, 0);
  await followThread({ json: async () => { polls++; return { cards: [{ card_id: 'card', status: 'unknown' }] }; } },
    'fake-thread', { card_id: 'card' }, { write() {} });
  assert.equal(polls, 1);
});

test('reopened chat shows saved messages and follows existing job without another model turn', async () => {
  let polls = 0; let output = '';
  const h = harness({ answers: ['/exit'] });
  h.transport = async (url, init) => {
    assert.equal(init.method, 'GET'); assert(url.endsWith('/threads/fake-thread')); polls++;
    return Response.json({ messages: [{ role: 'assistant', content: 'Saved answer' }],
      cards: [{ card_id: 'card', status: 'confirmed', build_id: 'build' }],
      steps: [{ step_id: 'build', actor: 'worker', label: 'Saved work', status: polls === 1 ? 'running' : 'done' }] });
  };
  h.io.write = text => { output += text; };
  await run(['chat', 'fake-thread'], h);
  assert.match(output, /Saved answer/); assert.match(output, /Saved work/);
  assert.equal(polls, 2);
});

// Reuse the production run/stream/receipt code for a clearly labeled recording.
export async function fakeTranscript() {
  const h = harness({ answers: ['hello', 'N', '/exit'] });
  await run(['chat', 'fake-thread', '--model', 'fake-model'], h);
  return h.output();
}

test('review of #506 B2: help hides models and --model; founder roster is formatted, never raw JSON', async () => {
  let output = ''; const io = { write: value => { output += value; } };
  await run(['help'], { io });
  assert.doesNotMatch(output, /models|--model|model/);
  const roster = [{ name: 'fake-model', provider_model: 'provider/secret', input_usd_per_m: 1, output_usd_per_m: 2 }];
  const h = harness();
  output = '';
  await run(['models'], { ...h, io: { ...h.io, write: value => { output += value; } }, transport: async (url, init) =>
    new URL(url).pathname === '/v1/command/models' ? Response.json(roster) : h.transport(url, init) });
  assert.equal(output, 'fake-model | in $1/M | out $2/M\n');
  assert.doesNotMatch(output, /provider|[{}"]/);
});

test('review of #506 B2: a customer --model pin is refused by Tank (403) and the CLI names no model', async () => {
  const h = harness(); let output = '';
  const transport = async (url, init) => {
    const path = new URL(url).pathname;
    if (path === '/v1/command/threads' && init.method === 'POST') return Response.json({ thread_id: 'fake-thread' });
    if (path.endsWith('/turn')) return Response.json({ detail: 'Only Auto is available on this account' }, { status: 403 });
    return h.transport(url, init);
  };
  await assert.rejects(run(['ask', 'hello', '--model', 'secret-pin'], { ...h, io: { ...h.io, write: v => { output += v; } }, transport }),
    error => error.message === 'Only Auto is available on this account. Nothing was sent.' && !error.message.includes('secret-pin'));
  assert(!output.includes('secret-pin'));
});

test('review of #506 B2: a customer receipt on a historic pin keeps the Auto line without any model field', () => {
  const [line] = receiptLines({ rows: [{ cost_usd: 0.02, baseline_cost_usd: 0.5, saved_usd: 0.48, auto_cost_usd: 0.05, auto_cost_status: 'priced' }] });
  assert.equal(line, 'Step 1 | cost 0.02 | would have cost 0.5 | saved 0.48 | What Auto would have cost: 0.05');
  const [auto] = receiptLines({ rows: [{ cost_usd: 0.02, baseline_cost_usd: 0.5, saved_usd: 0.48 }] });
  assert.equal(auto, 'Step 1 | cost 0.02 | would have cost 0.5 | saved 0.48');
});

test('paid-step pause leaves an unattended approval pending without inventing an answer', async () => {
  const calls=[];let output='';
  const card={card_id:'placeholder-approval',kind:'paid_run',tool:'paid_steps.approve',summary:'placeholder paid step',status:'pending'};
  const tank={request:async()=>new Response(encode('step',{step_id:'placeholder-turn',status:'planned',actor:'Tervory',label:'placeholder step',paused:true,phase_finished:true})+encode('card',card)),json:async(...args)=>{calls.push(args);throw new Error('Unattended approval must not be decided');}};
  await showTurn(tank,'placeholder-thread','placeholder request','auto',[],{write:text=>{output+=text;},interactive:false});
  assert.equal(calls.length,0);
  assert(output.includes('placeholder paid step'));
  assert(!output.includes('stream interrupted'));
});
