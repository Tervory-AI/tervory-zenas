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

/**
 * Run transport(url, init) then read(response) within timeoutMs, total.
 * init.signal (e.g. Ctrl-C) still aborts early. Rejects with DeadlineError on the deadline,
 * RedirectError on any redirect, or whatever the transport/read threw.
 */
export async function boundedFetch(transport, url, init, timeoutMs, read) {
  const controller = new AbortController();
  const signal = init?.signal ? AbortSignal.any([init.signal, controller.signal]) : controller.signal;
  let response; let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => {
      reject(new DeadlineError());
      controller.abort();
      // Best effort: free the socket even if undici ignores the abort.
      try { response?.body?.cancel?.().catch(() => {}); } catch { /* locked by the reader */ }
    }, timeoutMs);
  });
  const work = (async () => {
    response = await transport(url, { ...init, redirect: 'manual', signal });
    if (isRedirect(response)) {
      await response.body?.cancel?.().catch(() => {});
      throw new RedirectError(response.status);
    }
    return read(response);
  })();
  work.catch(() => {});
  try { return await Promise.race([work, deadline]); } finally { clearTimeout(timer); }
}
