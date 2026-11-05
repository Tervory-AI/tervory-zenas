// Self-hosted updates: https://app.tervory.com/cli/ only (no npm registry, no URL
// override). A downloaded tarball is used only if its SHA-256 matches the release
// manifest. Applies to copies installed by install.sh (they carry RELEASE.json).
import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, rename, rm, symlink, writeFile } from 'node:fs/promises';
import { basename, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { boundedFetch, DeadlineError, RedirectError } from './http.mjs';

export const RELEASE_BASE = 'https://app.tervory.com/cli';
export const INSTALL_HINT = 'curl -fsSL https://app.tervory.com/cli/install.sh | sh';
const VERSION = /^\d{1,4}\.\d{1,4}\.\d{1,4}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const MAX_TARBALL = 64 * 1024 * 1024;
const CHECK_EVERY_MS = 24 * 60 * 60 * 1000;
const PACKAGE_ROOT = fileURLToPath(new URL('..', import.meta.url));

export function compareVersions(a, b) {
  const x = a.split('.').map(Number); const y = b.split('.').map(Number);
  for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] - y[i];
  return 0;
}

/** This copy's release record, or null for a development checkout. */
export async function installedRelease(root = PACKAGE_ROOT) {
  try {
    const release = JSON.parse(await readFile(join(root, 'RELEASE.json'), 'utf8'));
    if (!VERSION.test(release.version) || typeof release.platform !== 'string') return null;
    if (basename(root.replace(/[\\/]+$/, '')) !== release.version) return null;
    return { ...release, root: root.replace(/[\\/]+$/, ''), cliDir: dirname(root.replace(/[\\/]+$/, '')) };
  } catch { return null; }
}

export async function packageVersion(root = PACKAGE_ROOT) {
  return JSON.parse(await readFile(join(root, 'package.json'), 'utf8')).version;
}

// Hard deadlines over each whole call, body included (http.mjs, review of #506 B1).
export const JSON_TIMEOUT_MS = 15_000;
export const DOWNLOAD_TIMEOUT_MS = 180_000;

async function get(transport, path, signal, timeoutMs, read) {
  try {
    return await boundedFetch(transport, `${RELEASE_BASE}/${path}`, { method: 'GET', cache: 'no-store', signal }, timeoutMs, async response => {
      if (!response.ok) {
        await response.body?.cancel?.().catch(() => {});
        const e = new Error(`Update failed: ${path} returned ${response.status}.`); e.status = response.status; e.http = true; throw e;
      }
      return read(response);
    });
  } catch (error) {
    if (error.http) throw error;
    if (error instanceof DeadlineError) throw new Error(`Update failed: ${path} did not arrive in time. Nothing was changed.`);
    if (error instanceof RedirectError) throw new Error(`Update failed: ${path} redirected (${error.status}); redirects are refused.`);
    throw new Error(`Update failed: could not reach ${RELEASE_BASE}.`);
  }
}

async function getJson(transport, path, signal, timeoutMs = JSON_TIMEOUT_MS) {
  return get(transport, path, signal, timeoutMs, response => response.json());
}

export async function latestVersion(transport, signal, timeoutMs = JSON_TIMEOUT_MS) {
  const latest = await getJson(transport, 'latest.json', signal, timeoutMs);
  if (!VERSION.test(latest?.version ?? '')) throw new Error('Update failed: invalid latest.json.');
  return latest.version;
}

const run = promisify(execFile);

/**
 * tervory update [--version X.Y.Z]: download, verify SHA-256, unpack beside the
 * current version and switch the `current` link. Nothing changes on any failure.
 */
export async function update({ transport, io, version, root = PACKAGE_ROOT, tar = 'tar' }) {
  if (process.platform === 'win32') throw new Error('Update refused: on Windows, verify and extract the signed release archive again.');
  const release = await installedRelease(root);
  if (!release) throw new Error(`Update is only available for copies installed with: ${INSTALL_HINT}`);
  if (version !== undefined && !VERSION.test(version)) throw new Error('Update failed: invalid version.');
  const target = version ?? await latestVersion(transport, io.signal);
  if (target === release.version) { io.write(`Tervory CLI ${release.version} is already installed.\n`); return; }
  // latest.json pointing below this copy is refused (a rollback or a stale cache must not
  // silently downgrade); an explicit --version or TERVORY_CLI_VERSION may go down.
  if (version === undefined && compareVersions(target, release.version) < 0) {
    throw new Error(`Update refused: latest.json offers ${target}, older than the installed ${release.version}. Use tervory update --version ${target} to downgrade.`);
  }
  let manifest;
  try { manifest = await getJson(transport, `releases/${target}/manifest.json`, io.signal); } catch (error) {
    if (error.status === 404) throw new Error(`Update failed: version ${target} is not available.`);
    throw error;
  }
  const artifact = manifest?.artifacts?.[release.platform];
  if (manifest?.version !== target || !artifact || artifact.file !== `tervory-cli-${target}-${release.platform}.tar.gz`
      || !SHA256.test(artifact.sha256 ?? '')) {
    throw new Error(`Update failed: version ${target} has no valid build for ${release.platform}.`);
  }
  io.write(`Downloading Tervory CLI ${target} (${release.platform})\n`);
  const bytes = await get(transport, `releases/${target}/${artifact.file}`, io.signal, DOWNLOAD_TIMEOUT_MS, async response => {
    const declared = Number(response.headers?.get?.('content-length'));
    if (declared > MAX_TARBALL) throw new Error('Update failed: download too large.');
    if (!response.body?.getReader) return Buffer.from(await response.arrayBuffer());
    // Stream with a running cap so a lying or missing Content-Length cannot fill memory.
    const reader = response.body.getReader(); const chunks = []; let size = 0;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_TARBALL) { await reader.cancel().catch(() => {}); throw new Error('Update failed: download too large.'); }
      chunks.push(value);
    }
    return Buffer.concat(chunks);
  });
  if (bytes.length > MAX_TARBALL || bytes.length !== artifact.size && Number.isInteger(artifact.size)) {
    throw new Error(bytes.length > MAX_TARBALL ? 'Update failed: download too large.' : 'Update refused: size does not match the manifest. Nothing was changed.');
  }
  const got = createHash('sha256').update(bytes).digest('hex');
  if (got !== artifact.sha256) {
    throw new Error(`Update refused: checksum mismatch for ${artifact.file} (expected ${artifact.sha256}, got ${got}). Nothing was changed.`);
  }
  io.write(`SHA-256 verified: ${got}\n`);
  const staging = await mkdtemp(join(release.cliDir, `.update-${target}-`));
  try {
    const file = join(staging, artifact.file);
    await writeFile(file, bytes);
    await run(tar, ['-xzf', file, '-C', staging]);
    const unpacked = join(staging, `tervory-cli-${target}`);
    const record = JSON.parse(await readFile(join(unpacked, 'RELEASE.json'), 'utf8'));
    if (record.version !== target || record.platform !== release.platform) throw new Error('Update failed: archive does not match the manifest.');
    const destination = join(release.cliDir, target);
    await rm(destination, { recursive: true, force: true });
    await rename(unpacked, destination);
    // Atomic switch: a new link renamed over `current`.
    const link = join(release.cliDir, `.current-${process.pid}`);
    await rm(link, { force: true });
    await symlink(target, link);
    await rename(link, join(release.cliDir, 'current'));
  } finally {
    await rm(staging, { recursive: true, force: true });
  }
  io.write(`Updated Tervory CLI ${release.version} -> ${target}.\n`);
}

/**
 * Passive check, at most once a day, for installed copies. Returns a one-line
 * notice or null; never throws (offline, slow or broken answers are ignored).
 */
export async function updateNotice({ transport, now = Date.now, root = PACKAGE_ROOT, timeoutMs = 1500 }) {
  // Windows uses verified archive installation; never suggest its refused updater.
  if (process.platform === 'win32') return null;
  try {
    const release = await installedRelease(root);
    if (!release) return null;
    const stateFile = join(release.cliDir, 'update-check.json');
    let state = {};
    try { state = JSON.parse(await readFile(stateFile, 'utf8')); } catch { /* first check */ }
    let latest = VERSION.test(state.latest ?? '') ? state.latest : null;
    if (!(Number(state.checked_at) > now() - CHECK_EVERY_MS)) {
      latest = await latestVersion(transport, undefined, timeoutMs);
      await mkdir(release.cliDir, { recursive: true });
      await writeFile(stateFile, JSON.stringify({ checked_at: now(), latest }) + '\n');
    }
    if (latest && compareVersions(latest, release.version) > 0) {
      return `A new Tervory CLI is available: ${release.version} -> ${latest}. Run: tervory update\n`;
    }
  } catch { /* never block or fail a command on the passive check */ }
  return null;
}

