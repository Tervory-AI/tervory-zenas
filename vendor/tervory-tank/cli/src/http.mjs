// Bounded HTTP for the CLI (review of #506, B1).
//
// Node 22 / undici 6.29.1: a fetch made with redirect:"error" whose body stalls after
// the headers never aborts once a GC has run, whatever the signal. So every call here:
//  - uses redirect:"manual" and refuses any 3xx / opaqueredirect itself (still no redirects), and
//  - races the whole call, body read included, against a hard deadline that aborts the
//    request and cancels the body when it fires, without relying on undici honouring the signal.

export class DeadlineError extends Error {
  constructor() { super('deadline'); this.name = 'DeadlineError'; }
}
export class RedirectError extends Error {
  constructor(status) { super(`redirect ${status}`); this.name = 'RedirectError'; this.status = status; }
}

export const isRedirect = response => response.type === 'opaqueredirect' || (response.status >= 300 && response.status < 400);

// Start only after checking cancellation. Race even transports which ignore signals.
// Operations are never retried here: a lost response can still mean work succeeded.
export async function boundedOperation(start, timeoutMs, signal, cancel = () => {}) {
  let timer; let onAbort;
  const stopped = new Promise((_, reject) => {
    const stop = error => { reject(error); try { cancel(); } catch {} };
    onAbort = () => stop(new DOMException('Cancelled', 'AbortError'));
    if (signal?.aborted) return onAbort();
    signal?.addEventListener('abort', onAbort, { once: true });
    timer = setTimeout(() => stop(new DeadlineError()), timeoutMs);
  });
  try {
    if (signal?.aborted) return await stopped;
    return await Promise.race([Promise.resolve().then(() => { signal?.throwIfAborted(); return start(); }), stopped]);
  } finally { clearTimeout(timer); signal?.removeEventListener('abort', onAbort); }
}

export async function responseBytes(response, limit) {
  const reader = response.body?.getReader();
  if (!reader) return Buffer.alloc(0);
  let size = 0; const chunks = [];
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) return Buffer.concat(chunks, size);
      size += value.byteLength;
      if (size > limit) throw new Error('Response exceeds the limit.');
      chunks.push(Buffer.from(value));
    }
  } finally { reader.cancel().catch(() => {}); reader.releaseLock(); }
}

// Preserve SSE delivery while bounding a remote tool's entire response lifetime.
export function boundedBody(body, limit, timeoutMs, signal) {
  if (!body) return null;
  const reader = body.getReader(); let size = 0; let timer; let stop; let ended = false;
  const cleanup = () => { clearTimeout(timer); signal?.removeEventListener('abort', stop); };
  return new ReadableStream({
    start(controller) {
      stop = () => {
        if (ended) return;
        ended = true; cleanup(); reader.cancel().catch(() => {});
        controller.error(signal?.aborted ? new DOMException('Cancelled', 'AbortError') : new DeadlineError());
      };
      timer = setTimeout(stop, timeoutMs);
      signal?.addEventListener('abort', stop, { once: true });
      if (signal?.aborted) stop();
    },
    async pull(controller) {
      try {
        const { value, done } = await reader.read();
        if (ended) return;
        if (done) { ended = true; cleanup(); controller.close(); return; }
        size += value.byteLength;
        if (size > limit) throw new Error('Response exceeds the limit.');
        controller.enqueue(value);
      } catch (error) {
        if (!ended) { ended = true; cleanup(); reader.cancel().catch(() => {}); controller.error(error); }
      }
    },
    cancel() { ended = true; cleanup(); reader.cancel().catch(() => {}); },
  });
}

/**
 * Run transport(url, init) then read(response) within timeoutMs, total.
 * init.signal (e.g. Ctrl-C) still aborts early. Rejects with DeadlineError on the deadline,
 * RedirectError on any redirect, or whatever the transport/read threw.
 */
export async function boundedFetch(transport, url, init, timeoutMs, read) {
  const controller = new AbortController();
  const signal = init?.signal ? AbortSignal.any([init.signal, controller.signal]) : controller.signal;
  let response;
  const cancel = () => {
    controller.abort();
    try { response?.body?.cancel?.().catch(() => {}); } catch { /* locked by the reader */ }
  };
  return boundedOperation(async () => {
    response = await transport(url, { ...init, redirect: init?.redirect === 'error' ? 'error' : 'manual', signal });
    if (signal.aborted) { cancel(); throw new DOMException('Cancelled', 'AbortError'); }
    if (isRedirect(response)) {
      cancel();
      throw new RedirectError(response.status);
    }
    return read(response);
  }, timeoutMs, init?.signal, cancel);
}
