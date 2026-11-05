// The CLI version guard (.github/scripts/cli_version_guard.sh, run by cli.yml on every
// pull request; review of #506 round 2, D2), both ways, on throwaway git repositories:
// a shipped-file change without a higher version, lockfile match and CHANGELOG entry
// fails; a proper bump passes; a base without cli/ and a docs/test-only change pass.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync, execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const GUARD = resolve(dirname(fileURLToPath(import.meta.url)), '../../.github/scripts/cli_version_guard.sh');
const roots = [];
test.after(() => { for (const r of roots) rmSync(r, { recursive: true, force: true }); });

const git = (cwd, ...args) => execFileSync('git', ['-c', 'user.name=t', '-c', 'user.email=t@t', ...args], { cwd, stdio: 'pipe' }).toString().trim();
function write(root, files) {
  for (const [name, text] of Object.entries(files)) {
    if (text === null) { rmSync(join(root, name), { force: true }); continue; }
    mkdirSync(dirname(join(root, name)), { recursive: true }); writeFileSync(join(root, name), text);
  }
}
const cli = (version, { lock = version, changelog = [version], src = 'export const x = 1;\n' } = {}) => ({
  'cli/package.json': JSON.stringify({ name: '@tervory/cli', version }),
  'cli/package-lock.json': JSON.stringify({ name: '@tervory/cli', version: lock, lockfileVersion: 3, packages: { '': { name: '@tervory/cli', version: lock } } }),
  'cli/CHANGELOG.md': `# Changelog\n\n${changelog.map(v => `## ${v} — 2026-10-31\n\n- x\n`).join('\n')}`,
  'cli/src/client.mjs': src,
});
/** A repository whose base commit holds `base` files, then a head commit with `head` applied; runs the guard. */
function guard(base, head) {
  const root = mkdtempSync(join(tmpdir(), 'cli-guard-')); roots.push(root);
  git(root, 'init', '-q', '-b', 'main');
  write(root, { 'README.md': 'repo\n', ...base }); git(root, 'add', '.'); git(root, 'commit', '-qm', 'base');
  const baseSha = git(root, 'rev-parse', 'HEAD');
  write(root, head); git(root, 'add', '-A'); git(root, 'commit', '-qm', 'head', '--allow-empty');
  const r = spawnSync('sh', [GUARD, baseSha], { cwd: root, encoding: 'utf8' });
  return { code: r.status, out: r.stdout + r.stderr };
}

test('fails: shipped CLI code changed, version not bumped', () => {
  const r = guard(cli('0.1.0'), { 'cli/src/client.mjs': 'export const x = 2;\n' });
  assert.equal(r.code, 1, r.out);
  assert.match(r.out, /CLI changed; bump cli\/package\.json version and add a CHANGELOG entry \(version 0\.1\.0 is not above the base branch's 0\.1\.0\)/);
  assert.match(r.out, /cli\/src\/client\.mjs/);
});

test('fails for each shipped path: bin, install.sh, scripts, package-lock', () => {
  for (const [file, text] of [['cli/bin/tervory.mjs', 'x\n'], ['cli/install.sh', '#!/bin/sh\n'], ['cli/scripts/build-release.mjs', 'x\n']]) {
    const r = guard(cli('0.1.0'), { [file]: text });
    assert.equal(r.code, 1, `${file}: ${r.out}`);
  }
  const lockOnly = cli('0.1.0'); const changedLock = JSON.parse(lockOnly['cli/package-lock.json']); changedLock.packages['node_modules/x'] = { version: '1.0.0' };
  assert.equal(guard(lockOnly, { 'cli/package-lock.json': JSON.stringify(changedLock) }).code, 1);
});

test('fails: version lowered, lockfile not bumped, or no CHANGELOG entry', () => {
  const lower = guard(cli('0.1.0'), cli('0.0.9', { changelog: ['0.0.9', '0.1.0'], src: 'export const x = 2;\n' }));
  assert.equal(lower.code, 1, lower.out); assert.match(lower.out, /not above the base branch's 0\.1\.0/);
  const lock = guard(cli('0.1.0'), cli('0.1.1', { lock: '0.1.0', changelog: ['0.1.1', '0.1.0'], src: 'export const x = 2;\n' }));
  assert.equal(lock.code, 1, lock.out); assert.match(lock.out, /package-lock\.json version .* must equal cli\/package\.json \(0\.1\.1\)/);
  const log = guard(cli('0.1.0'), cli('0.1.1', { changelog: ['0.1.0'], src: 'export const x = 2;\n' }));
  assert.equal(log.code, 1, log.out); assert.match(log.out, /cli\/CHANGELOG\.md has no '## 0\.1\.1' entry/);
});

test('passes: shipped code changed with a higher version, matching lockfile and CHANGELOG entry', () => {
  const r = guard(cli('0.1.0'), cli('0.1.1', { changelog: ['0.1.1', '0.1.0'], src: 'export const x = 2;\n' }));
  assert.equal(r.code, 0, r.out); assert.match(r.out, /0\.1\.0 -> 0\.1\.1/);
});

test('passes: only docs or tests changed, no bump needed', () => {
  const r = guard(cli('0.1.0'), { 'cli/README.md': 'docs\n', 'cli/test/a.test.mjs': 'x\n' });
  assert.equal(r.code, 0, r.out); assert.match(r.out, /No shipped CLI file changed/);
});

test('passes: the base branch has no cli/ yet (first version)', () => {
  const r = guard({}, cli('0.1.1', { changelog: ['0.1.1', '0.1.0'] }));
  assert.equal(r.code, 0, r.out); assert.match(r.out, /Base has no cli\/package\.json/);
});

test('this checkout: package.json, package-lock.json and CHANGELOG agree on the version', () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  const r = spawnSync('sh', [GUARD, '4b825dc642cb6eb9a060e54bf8d69288fbee4904'], { cwd: root, encoding: 'utf8' }); // empty tree: no cli/ at base
  assert.equal(r.status, 0, r.stdout + r.stderr);
  const { version } = JSON.parse(readFileSync(join(root, 'cli/package.json'), 'utf8'));
  assert(r.stdout.includes(`${version} is the first version`), r.stdout);
});
