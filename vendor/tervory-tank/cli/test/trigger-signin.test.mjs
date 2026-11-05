import test from 'node:test';
import assert from 'node:assert/strict';
import { identityApprovalUrl, run, safe, StreamSanitizer } from '../src/client.mjs';

const issuers = { 'us-east': 'https://auth.tervory.com', za: 'https://auth.tervory.com' };

function startFor(region) {
  return { identity_provider: 'trigger-signin', device_code: 'tzd1.synthetic-offline', user_code: '1234-5678',
    verification_uri: issuers[region] + '/ui/v2/login/device',
    verification_uri_complete: issuers[region] + '/ui/v2/login/device?user_code=1234-5678', interval: 1, expires_in: 600 };
}

for (const region of ['us-east', 'za']) {
  test(`Trigger sign-in ${region} refuses another origin and legacy approvals`, () => {
    const start = startFor(region);
    assert.equal(identityApprovalUrl(start, region), start.verification_uri_complete);
    for (const field of ['verification_uri', 'verification_uri_complete']) {
      for (const url of ['https://evil.example/device', issuers[region].replace('https:', 'http:') + '/device',
        issuers[region] + '.evil.example/device', issuers[region].replace('https://', 'https://user@') + '/device',
        issuers[region] + ':444/device', issuers[region] + '/device#fragment', 'https://retired.example.invalid/device']) {
        assert.throws(() => identityApprovalUrl({ ...start, [field]: url }, region), /Login refused by Tank/);
      }
    }
    assert.throws(() => identityApprovalUrl({ user_code: 'BCDF-GHJK' }, region), /Login refused by Tank/);
  });

  test(`Trigger sign-in ${region} opens its native page and stores only Tank credentials`, async () => {
    const start = startFor(region);
    const opened = []; const calls = []; let saved;
    const grant = {
      ['access' + '_token']: `tv1.${region}.${'0'.repeat(32)}.${'x'.repeat(43)}`,
      refresh_token: `tvr1.${region}.${'0'.repeat(32)}.${'r'.repeat(43)}`,
      expires_at: new Date(Date.now() + 3600_000).toISOString(),
      refresh_expires_at: new Date(Date.now() + 30 * 86400_000).toISOString(),
    };
    await run(['login', '--region', region], {
      store: { set: async value => { saved = value; } },
      io: { write() {}, sleep: async () => {}, open: value => opened.push(value) },
      transport: async (url, init) => {
        calls.push(url);
        assert.equal(new URL(url).origin, region === 'za' ? 'https://za.tervory.com' : 'https://app.tervory.com');
        assert.equal(init.headers.Authorization, undefined);
        assert.equal(init.redirect, 'manual');
        if (url.endsWith('/code')) assert.equal(JSON.parse(init.body).approval, 'trigger-signin');
        return Response.json(url.endsWith('/code') ? start : grant);
      },
    });
    assert.deepEqual(opened, [start.verification_uri_complete]);
    assert.equal(calls.length, 2);
    assert.equal(JSON.parse(saved).access_token, grant.access_token);
    assert.equal(JSON.parse(saved).refresh_token, grant.refresh_token);
    assert(!saved.includes(start.device_code));
  });

  test(`Trigger sign-in ${region} fails closed when the server is unavailable`, async () => {
    let calls = 0;
    await assert.rejects(run(['login', '--region', region], {
      store: { set() { assert.fail('Must not store a refused login'); } },
      io: { write() {}, open() { assert.fail('Must not open a refused login'); } },
      transport: async () => { calls++; return Response.json({ error: 'temporarily_unavailable' }, { status: 503 }); },
    }), /Login refused/);
    assert.equal(calls, 1);
  });
}

test('wrapped device codes stay redacted across every chunk boundary', () => {
  const code = 'tzd1.synthetic-offline-device-code';
  assert.equal(safe(code), '[redacted]');
  for (let split = 0; split <= code.length; split++) {
    const sanitizer = new StreamSanitizer();
    assert.equal(sanitizer.write(code.slice(0, split)) + sanitizer.write(code.slice(split)) + sanitizer.end(), '[redacted]');
  }
});

test('a provider selector is refused before credentials or network access', async () => {
  for (const argv of [['login', '--sign-in', 'legacy'], ['threads', '--sign-in', 'legacy']]) {
    await assert.rejects(run(argv, {
      store: { get() { assert.fail('Must refuse before reading credentials'); } },
      io: { write() {} }, transport: () => assert.fail('Must refuse before network'),
    }), /Invalid command option/);
  }
});
