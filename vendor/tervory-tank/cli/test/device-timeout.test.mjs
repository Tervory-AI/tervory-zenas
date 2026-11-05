// Review of #496, item 1: deviceCall had no timeout, so a Tank that accepts the
// connection and never answers stalled `tervory login` past its 10-minute deadline.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { DEVICE_TIMEOUT_MS, deviceCall } from '../src/client.mjs';

test('deviceCall is bounded when Tank accepts and never answers, or stalls the body', async () => {
  assert.equal(DEVICE_TIMEOUT_MS, 10_000);
  const held = [];
  const server = createServer((req, res) => {
    held.push(res);
    if (req.url.endsWith('/token')) { res.writeHead(200, { 'content-type': 'application/json' }); res.write('{"access_token":'); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const local = (url, init) => fetch(`http://127.0.0.1:${server.address().port}${new URL(url).pathname}`, init);
  try {
    for (const action of ['code', 'token']) {
      const began = Date.now();
      await assert.rejects(deviceCall('us-east', action, {}, local, {}, 300), /^Error: Tank connection failed\.$/);
      const took = Date.now() - began;
      assert(took >= 250 && took < 3000, `${action} ended after ${took} ms`);
    }
  } finally { held.forEach(res => res.destroy()); server.close(); }
});

test('deviceCall passes an abort signal to the transport', async () => {
  let seen;
  await deviceCall('za', 'code', {}, async (url, init) => { seen = init.signal; return Response.json({ ok: true }); });
  assert(seen instanceof AbortSignal);
});
