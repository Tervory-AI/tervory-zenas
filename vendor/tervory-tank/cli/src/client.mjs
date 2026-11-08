import { basename } from 'node:path';
import { codeSession, dollars } from './code-mode.mjs';
import { redactSecrets, LIMITS } from './code-tools.mjs';
import { inputText } from './input-file.mjs';
import { keyringEntry } from './keyring.mjs';
import { loadMCP, mcpKeyring, serverIdentity } from './mcp.mjs';
import { ConfigurationError, LOGIN_SETTING, keyringUnavailable } from './configuration.mjs';
import { emptyState, EMPTY } from './empty-state.mjs';
import { prepareRun, dryRunResult, dryRunText } from './run-request.mjs';
import { installedRelease, packageVersion, update } from './update.mjs';
import { boundedFetch, boundedOperation, responseBytes, DeadlineError } from './http.mjs';

const ORIGINS = Object.freeze({ 'us-east': 'https://app.tervory.com', za: 'https://za.tervory.com' });
const ENVELOPE = /^tv1\.(us-east|za)\.[0-9a-f]{32}\.[A-Za-z0-9_-]{43}$/;

export function originFor(token) {
  if (typeof token !== 'string' || !ENVELOPE.test(token)) throw new Error('Personal token refused.');
  return ORIGINS[token.split('.')[1]];
}

// Terminal output is untrusted server data: strip terminal controls and bearer
// strings even if an upstream error or model response accidentally contains one.
export function safe(value) {
  const sanitizer = new StreamSanitizer();
  return sanitizer.write(value) + sanitizer.end();
}
// Access (tv1), refresh (tvr1) and device-code (tvd1) credentials are never shown.
const redact = value => value.replace(/(?:tv[rd]?1\.(?:us-east|za)\.|tzd1\.)[A-Za-z0-9_.-]+/g, '[redacted]');
const PREFIXES = ['tv1.', 'tvr1.', 'tvd1.', 'tzd1.'];

/** Keep terminal-control and credential state across arbitrary SSE chunks. */
export class StreamSanitizer {
  #escape = '';
  #candidate = '';
  #dropping = false;
  write(value) {
    let out = '';
    const printable = char => {
      if (this.#dropping) {
        if (/^[A-Za-z0-9_.-]$/.test(char)) return;
        this.#dropping = false;
      }
      if (PREFIXES.some(prefix => this.#candidate.startsWith(prefix))) {
        if (/^[A-Za-z0-9_.-]$/.test(char)) {
          this.#candidate += char;
          if (this.#candidate.length > 256) {
            out += '[redacted]'; this.#candidate = ''; this.#dropping = true;
          }
          return;
        }
        out += redact(this.#candidate); this.#candidate = '';
      }
      this.#candidate += char;
      while (this.#candidate && !PREFIXES.some(prefix => prefix.startsWith(this.#candidate))) {
        out += this.#candidate[0]; this.#candidate = this.#candidate.slice(1);
      }
    };
    for (const char of String(value)) {
      if (this.#escape === 'esc') {
        this.#escape = char === '[' ? 'csi' : [']', 'P', '^', '_'].includes(char) ? 'string' : '';
        continue;
      }
      if (this.#escape === 'csi') {
        if (/^[\x40-\x7e]$/.test(char)) this.#escape = '';
        continue;
      }
      if (this.#escape === 'string') {
        if (char === '\x07' || char === '\x9c') this.#escape = '';
        else if (char === '\x1b') this.#escape = 'string-esc';
        continue;
      }
      if (this.#escape === 'string-esc') {
        this.#escape = char === '\\' ? '' : char === '\x1b' ? 'string-esc' : 'string';
        continue;
      }
      if (char === '\x1b') { this.#escape = 'esc'; continue; }
      if (char === '\x9b') { this.#escape = 'csi'; continue; }
      if (['\x90', '\x9d', '\x9e', '\x9f'].includes(char)) { this.#escape = 'string'; continue; }
      if (/[\x00-\x08\x0b-\x1f\x7f-\x9f]/.test(char)) continue;
      printable(char);
    }
    return out;
  }
  end() {
    const out = redact(this.#candidate);
    this.#candidate = ''; this.#escape = ''; this.#dropping = false;
    return out;
  }
}

export async function keychain() {
  const entry = keyringEntry('@tervory/cli', 'personal-token');
  return {
    get: () => entry.getPassword(),
    set: value => entry.setPassword(value),
    remove: () => entry.deletePassword(),
  };
}

const REFRESH = /^tvr1\.(us-east|za)\.[0-9a-f]{32}\.[A-Za-z0-9_-]{43}$/;
const DEVICE_ERRORS = ['authorization_pending', 'slow_down', 'access_denied', 'expired_token', 'invalid_grant'];
const SIGNIN_ORIGINS = { 'us-east': 'https://auth.tervory.com', za: 'https://auth.tervory.com' };

export function identityApprovalUrl(start, region) {
  if (start.identity_provider !== 'trigger-signin' || !SIGNIN_ORIGINS[region] || typeof start.user_code !== 'string' || !/^[A-Z0-9-]{4,40}$/.test(start.user_code)) {
    throw new Error('Login refused by Tank.');
  }
  try {
    const base = new URL(start.verification_uri);
    const url = new URL(start.verification_uri_complete || start.verification_uri);
    for (const value of [base, url]) {
      if (value.origin !== SIGNIN_ORIGINS[region] || value.username || value.password || value.hash) {
        throw new Error();
      }
    }
    return url.href;
  } catch { throw new Error('Login refused by Tank.'); }
}

/** Unauthenticated device-login call to the region's public origin (Workspace for us-east, Tank for ZA). */
// Each device-login call is bounded: a Tank that accepts the connection and never
// answers must not stall login (or the poll loop past its 10-minute deadline).
export const DEVICE_TIMEOUT_MS = 10_000;

export async function deviceCall(region, action, body, transport = fetch, headers = {}, timeoutMs = DEVICE_TIMEOUT_MS, signal) {
  if (!ORIGINS[region] || !['code', 'token', 'refresh', 'revoke'].includes(action)) throw new Error('Login refused by Tank.');
  // Hard deadline over the whole call, body included (http.mjs: undici + GC can ignore the signal).
  let result;
  try {
    result = await boundedFetch(transport, `${ORIGINS[region]}/v1/command/device/${action}`, {
      method: 'POST', signal, headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body),
    }, timeoutMs, async response => ({ response, payload: await jsonBody(response).catch(error => {
      if (error?.name === 'AbortError') throw error; return {};
    }) }));
  } catch { throw new Error('Tank connection failed.'); }
  const { response, payload } = result;
  if (response.ok) return payload;
  return { error: DEVICE_ERRORS.includes(payload?.error) ? payload.error : 'failed', status: response.status };
}

function credential(grant, region) {
  if (!grant || !ENVELOPE.test(grant.access_token) || !REFRESH.test(grant.refresh_token)
      || grant.access_token.split('.')[1] !== region || grant.refresh_token.split('.')[1] !== region
      || !Number.isFinite(Date.parse(grant.expires_at)) || !Number.isFinite(Date.parse(grant.refresh_expires_at))) {
    throw new Error('Login refused by Tank.');
  }
  const { access_token, refresh_token, expires_at, refresh_expires_at } = grant;
  return JSON.stringify({ version: 1, region, access_token, refresh_token, expires_at, refresh_expires_at });
}

function stored(raw) {
  if (typeof raw !== 'string' || !raw) return null;
  // A personal token saved by CLI 0.1 keeps working until it is revoked.
  if (ENVELOPE.test(raw)) return { legacy: true, access_token: raw };
  try {
    const value = JSON.parse(raw);
    if (value?.version === 1 && ENVELOPE.test(value.access_token) && REFRESH.test(value.refresh_token)) return value;
  } catch { /* fall through */ }
  return null;
}

/** A valid access token, refreshed (and rotated in the keychain) shortly before expiry. */
export async function session(store, transport = fetch, now = Date.now) {
  let raw;
  try { raw = await store.get(); } catch { throw keyringUnavailable(); }
  const value = stored(raw);
  if (!value) throw new ConfigurationError(`Login required. Missing OS keyring entry ${LOGIN_SETTING}. Run tervory login.`, [LOGIN_SETTING]);
  if (value.legacy || Date.parse(value.expires_at) - now() > 60_000) return value.access_token;
  if (Date.parse(value.refresh_expires_at) <= now()) throw new Error('Login expired. Run tervory login.');
  const grant = await deviceCall(value.region, 'refresh', { refresh_token: value.refresh_token }, transport);
  if (grant.error) {
    throw new Error(grant.error === 'invalid_grant' ? 'Login expired or revoked. Run tervory login.' : 'Tank refused the login refresh.');
  }
  const refreshed = credential(grant, value.region);
  try { await store.set(refreshed); } catch { throw keyringUnavailable('written'); }
  return grant.access_token;
}

async function login(region, { store, transport, io }) {
  // Do not begin device approval when the required credential store is absent.
  try { await store.get?.(); } catch { throw keyringUnavailable(); }
  if (typeof store.set !== 'function') throw keyringUnavailable('written');
  const name = String(io.deviceName?.() ?? 'tervory-cli').replace(/[\x00-\x1f\x7f-\x9f]/g, '').trim().slice(0, 100) || 'tervory-cli';
  const start = await deviceCall(region, 'code', { device_name: name, approval: 'trigger-signin' }, transport, {}, DEVICE_TIMEOUT_MS, io.signal);
  if (start.error || typeof start.device_code !== 'string'
      || !start.device_code.startsWith('tzd1.')) throw new Error('Login refused by Tank.');
  const url = identityApprovalUrl(start, region);
  io.write(`Approve this device in your browser (${safe(name)}, ${region}):\n  ${url}\nConfirm the page shows code ${start.user_code}.\n`);
  await boundedOperation(() => io.open?.(url), DEVICE_TIMEOUT_MS, io.signal)
    .catch(() => io.write('Login browser could not open. Open the printed link to approve this device.\n'));
  const sleep = io.sleep ?? (ms => new Promise(resolve => setTimeout(resolve, ms)));
  let interval = Math.min(30, Math.max(1, Number(start.interval) || 5)) * 1000;
  const deadline = Date.now() + Math.min(600, Math.max(1, Number(start.expires_in) || 600)) * 1000;
  while (Date.now() < deadline) {
    if (io.signal?.aborted) throw new Error('Login cancelled.');
    await boundedOperation(() => sleep(Math.min(interval, deadline - Date.now())), Math.max(1, deadline - Date.now()), io.signal)
      .catch(() => { throw new Error(io.signal?.aborted ? 'Login cancelled.' : 'Login code expired. Run tervory login again.'); });
    if (Date.now() >= deadline) break;
    const grant = await deviceCall(region, 'token', { device_code: start.device_code }, transport, {}, Math.min(DEVICE_TIMEOUT_MS, deadline - Date.now()), io.signal);
    if (!grant.error) {
      const saved = credential(grant, region);
      try { await store.set(saved); } catch { throw keyringUnavailable('written'); }
      io.write(`Signed in on ${region}. Credential saved in the OS keychain; it refreshes automatically.\n`);
      return;
    }
    // A 401/403 never turns into a grant by waiting: stop instead of polling to expiry.
    if (grant.status === 401 || grant.status === 403) throw new Error(`Login refused by Tank (HTTP ${grant.status}). Run tervory login again.`);
    if (grant.error === 'authorization_pending') continue;
    if (grant.error === 'slow_down') { interval = Math.min(30_000, interval + 5000); continue; }
    if (grant.error === 'access_denied') throw new Error('Login denied in the browser.');
    if (grant.error === 'expired_token') break;
    throw new Error('Login refused by Tank.');
  }
  throw new Error('Login code expired. Run tervory login again.');
}

async function logout({ store, transport, io }) {
  const value = stored(await store.get().catch(() => null));
  let revoked = false;
  if (value && !value.legacy) {
    // Best effort: the server credential stops working even if this copy leaked.
    const result = await deviceCall(value.region, 'revoke', { refresh_token: value.refresh_token }, transport, {}, DEVICE_TIMEOUT_MS, io.signal).catch(() => null);
    revoked = Boolean(result && !result.error);
  }
  await store.remove();
  io.write(value && !value.legacy ? revoked ? 'Signed out. The credential is revoked and removed from the OS keychain.\n'
    : 'Signed out locally. Removed from the OS keychain. Server revocation could not be confirmed; review your signed-in devices in account settings.\n'
    : 'Signed out. Removed from the OS keychain.\n');
}

// Bounds for authenticated calls. JSON answers include synchronous card confirmations,
// so the bound is generous; a stream only has to start within HEADERS_TIMEOUT_MS and then
// must deliver bytes (Tank sends a heartbeat every 0.25 s while a turn runs) every STREAM_IDLE_MS.
export const HEADERS_TIMEOUT_MS = 30_000;
export const JSON_TIMEOUT_MS = 300_000;
export const STREAM_IDLE_MS = 120_000;
const RESPONSE_BYTES = 2_000_000; // Same limit as the existing Command event buffer.
const jsonBody = async response => JSON.parse(await responseBytes(response, RESPONSE_BYTES));

export class Tank {
  #token;
  constructor(token, transport = fetch, signal) {
    this.origin = originFor(token);
    this.#token = token;
    this.transport = transport;
    this.signal = signal;
  }
  async #call(path, body, method, timeoutMs, read, stop) {
    if (!/^\/v1\/command\/[A-Za-z0-9/?=&._%-]+$/.test(path)) throw new Error('Command path refused.');
    if (path.split(/[/?]/).some(part => /^(?:\.|%2e){1,2}$/i.test(part)) || /%2f|%5c/i.test(path)) throw new Error('Command path refused.');
    const signal = stop && this.signal ? AbortSignal.any([this.signal, stop]) : stop ?? this.signal;
    try {
      return await boundedFetch(this.transport, this.origin + path, {
        method, signal, headers: { Authorization: `Bearer ${this.#token}`, 'Content-Type': 'application/json' },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      }, timeoutMs, async response => {
        if (!response.ok) {
          // Do not print error bodies, request objects or transport exceptions.
          await response.body?.cancel?.().catch(() => {});
          const error = new Error(response.status === 401 ? 'Personal token refused.' : `Tank refused the command (${response.status}).`);
          error.status = response.status; error.tank = true;
          throw error;
        }
        return read(response);
      });
    } catch (error) {
      if (error.tank) throw error;
      if (this.signal?.aborted) throw new Error('Command cancelled; no action was retried.');
      if (error instanceof DeadlineError) throw new Error('Tank did not answer in time; nothing was retried. Reopen the thread to see its state.');
      throw new Error('Tank connection failed.');
    }
  }
  /** A streaming response: the deadline covers the headers; events() bounds each read. */
  async request(path, body, method = body === undefined ? 'GET' : 'POST') {
    const stop = new AbortController();
    const response = await this.#call(path, body, method, HEADERS_TIMEOUT_MS, answer => answer, stop.signal);
    STREAM_STOPS.set(response, stop);
    return response;
  }
  /** A JSON answer: the deadline covers the whole call, body included. */
  async json(path, body, method = body === undefined ? 'GET' : 'POST') {
    return this.#call(path, body, method, JSON_TIMEOUT_MS, jsonBody);
  }
  async codeTurn(thread, body, preview) {
    const response = await this.request(`/v1/command/threads/${encodeURIComponent(thread)}/code-stream`, body);
    let result;
    for await (const event of events(response, { signal:this.signal })) {
      if (event.kind === 'code_preview' && typeof event.data.text === 'string') preview?.(event.data.text);
      else if (event.kind === 'code_result') result = event.data;
      else if (event.kind === 'error') throw new Error('Code stream failed; nothing was retried.');
    }
    if (!result) throw new Error('Code stream interrupted; nothing was retried.');
    return result;
  }
}

// The abort for each streaming response from Tank.request, so events() closes the
// connection itself (not only the reader) whenever it stops early: idle, cancel, an
// invalid or oversized event, or the caller leaving the loop.
const STREAM_STOPS = new WeakMap();

export async function* events(response, { idleMs = STREAM_IDLE_MS, totalMs = JSON_TIMEOUT_MS, signal } = {}) {
  if (!response.body) throw new Error('Tank stream missing.');
  const reader = response.body.getReader();
  const abortUpstream = () => { STREAM_STOPS.get(response)?.abort(); reader.cancel().catch(() => {}); };
  let finished = false;
  const deadline = Date.now() + totalMs;
  // Every read is raced against an idle deadline and the caller's signal, so a stalled
  // stream cannot hang the CLI even if undici ignores the abort (see http.mjs).
  const read = () => new Promise((resolve, reject) => {
    const stop = error => { clearTimeout(timer); signal?.removeEventListener('abort', onAbort); abortUpstream(); reject(error); };
    const onAbort = () => stop(new Error('Command cancelled; no action was retried.'));
    const remaining = deadline - Date.now();
    const timer = setTimeout(() => stop(new Error(remaining < idleMs
      ? 'Tank stream exceeded its time limit; the turn was not retried. Reopen the thread to see its state.'
      : 'Tank stream stalled; the turn was not retried. Reopen the thread to see its state.')), Math.max(0, Math.min(idleMs, remaining)));
    if (remaining <= 0) { stop(new Error('Tank stream exceeded its time limit; the turn was not retried. Reopen the thread to see its state.')); return; }
    if (signal?.aborted) return onAbort();
    signal?.addEventListener('abort', onAbort, { once: true });
    reader.read().then(value => { clearTimeout(timer); signal?.removeEventListener('abort', onAbort); resolve(value); },
      error => { clearTimeout(timer); signal?.removeEventListener('abort', onAbort); reject(error); });
  });
  const decoder = new TextDecoder();
  let buffer = ''; let received = 0;
  try {
    while (true) {
      const { value, done } = await read();
      received += value?.byteLength ?? 0;
      if (received > RESPONSE_BYTES) throw new Error('Tank stream exceeds the limit; the turn was not retried. Reopen the thread to see its state.');
      buffer += decoder.decode(value, { stream: !done });
      buffer = buffer.replace(/\r\n/g, '\n');
      if (buffer.length > 2_000_000) throw new Error('Tank event exceeds the limit.');
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
        let kind = 'message'; const data = [];
        for (const line of frame.split('\n')) {
          if (line.startsWith('event:')) kind = line.slice(6).trim();
          if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
        }
        if (data.length) {
          let parsed;
          try { parsed = JSON.parse(data.join('\n')); } catch { throw new Error('Tank sent an invalid event.'); }
          yield { kind, data: parsed };
        }
      }
      if (done) {
        finished = true;
        if (buffer.trim()) throw new Error('Tank stream interrupted.');
        break;
      }
    }
  } finally {
    if (!finished) abortUpstream();
    try { reader.releaseLock(); } catch { /* a cancelled read is still settling */ }
  }
}

const amount = value => value === null || value === undefined ? 'pending' : String(value);
export function receiptLines(receipt, { empty = false } = {}) {
  const rows = receipt.rows ?? [];
  if (!rows.length) return empty ? [...EMPTY.receipts] : ['Receipt pending'];
  return rows.map((row, index) => {
    // Tank prices pinned turns' same recorded tokens at the frozen Auto policy price; the CLI never computes money.
    const priced = row.auto_cost_status === 'priced' && typeof row.auto_cost_usd === 'number';
    // Customers never receive requested_model (Tank's customer receipt is an allowlist);
    // Tank sets auto_cost_status only on a pinned step, so it marks one for both views.
    const pinned = (row.requested_model && row.requested_model !== 'auto') || typeof row.auto_cost_status === 'string';
    // Tank sends model names only to the founder (as the Workspace receipt shows them only
    // to the founder). Without them this is a customer receipt: dollar figures only.
    if (typeof row.model !== 'string') {
      return `Step ${index + 1} | cost ${amount(row.cost_usd)} | would have cost ${amount(row.baseline_cost_usd)}`
        + ` | saved ${amount(row.saved_usd)}`
        + (pinned ? ` | What Auto would have cost: ${priced ? amount(row.auto_cost_usd) : 'comparison pending'}` : '');
    }
    const auto = priced
      ? `would have cost ${amount(row.auto_cost_usd)} (${row.auto_model ?? 'policy model'}, same tokens at policy model prices)` : 'comparison pending';
    return `${row.model === 'code' ? 'code (no model)' : row.model} | tokens ${row.tokens_in}/${row.tokens_out}`
      + ` | cost ${amount(row.cost_usd)} | baseline ${row.baseline_model ? `${row.baseline_model} ` : ''}${amount(row.baseline_cost_usd)}`
      + ` | saved ${amount(row.saved_usd)} | ledger ${row.ledger_id}`
      + (pinned ? ` | Auto ${auto}` : '');
  });
}

export async function confirmCard(tank, card, io) {
  io.write(`${safe(card.summary)}\n`);
  if (card.tool === 'paid_steps.approve' && io.interactive !== true) return card;
  // Any response except an explicit yes cancels, including EOF.
  const answer = io.interactive !== true ? null : await io.prompt('Confirm [y/N] ');
  const action = /^(y|yes)$/i.test(answer?.trim() ?? '') ? 'confirm' : 'cancel';
  return tank.json(`/v1/command/cards/${encodeURIComponent(card.card_id)}`, { action });
}

export async function showTurn(tank, thread, text, model, attachments, io) {
  text = redactSecrets(text);
  let response;
  try { response = await tank.request(`/v1/command/threads/${encodeURIComponent(thread)}/turn`, { text, model, attachments }); } catch (error) {
    // Pinning is founder-only; Tank's 403 names no model and neither does this.
    if (error.status === 403 && model !== 'auto') throw new Error('Only Auto is available on this account. Nothing was sent.');
    throw error;
  }
  const cards = new Map(); const receipts = []; let complete = false; let lastSeq = 0;
  let sanitizer = new StreamSanitizer(); let answer = '';
  for await (const event of events(response, { signal: tank.signal })) {
    if (Number.isSafeInteger(event.data.seq)) {
      if (event.data.seq <= lastSeq) continue;
      lastSeq = event.data.seq;
    }
    switch (event.kind) {
      case 'token':
        if (event.data.replace) { sanitizer = new StreamSanitizer(); answer = ''; }
        answer += sanitizer.write(event.data.text);
        if (answer.length > 2_000_000) throw new Error('Tank answer exceeds the limit.');
        io.preview?.(answer); break;
      case 'step':
        if (event.data.phase_finished === true) { complete = true; answer = ''; sanitizer = new StreamSanitizer(); }
        io.preview?.('');
        io.write(`\n[${safe(event.data.status)}] ${safe(event.data.actor)}: ${safe(event.data.label)}\n`);
        io.preview?.(answer); break;
      case 'receipt': receipts.push(event.data); break;
      case 'card': if (!event.data.status || event.data.status === 'pending') cards.set(event.data.card_id, event.data); break;
      case 'error': io.preview?.(''); throw new Error('Tank could not complete the turn.');
      case 'done': complete = true; break;
    }
  }
  io.preview?.('');
  if (!complete) throw new Error('Tank stream interrupted; the turn was not retried.');
  io.write(answer + sanitizer.end());
  for (const receipt of receipts) io.write(`\n${receiptLines(receipt).map(safe).join('\n')}\n`);
  for (const card of cards.values()) {
    const result = await confirmCard(tank, card, io);
    if (result.status !== 'cancelled' && !(result.tool === 'paid_steps.approve' && result.status === 'pending')) await followThread(tank, thread, result, io);
  }
}

export async function followThread(tank, thread, card, io, { limit = 240, interval = 500 } = {}) {
  const seen = new Map();
  for (let poll = 0; poll < limit; poll++) {
    if (io.signal?.aborted) throw new Error('Command cancelled; no action was retried.');
    const snapshot = await tank.json(`/v1/command/threads/${encodeURIComponent(thread)}`);
    const tracked = new Set([card.card_id, card.build_id].filter(Boolean));
    const steps = (snapshot.steps ?? []).filter(step => tracked.has(step.step_id));
    for (const step of steps) {
      const value = JSON.stringify(step);
      if (seen.get(step.step_id) === value) continue;
      seen.set(step.step_id, value);
      io.write(`[${safe(step.status)}] ${safe(step.actor)}: ${safe(step.label)}${step.link ? ` ${safe(step.link)}` : ''}\n`);
    }
    for (const row of snapshot.receipts ?? []) {
      if (!tracked.has(row.step_id)) continue;
      const key = `receipt:${row.ledger_id}`; const value = JSON.stringify(row);
      if (seen.get(key) === value) continue;
      seen.set(key, value); io.write(receiptLines({ rows: [row] }).map(safe).join('\n') + '\n');
    }
    const stored = (snapshot.cards ?? []).find(item => item.card_id === card.card_id);
    if (['failed', 'unknown', 'cancelled'].includes(stored?.status)) {
      io.write(`Work ${stored.status === 'unknown' ? 'outcome is unknown' : stored.status}; nothing was retried. Reopen thread ${safe(thread)} to inspect its state.\n`);
      return;
    }
    if (steps.length && steps.every(step => ['done', 'failed'].includes(step.status))
        && !steps.some(step => step.receipt_status === 'pending')) return;
    if (poll + 1 < limit) {
      if (io.wait) await io.wait(interval);
      else await new Promise((resolve, reject) => {
        const cancel = () => { clearTimeout(timer); reject(new Error('Command cancelled; no action was retried.')); };
        const timer = setTimeout(() => { io.signal?.removeEventListener('abort', cancel); resolve(); }, interval);
        io.signal?.addEventListener('abort', cancel, { once: true });
      });
    }
  }
  io.write(`Work is still pending. Reopen thread ${safe(thread)} to continue watching.\n`);
}

export function options(argv) {
  const args = []; const flags = {};
  for (let i = 0; i < argv.length; i++) {
    if (!argv[i].startsWith('--')) { args.push(argv[i]); continue; }
    const flag = argv[i].slice(2);
    if (['resume','plain','dry-run'].includes(flag) && flags[flag] === undefined) { flags[flag] = true; continue; }
    if (!['model', 'thread', 'file', 'args', 'region', 'version', 'output'].includes(flag) || flags[flag] !== undefined || !argv[i + 1] || argv[i + 1].startsWith('--')) {
      throw new Error('Invalid command option.');
    }
    flags[flag] = argv[++i];
  }
  return { args, flags };
}

export async function run(argv, { store, transport, io, env = process.env }) {
  if (argv[0] === 'mcp' && argv[1] === 'secret') {
    if (argv.length !== 4 || io.interactive !== true || !io.secret || !/^[A-Za-z0-9_.-]+$/.test(argv[2]) || !/^[A-Za-z0-9_.-]+$/.test(argv[3])) throw new Error('Code MCP refused.');
    const entry = await mcpKeyring(argv[2], argv[3]);
    const value = await io.secret('MCP secret (hidden): ');
    if (!value) throw new Error('Code MCP refused.');
    await entry.setPassword(value); io.write('MCP secret saved in OS keyring.\n'); return;
  }
  if (argv[0] === '-p') {
    const { args: extra, flags: headlessFlags } = options(argv.slice(2));
    const output = headlessFlags.output ?? 'text';
    if (!argv[1]?.trim() || extra.length || Object.keys(headlessFlags).some(key => key !== 'output') || !['json', 'text'].includes(output)) throw new Error('Invalid headless command option.');
    let result;
    try {
      const token = await session(store, transport);
      result = await codeSession(new Tank(token, transport, io.signal), '.', 'auto',
        { ...io, interactive: false, prompt: async () => null, write() {} }, receiptLines, safe,
        { task: argv[1], headless: true });
    } catch (error) {
      result = { status: 'error', answer: error instanceof ConfigurationError ? error.message : 'Code task failed; nothing was retried.', cost_usd: null, turns: [],
        ...(error instanceof ConfigurationError ? { missing_settings: error.settings } : {}) };
    }
    io.write(output === 'json' ? JSON.stringify(result) + '\n'
      : `${safe(redactSecrets(result.answer ?? ''))}\n${result.turns.map(turn => `Model ${turn.model} | cost ${dollars(turn.cost_usd)}\n`).join('')}Status ${result.status}\nCost ${dollars(result.cost_usd)}\n`);
    if (result.status !== 'completed') {
      const error = new Error('Code task needs attention.'); error.reported = true; error.exitCode = result.status === 'needs_input' ? 2 : 1; throw error;
    }
    return result;
  }
  const { args, flags } = options(argv); const [name, ...rest] = args;
  if (flags['dry-run']) {
    if (name !== 'run' || !['json', 'text'].includes(flags.output ?? 'text')) throw new Error('Invalid dry-run command option.');
    const result = dryRunResult(await prepareRun(rest, flags));
    io.write(flags.output === 'json' ? JSON.stringify(result) + '\n' : dryRunText(result));
    return result;
  }
  if (flags.output !== undefined || ((flags.resume !== undefined || flags.plain !== undefined) && name !== 'code')) throw new Error('Invalid command option.');
  if (!name || name === 'help') {
    io.write('tervory login [--region us-east|za] | logout | chat [thread] | code [folder] [--resume] [--plain] | -p "<task>" [--output json|text] | ask "question" | threads | run <agent> <task> [--file path] [--args JSON] [--dry-run --output json|text] | build "change" | receipts <thread|task> | memory view|edit|forget|off|control <args> | mcp secret <server> <name> | mcp block|unblock <server> | update [--version X.Y.Z] | version\n');
    return;
  }
  if (name === 'update') {
    const { version, ...other } = flags;
    if (rest.length || Object.keys(other).length) throw new Error('Update accepts only --version X.Y.Z.');
    // Pin: TERVORY_CLI_VERSION (the same variable install.sh honours) when --version is not given.
    const pinned = (env.TERVORY_CLI_VERSION ?? '').trim() || undefined;
    return update({ transport: transport ?? fetch, io, version: version ?? pinned });
  }
  if (flags.version !== undefined) throw new Error('Invalid command option.');
  if (name === 'version') {
    if (rest.length || Object.keys(flags).length) throw new Error('Invalid command option.');
    const release = await installedRelease();
    io.write(`tervory ${release ? `${release.version} (${release.platform})` : `${await packageVersion()} (development checkout)`}\n`);
    return;
  }
  if (name === 'login') {
    const { region = 'us-east', ...other } = flags;
    if (rest.length || Object.keys(other).length || !ORIGINS[region]) {
      throw new Error('Trigger sign-in accepts only --region us-east or --region za.');
    }
    return login(region, { store, transport, io });
  }
  if (flags.region !== undefined) throw new Error('Invalid command option.');
  if (name === 'logout') return logout({ store, transport, io });
  const runRequest = name === 'run' ? await prepareRun(rest, flags) : null;
  const attachments = ['ask', 'chat'].includes(name) && flags.file
    ? [{ name: basename(flags.file), text: await inputText(flags.file, LIMITS.fileBytes) }] : [];
  const token = await session(store, transport);
  const tank = new Tank(token, transport, io.signal);
  if (name === 'mcp') {
    if (Object.keys(flags).length || rest.length !== 2 || !['block','unblock'].includes(rest[0])) throw new Error('Code MCP refused.');
    const config = (await loadMCP())[rest[1]];
    const server_id = config ? serverIdentity(config) : rest[1];
    io.write(`MCP policy: ${safe(server_id)}\n`);
    if (io.interactive !== true || (await io.prompt('Change tenant MCP policy [y/N] '))?.trim().toLowerCase() !== 'y') return;
    await tank.json('/v1/command/code-mcp-policy', { server_id, blocked: rest[0] === 'block' }, 'PUT');
    io.write('MCP policy updated.\n'); return;
  }

  if (name === 'memory') {
    // Live view/edit/forget/switch-off via Tank /v1/command/opli/memory.* (tenant+user scoped).
    if (Object.keys(flags).length) throw new Error('Invalid command option.');
    const action = rest[0];
    if (!['view', 'edit', 'forget', 'off', 'control'].includes(action)) {
      throw new Error('memory requires view, edit, forget, off, or control.');
    }
    if (action === 'view') {
      if (rest.length > 2) throw new Error('Invalid command option.');
      const task_key = rest[1] || 'memory_control';
      const result = await tank.json(`/v1/command/opli/memory.view?task_key=${encodeURIComponent(task_key)}&memory_control=view`);
      io.write(`${safe(JSON.stringify(result, null, 2))}\n`);
      return result;
    }
    if (action === 'off' || (action === 'control' && rest[1])) {
      const control = action === 'off' ? 'off' : rest[1];
      if (!['off', 'view', 'edit', 'forget'].includes(control) || rest.length !== (action === 'off' ? 1 : 2)) {
        throw new Error('memory control accepts off, view, edit, or forget.');
      }
      const result = await tank.json('/v1/command/opli/memory.control', { control });
      io.write(`Memory control set to ${safe(control)}.\n`);
      return result;
    }
    if (action === 'forget') {
      if (rest.length !== 2) throw new Error('memory forget requires a memory id.');
      const result = await tank.json('/v1/command/opli/memory.forget', { memory_id: rest[1], memory_control: 'forget' });
      io.write(`Forgot memory ${safe(rest[1])}.\n`);
      return result;
    }
    if (action === 'edit') {
      if (rest.length < 3) throw new Error('memory edit requires a memory id and text.');
      const memory_id = rest[1];
      const body = rest.slice(2).join(' ');
      const result = await tank.json('/v1/command/opli/memory.edit', { memory_id, text: body, memory_control: 'edit' });
      io.write(`Edited memory ${safe(memory_id)}.\n`);
      return result;
    }
    throw new Error('memory requires view, edit, forget, off, or control.');
  }

  if (name === 'code') {
    if (rest.length > 1 || Object.keys(flags).some(flag => !['model', 'resume','plain'].includes(flag))) throw new Error('Invalid command option.');
    return codeSession(tank, rest[0] ?? '.', flags.model ?? 'auto', io, receiptLines, safe, { resume: flags.resume });
  }
  if (name === 'models') {
    // Founder-only and unadvertised: Tank answers 404 to everyone else (and the
    // app.tervory.com corridor does not forward it). Never print Tank's raw JSON.
    let roster;
    try { roster = await tank.json('/v1/command/models'); } catch (error) {
      if (error.status === 404 || error.status === 403) throw new Error('Unknown command. Run: tervory help');
      throw error;
    }
    if (!Array.isArray(roster)) throw new Error('Tank sent an invalid answer.');
    if (!roster.length) io.write(emptyState('models'));
    const price = value => (typeof value === 'number' && Number.isFinite(value) ? `$${value}` : 'price not set');
    for (const row of roster) {
      if (row && typeof row.name === 'string') io.write(`${safe(row.name)} | in ${price(row.input_usd_per_m)}/M | out ${price(row.output_usd_per_m)}/M\n`);
    }
    return;
  }
  if (name === 'threads') {
    const threads = await tank.json('/v1/command/threads');
    if (!Array.isArray(threads)) throw new Error('Tank sent an invalid answer.');
    if (!threads.length) io.write(emptyState('threads'));
    for (const thread of threads) io.write(`${safe(thread.thread_id)} ${safe(thread.title)} | spent ${amount(thread.spent_usd)} | saved ${amount(thread.saved_usd)}\n`);
    return;
  }
  if (name === 'receipts') {
    if (!rest[0]) throw new Error('A thread or task identifier is required.');
    let result;
    try {
      result = await tank.json(`/v1/command/threads/${encodeURIComponent(rest[0])}`);
    } catch (error) {
      if (error.status !== 404) throw error;
      result = await tank.json(`/v1/command/tasks/${encodeURIComponent(rest[0])}/receipts`);
    }
    io.write(receiptLines({ rows: result.rows ?? result.receipts ?? [] }, { empty: true }).map(safe).join('\n') + '\n'); return;
  }
  if (!['ask', 'chat', 'run', 'build'].includes(name)) throw new Error('Unknown command.');
  if (name !== 'chat' && !rest.length) throw new Error('Command text is required.');
  const thread = flags.thread ?? (name === 'chat' ? rest[0] : undefined)
    ?? (await tank.json('/v1/command/threads', {})).thread_id;
  io.write(`Thread ${safe(thread)}\n`);
  if (name === 'build') {
    const brief = redactSecrets(rest.join(' '));
    io.write(safe(brief) + '\n');
    const card = await tank.json('/v1/command/build', { thread_id: thread, brief_markdown: brief });
    const result = await confirmCard(tank, card, io);
    if (result.status !== 'cancelled') await followThread(tank, thread, result, io);
    return;
  }
  if (name === 'run') {
    const { tool, arguments: arguments_ } = runRequest;
    const allowed = await tank.json('/v1/command/tools');
    if (!allowed.includes(tool)) throw new Error('This tool is not available to this user.');
    const card = await tank.json(`/v1/command/threads/${encodeURIComponent(thread)}/actions`, { tool, arguments: arguments_ });
    const result = await confirmCard(tank, card, io);
    if (result.status !== 'cancelled') await followThread(tank, thread, result, io);
    return;
  }
  if (name === 'ask') { await showTurn(tank, thread, rest.join(' '), flags.model ?? 'auto', attachments, io); return; }
  if (rest[0] || flags.thread) {
    const snapshot = await tank.json(`/v1/command/threads/${encodeURIComponent(thread)}`);
    if (!snapshot.messages?.length && !snapshot.steps?.length && !snapshot.cards?.length) io.write(emptyState('messages'));
    for (const message of snapshot.messages ?? []) io.write(`${safe(message.role)}: ${safe(message.content)}\n`);
    for (const step of snapshot.steps ?? []) io.write(`[${safe(step.status)}] ${safe(step.actor)}: ${safe(step.label)}\n`);
    for (const card of snapshot.cards ?? []) {
      if (['confirmed', 'executing'].includes(card.status)) await followThread(tank, thread, card, io);
    }
  } else io.write(emptyState('messages'));
  while (true) {
    const text = await io.prompt('> ');
    if (text === null || text.trim() === '/exit') break;
    if (!text.trim()) continue;
    await showTurn(tank, thread, text, flags.model ?? 'auto', attachments, io);
  }
}
