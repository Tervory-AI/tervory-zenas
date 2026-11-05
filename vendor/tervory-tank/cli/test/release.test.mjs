// Self-hosted distribution: build-release.mjs -> install.sh -> tervory update.
// Everything is served from a loopback HTTP server standing in for
// https://app.tervory.com/cli; no registry publish, no network beyond build-time
// `npm ci` of the pinned lockfile. Host platform only (the CI runner's).
import test from 'node:test';
import assert from 'node:assert/strict';
import { execFile, execFileSync } from 'node:child_process';
import { createServer } from 'node:http';
import { createServer as createHttpsServer } from 'node:https';
import { existsSync, mkdtempSync, readdirSync, readFileSync, readlinkSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { promisify } from 'node:util';
import { buildRelease, compareVersions } from '../scripts/build-release.mjs';

const CLI = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const run = promisify(execFile);
const ARCH = { x64: 'x64', arm64: 'arm64' }[process.arch];
const musl = process.platform === 'linux' && !process.report.getReport().header.glibcVersionRuntime;
const PLATFORM = process.platform === 'darwin' ? `darwin-${ARCH}` : `linux-${ARCH}-${musl ? 'musl' : 'gnu'}`;
const supported = (process.platform === 'linux' || process.platform === 'darwin') && ARCH;

const work = mkdtempSync(join(tmpdir(), 'tervory-release-test-'));
const out = join(work, 'site');
let server; let base; const tamper = { on: false }; const stale = { on: false };

test.before(async () => {
  if (!supported) return;
  buildRelease({ out, platforms: [PLATFORM], version: '0.1.0' });
  buildRelease({ out, platforms: [PLATFORM], version: '0.1.1' });
  server = createServer((req, res) => {
    const path = new URL(req.url, 'http://x').pathname.replace(/^\/cli\//, '');
    if (stale.on && path === 'latest.json') { res.writeHead(200).end('{"version":"0.1.0","manifest":"releases/0.1.0/manifest.json"}'); return; }
    const file = resolve(out, path);
    if (!file.startsWith(out + '/') || !existsSync(file)) { res.writeHead(404).end('not found'); return; }
    let bytes = readFileSync(file);
    if (tamper.on && file.endsWith('.tar.gz')) { bytes = Buffer.from(bytes); bytes[bytes.length - 20] ^= 0xff; }
    res.writeHead(200).end(bytes);
  });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${server.address().port}/cli`;
});
test.after(() => { server?.close(); rmSync(work, { recursive: true, force: true }); });

// Map the fixed https://app.tervory.com/cli base onto the loopback server.
const transport = (url, init) => fetch(String(url).replace('https://app.tervory.com/cli', base), init);

function install(env, home) {
  return run('sh', [join(CLI, 'install.sh')], {
    env: { PATH: process.env.PATH, TMPDIR: work, HOME: home, TERVORY_CLI_BASE: base, TERVORY_BIN_DIR: join(home, 'bin'), ...env },
  }).then(r => ({ code: 0, ...r }), e => ({ code: e.code, stdout: e.stdout, stderr: e.stderr }));
}

test('release is reproducible and the manifest matches the bytes', { skip: !supported && 'host platform has no build' }, () => {
  const manifest = JSON.parse(readFileSync(join(out, 'releases/0.1.1/manifest.json'), 'utf8'));
  const artifact = manifest.artifacts[PLATFORM];
  assert.equal(artifact.file, `tervory-cli-0.1.1-${PLATFORM}.tar.gz`);
  const sum = execFileSync('sha256sum', [join(out, 'releases/0.1.1', artifact.file)]).toString().split(' ')[0];
  assert.equal(sum, artifact.sha256);
  assert.match(readFileSync(join(out, 'releases/0.1.1/SHA256SUMS'), 'utf8'), new RegExp(`^${sum}  ${artifact.file}\\n$`));
  assert.deepEqual(JSON.parse(readFileSync(join(out, 'latest.json'), 'utf8')), { version: '0.1.1', manifest: 'releases/0.1.1/manifest.json' });
  assert.equal(readFileSync(join(out, 'install.sh'), 'utf8'), readFileSync(join(CLI, 'install.sh'), 'utf8'));
  const again = join(work, 'again');
  buildRelease({ out: again, platforms: [PLATFORM], version: '0.1.1' });
  assert.deepEqual(readFileSync(join(again, 'releases/0.1.1', artifact.file)), readFileSync(join(out, 'releases/0.1.1', artifact.file)));
  assert(compareVersions('0.10.0', '0.9.9') > 0);
});

test('a default build (as the Workspace image runs it) offers cli/package.json\'s version', { skip: !supported && 'host platform has no build' }, () => {
  const { version } = JSON.parse(readFileSync(join(CLI, 'package.json'), 'utf8'));
  const dir = join(work, 'default');
  buildRelease({ out: dir, platforms: [PLATFORM] });
  assert.deepEqual(JSON.parse(readFileSync(join(dir, 'latest.json'), 'utf8')), { version, manifest: `releases/${version}/manifest.json` });
  assert.equal(JSON.parse(readFileSync(join(dir, `releases/${version}/manifest.json`), 'utf8')).version, version);
  const lock = JSON.parse(readFileSync(join(CLI, 'package-lock.json'), 'utf8'));
  assert.equal(lock.version, version); assert.equal(lock.packages[''].version, version);
});

test('install.sh installs latest, honours TERVORY_CLI_VERSION, and refuses a tampered tarball', { skip: !supported && 'host platform has no build' }, async () => {
  const home = mkdtempSync(join(work, 'home-'));
  const latest = await install({}, home);
  assert.equal(latest.code, 0, latest.stderr);
  assert.match(latest.stdout, /SHA-256 verified: [0-9a-f]{64}/);
  assert.equal(readlinkSync(join(home, '.tervory/cli/current')), '0.1.1');
  // This installer-debris assertion excludes the independent passive-update cache.
  // Keep the version probe offline and deterministic using the CLI's supported switch.
  const version = await run(join(home, 'bin/tervory'), ['version'], {
    env: { PATH: process.env.PATH, HOME: home, TERVORY_NO_UPDATE_CHECK: '1' },
  });
  assert.equal(version.stdout, `tervory 0.1.1 (${PLATFORM})\n`);

  // Re-installing the same version swaps the directory (.new in, .old out) and leaves no debris.
  assert.equal((await install({}, home)).code, 0);
  assert.deepEqual(readdirSync(join(home, '.tervory/cli')).sort(), ['0.1.1', 'current']);

  const pinned = await install({ TERVORY_CLI_VERSION: '0.1.0' }, home);
  assert.equal(pinned.code, 0, pinned.stderr);
  assert.equal(readlinkSync(join(home, '.tervory/cli/current')), '0.1.0');

  // No silent downgrade: with 0.1.1 current, a latest.json offering 0.1.0 is refused.
  assert.equal((await install({ TERVORY_CLI_VERSION: '0.1.1' }, home)).code, 0);
  stale.on = true;
  try {
    const refused = await install({}, home);
    assert.notEqual(refused.code, 0);
    assert.match(refused.stderr, /latest\.json offers 0\.1\.0, older than the installed 0\.1\.1\. Set TERVORY_CLI_VERSION=0\.1\.0 to downgrade\. Nothing was changed\./);
    assert.equal(readlinkSync(join(home, '.tervory/cli/current')), '0.1.1');
  } finally { stale.on = false; }
  assert.equal((await install({ TERVORY_CLI_VERSION: '0.1.0' }, home)).code, 0);

  assert.notEqual((await install({ TERVORY_CLI_VERSION: '9.9.9' }, home)).code, 0);
  assert.notEqual((await install({ TERVORY_CLI_VERSION: '1.0; rm -rf /' }, home)).code, 0);
  assert.notEqual((await install({ TERVORY_CLI_BASE: 'http://example.com/cli' }, home)).code, 0);

  const fresh = mkdtempSync(join(work, 'home-'));
  tamper.on = true;
  try {
    const refused = await install({}, fresh);
    assert.notEqual(refused.code, 0);
    assert.match(refused.stderr, /Checksum mismatch .* Nothing was installed\./);
    assert.equal(existsSync(join(fresh, '.tervory/cli')), false);
    assert.equal(existsSync(join(fresh, 'bin/tervory')), false);
  } finally { tamper.on = false; }
});

test('tervory update: latest, TERVORY_CLI_VERSION pin, and checksum refusal', { skip: !supported && 'host platform has no build' }, async () => {
  const home = mkdtempSync(join(work, 'home-'));
  assert.equal((await install({ TERVORY_CLI_VERSION: '0.1.0' }, home)).code, 0);
  const cliDir = join(home, '.tervory/cli');
  // Drive the installed copy's own modules (their package root is the installed release).
  const installed = await import(pathToFileURL(join(cliDir, '0.1.0/src/client.mjs')));
  let output = ''; const io = { write: v => { output += v; }, prompt: async () => null };

  tamper.on = true;
  try {
    await assert.rejects(installed.run(['update'], { transport, io, env: {} }),
      /Update refused: checksum mismatch for tervory-cli-0\.1\.1-.*Nothing was changed\./);
  } finally { tamper.on = false; }
  assert.equal(readlinkSync(join(cliDir, 'current')), '0.1.0');
  assert.equal(existsSync(join(cliDir, '0.1.1')), false);

  // A pin to the installed version is a no-op, even though 0.1.1 is latest.
  await installed.run(['update'], { transport, io, env: { TERVORY_CLI_VERSION: '0.1.0' } });
  assert.match(output, /0\.1\.0 is already installed/);
  assert.equal(readlinkSync(join(cliDir, 'current')), '0.1.0');
  await assert.rejects(installed.run(['update'], { transport, io, env: { TERVORY_CLI_VERSION: 'latest' } }), /invalid version/);
  await assert.rejects(installed.run(['update'], { transport, io, env: { TERVORY_CLI_VERSION: '7.7.7' } }), /version 7\.7\.7 is not available/);

  await installed.run(['update'], { transport, io, env: {} });
  assert.match(output, /SHA-256 verified: [0-9a-f]{64}\nUpdated Tervory CLI 0\.1\.0 -> 0\.1\.1\./);
  assert.equal(readlinkSync(join(cliDir, 'current')), '0.1.1');

  // --version wins over the pin; downgrade back to 0.1.0 through the 0.1.1 copy.
  const newer = await import(pathToFileURL(join(cliDir, '0.1.1/src/client.mjs')));
  await newer.run(['update', '--version', '0.1.0'], { transport, io, env: { TERVORY_CLI_VERSION: '0.1.1' } });
  assert.equal(readlinkSync(join(cliDir, 'current')), '0.1.0');
  const launched = await run(join(home, 'bin/tervory'), ['version'], { env: { PATH: process.env.PATH, HOME: home, TERVORY_NO_UPDATE_CHECK: '1' } });
  assert.equal(launched.stdout, `tervory 0.1.0 (${PLATFORM})\n`);

  // Review of #506 should-fix 3: latest.json pointing below the installed copy (a rollback,
  // a stale cache or a tampered mirror) is refused unless --version / the pin asks for it.
  const stale = (url, init) => String(url).endsWith('/latest.json')
    ? Promise.resolve(Response.json({ version: '0.1.0', manifest: 'releases/0.1.0/manifest.json' }))
    : transport(url, init);
  const before = readlinkSync(join(cliDir, 'current'));
  await assert.rejects(newer.run(['update'], { transport: stale, io, env: {} }),
    /Update refused: latest\.json offers 0\.1\.0, older than the installed 0\.1\.1\. Use tervory update --version 0\.1\.0 to downgrade\./);
  assert.equal(readlinkSync(join(cliDir, 'current')), before);
  assert(existsSync(join(cliDir, '0.1.1/RELEASE.json')));

  // Development checkouts never self-update.
  const { run: devRun } = await import('../src/client.mjs');
  await assert.rejects(devRun(['update'], { transport, io, env: {} }), /only available for copies installed with: curl -fsSL https:\/\/app\.tervory\.com\/cli\/install\.sh \| sh/);
});

test('install.sh: deadlines on every download, wrapped in main, atomic swap', () => {
  const script = readFileSync(join(CLI, 'install.sh'), 'utf8').replaceAll('\r\n', '\n');
  assert.match(script, /PROTO='=https'; LOOPBACK=0/);
  assert.match(script, /http:\/\/127\.0\.0\.1:\*\|http:\/\/127\.0\.0\.1\/\*\|http:\/\/localhost:\*\|http:\/\/localhost\/\*\) PROTO='=https,http'; LOOPBACK=1/);
  assert.match(script, /curl -fsSL --proto "\$PROTO" --proto-redir "\$PROTO" --connect-timeout 15 --max-time 300/);
  // Every wget call follows no redirect and has a per-read and (where available) total deadline.
  const wgets = script.split('\n').filter(line => /\bwget -q\b/.test(line));
  assert.equal(wgets.length, 2);
  for (const line of wgets) assert.match(line, /\$DEADLINE wget -q (--https-only )?--max-redirect=0 -T 30 -t 2 -O "\$2" "\$1"/);
  assert.match(script, /DEADLINE="timeout 300"/);
  assert.match(script, /\nmain\(\) \{\n/);
  assert.equal(script.trimEnd().split('\n').at(-1), 'main "$@"');
  assert.match(script, /mv "\$CLI_DIR\/\$VERSION" "\$CLI_DIR\/\$VERSION\.old"/);
});

// Behavioural redirect tests (review of #506 round 2, D1): the installer runs against a
// loopback https server (throwaway self-signed certificate made here, trusted only through
// CURL_CA_BUNDLE / WGETRC) whose /r/cli/* answers 302 to the plain-http server above.
// The PATH holds only the tools install.sh needs, without curl (wget path) or without wget
// (curl path). Each downloader must refuse the downgrade: non-zero exit, nothing installed,
// and the http server never asked. A direct https install with the same PATH must succeed,
// so a refusal cannot come from TLS or a missing tool.
const has = cmd => { try { execFileSync('sh', ['-c', `command -v ${cmd}`], { stdio: 'ignore' }); return true; } catch { return false; } };
const TOOLS = ['sh', 'tar', 'gzip', 'sha256sum', 'shasum', 'cut', 'grep', 'mktemp', 'rm', 'mv', 'mkdir', 'uname', 'ldd', 'chmod',
  'timeout', 'cat', 'env', 'readlink', 'dirname', 'basename', 'head', 'tr', 'sed', 'ls', 'curl', 'wget'];
const redirectSkip = !supported ? 'host platform has no build'
  : !process.env.CI && !(has('openssl') && has('wget') && has('curl')) && 'needs openssl, wget and curl (always run in CI)';

function toolDir(without) {
  const dir = mkdtempSync(join(work, `bin-no-${without}-`));
  symlinkSync(process.execPath, join(dir, 'node'));
  for (const tool of TOOLS) {
    if (tool === without) continue;
    const found = (() => { try { return execFileSync('sh', ['-c', `command -v ${tool}`]).toString().trim(); } catch { return ''; } })();
    if (found.startsWith('/')) symlinkSync(found, join(dir, tool));
  }
  return dir;
}

test('install.sh refuses an https->http redirect over wget and over curl (behaviour, not text)', { skip: redirectSkip }, async () => {
  const tls = mkdtempSync(join(work, 'tls-'));
  execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2', '-subj', '/CN=127.0.0.1',
    '-addext', 'subjectAltName=IP:127.0.0.1', '-keyout', join(tls, 'key.pem'), '-out', join(tls, 'cert.pem')], { stdio: 'ignore' });
  const cert = join(tls, 'cert.pem');
  writeFileSync(join(tls, 'wgetrc'), `ca_certificate = ${cert}\n`);
  const httpPort = new URL(base).port;
  let plainHits = 0;
  const countPlain = req => { if (!req.headers['x-https-front']) plainHits++; };
  server.on('request', countPlain);
  const front = createHttpsServer({ key: readFileSync(join(tls, 'key.pem')), cert: readFileSync(cert) }, (req, res) => {
    const url = new URL(req.url, 'https://x');
    if (url.pathname.startsWith('/r/cli/')) { res.writeHead(302, { location: `http://127.0.0.1:${httpPort}${url.pathname.slice(2)}` }).end(); return; }
    // Direct: the same files, fetched from the plain server behind this one.
    fetch(`${base}${url.pathname.replace(/^\/cli/, '')}`, { headers: { 'x-https-front': '1' } })
      .then(async upstream => res.writeHead(upstream.status).end(Buffer.from(await upstream.arrayBuffer())))
      .catch(() => res.writeHead(502).end());
  });
  await new Promise(r => front.listen(0, '127.0.0.1', r));
  const httpsBase = `https://127.0.0.1:${front.address().port}`;
  const attempt = (without, path) => {
    const home = mkdtempSync(join(work, `home-${without}-`));
    const before = plainHits;
    return run('sh', [join(CLI, 'install.sh')], { env: {
      PATH: toolDir(without), TMPDIR: work, HOME: home, TERVORY_CLI_BASE: `${httpsBase}${path}`, TERVORY_BIN_DIR: join(home, 'bin'),
      CURL_CA_BUNDLE: cert, WGETRC: join(tls, 'wgetrc'),
    } }).then(r => ({ code: 0, ...r }), e => ({ code: e.code, stdout: e.stdout, stderr: e.stderr }))
      .then(result => ({ ...result, home, plain: plainHits - before }));
  };
  try {
    for (const [downloader, without] of [['wget', 'curl'], ['curl', 'wget']]) {
      const direct = await attempt(without, '/cli');
      assert.equal(direct.code, 0, `${downloader} direct https install: ${direct.stderr}`);
      assert.equal(readlinkSync(join(direct.home, '.tervory/cli/current')), '0.1.1');

      const redirected = await attempt(without, '/r/cli');
      assert.notEqual(redirected.code, 0, `${downloader} followed an https->http redirect: ${redirected.stdout}`);
      assert.match(redirected.stderr, /tervory install: Could not read https:\/\/127\.0\.0\.1:\d+\/r\/cli\/latest\.json\./);
      assert.equal(existsSync(join(redirected.home, '.tervory')), false, `${downloader}: nothing installed`);
      assert.equal(existsSync(join(redirected.home, 'bin/tervory')), false, `${downloader}: no launcher`);
      assert.equal(redirected.plain, 0, `${downloader}: the plain-http server was never asked`);
    }
  } finally {
    server.off('request', countPlain);
    await new Promise(r => front.close(r));
  }
});

test('update notice: at most daily, silent on failure', { skip: !supported && 'host platform has no build' }, async () => {
  const home = mkdtempSync(join(work, 'home-'));
  assert.equal((await install({ TERVORY_CLI_VERSION: '0.1.0' }, home)).code, 0);
  const root = join(home, '.tervory/cli/0.1.0');
  const { updateNotice } = await import(pathToFileURL(join(root, 'src/update.mjs')));
  let calls = 0; const counted = (url, init) => { calls++; return transport(url, init); };
  assert.match(await updateNotice({ transport: counted, now: () => 1_000_000 }), /0\.1\.0 -> 0\.1\.1\. Run: tervory update/);
  assert.match(await updateNotice({ transport: counted, now: () => 1_000_000 + 60_000 }), /0\.1\.1/);
  assert.equal(calls, 1);
  assert.equal(await updateNotice({ transport: async () => { throw new Error('offline'); }, now: () => 10 ** 12 }), null);
});
