#!/usr/bin/env node
// Build the self-hosted CLI release served at https://app.tervory.com/cli/.
// No registry publish: `npm pack` collects the package files, `npm ci` (build time
// only, from package-lock.json integrity hashes) adds each platform's keychain
// binding, and the result is one tarball per platform plus a SHA-256 manifest.
//
//   node cli/scripts/build-release.mjs --out <dir> [--platforms linux-x64-gnu,...] [--version X.Y.Z]
//
// Output (the Workspace image serves <dir> as /cli/):
//   install.sh
//   latest.json                              {"version", "manifest"}
//   releases/<v>/manifest.json               {"name","version","node","artifacts":{platform:{file,sha256,size}}}
//   releases/<v>/SHA256SUMS                  sha256sum format
//   releases/<v>/tervory-cli-<v>-<platform>.tar.gz
// Tarballs are reproducible (sorted, fixed mtime/owner, gzip -n). Linux build host (GNU tar).
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { delimiter, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const PLATFORMS = Object.freeze({
  'linux-x64-gnu': { os: 'linux', cpu: 'x64', libc: 'glibc' },
  'linux-x64-musl': { os: 'linux', cpu: 'x64', libc: 'musl' },
  'linux-arm64-gnu': { os: 'linux', cpu: 'arm64', libc: 'glibc' },
  'linux-arm64-musl': { os: 'linux', cpu: 'arm64', libc: 'musl' },
  'darwin-x64': { os: 'darwin', cpu: 'x64' },
  'darwin-arm64': { os: 'darwin', cpu: 'arm64' },
  'win32-x64-msvc': { os: 'win32', cpu: 'x64' },
  'win32-arm64-msvc': { os: 'win32', cpu: 'arm64' },
});
export const VERSION = /^\d{1,4}\.\d{1,4}\.\d{1,4}$/;
const CLI = resolve(dirname(fileURLToPath(import.meta.url)), '..');

const sha256 = file => createHash('sha256').update(readFileSync(file)).digest('hex');
// Git for Windows' GNU tar/gzip are MSYS programs, not native Win32 tools.
const archivePath = value => process.platform === 'win32'
  ? value.replaceAll('\\', '/').replace(/^([A-Za-z]):\//, (_, drive) => `/${drive.toLowerCase()}/`) : value;
const run = (command, args, cwd) => {
  // Windows .cmd launchers need a shell; invoke npm's JS entry with Node instead.
  if (command === 'npm' && process.platform === 'win32') {
    const npm = process.env.npm_execpath ?? (process.env.PATH ?? '').split(delimiter)
      .map(dir => join(dir, 'node_modules/npm/bin/npm-cli.js')).find(existsSync);
    if (!npm) throw new Error('npm CLI entry was not found');
    args = [npm, ...args]; command = process.execPath;
  }
  if (command === 'tar') args = args.map(archivePath);
  return execFileSync(command, args, { cwd, stdio: ['ignore', 'pipe', 'inherit'] }).toString();
};

export function buildRelease({ out, platforms = Object.keys(PLATFORMS), version } = {}) {
  const pkg = JSON.parse(readFileSync(join(CLI, 'package.json'), 'utf8'));
  version ??= pkg.version;
  if (!VERSION.test(version)) throw new Error(`Invalid version ${version}`);
  for (const name of platforms) if (!PLATFORMS[name]) throw new Error(`Unknown platform ${name}`);
  out = resolve(out);
  const releaseDir = join(out, 'releases', version);
  rmSync(releaseDir, { recursive: true, force: true });
  mkdirSync(releaseDir, { recursive: true });
  const work = mkdtempSync(join(tmpdir(), 'tervory-cli-release-'));
  try {
    const packed = JSON.parse(run('npm', ['pack', '--json', '--pack-destination', work], CLI))[0].filename;
    const artifacts = {};
    for (const name of platforms) {
      const stage = join(work, name); const top = `tervory-cli-${version}`; const root = join(stage, top);
      mkdirSync(stage, { recursive: true });
      run('tar', ['--force-local', '-xzf', join(work, packed), '-C', stage]);
      renameSync(join(stage, 'package'), root);
      const manifest = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
      manifest.version = version;
      writeFileSync(join(root, 'package.json'), JSON.stringify(manifest, null, 2) + '\n');
      cpSync(join(CLI, 'package-lock.json'), join(root, 'package-lock.json'));
      const target = PLATFORMS[name];
      const flags = ['ci', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund', '--os', target.os, '--cpu', target.cpu];
      if (target.libc) flags.push('--libc', target.libc);
      run('npm', flags, root);
      if (target.os === 'win32') writeFileSync(join(root, 'tervory.cmd'), '@echo off\r\nnode "%~dp0bin\\tervory.mjs" %*\r\n');
      const binding = join(root, 'node_modules', '@napi-rs', `keyring-${name}`);
      if (!existsSync(binding)) throw new Error(`Keychain binding for ${name} was not installed`);
      rmSync(join(root, 'node_modules', '.package-lock.json'), { force: true });
      writeFileSync(join(root, 'RELEASE.json'), JSON.stringify({ name: 'tervory-cli', version, platform: name }, null, 2) + '\n');
      const file = `${top}-${name}.tar.gz`; const tarFile = join(work, `${file}.tar`);
      run('tar', ['--force-local', '--sort=name', '--mtime=@0', '--owner=0', '--group=0', '--numeric-owner', '--mode=u=rwX,go=rX', '--format=ustar',
                  '-cf', tarFile, '-C', stage, top]);
      writeFileSync(join(releaseDir, file), execFileSync('gzip', ['-n', '-9', '-c', archivePath(tarFile)], { maxBuffer: 1 << 28 }));
      artifacts[name] = { file, sha256: sha256(join(releaseDir, file)), size: statSync(join(releaseDir, file)).size };
    }
    const manifest = { name: 'tervory-cli', version, node: pkg.engines?.node ?? '>=22 <23', artifacts };
    writeFileSync(join(releaseDir, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
    writeFileSync(join(releaseDir, 'SHA256SUMS'),
      Object.values(artifacts).map(a => `${a.sha256}  ${a.file}\n`).join(''));
    // latest.json points at the newest version present in <out>/releases.
    const versions = readdirSync(join(out, 'releases')).filter(v => VERSION.test(v)
      && existsSync(join(out, 'releases', v, 'manifest.json')));
    const newest = versions.sort(compareVersions).at(-1);
    writeFileSync(join(out, 'latest.json'), JSON.stringify({ version: newest, manifest: `releases/${newest}/manifest.json` }, null, 2) + '\n');
    cpSync(join(CLI, 'install.sh'), join(out, 'install.sh'));
    return manifest;
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
}

export function compareVersions(a, b) {
  const x = a.split('.').map(Number); const y = b.split('.').map(Number);
  for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] - y[i];
  return 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2); const get = name => { const i = args.indexOf(`--${name}`); return i === -1 ? undefined : args[i + 1]; };
  if (!get('out')) { console.error('Usage: build-release.mjs --out <dir> [--platforms a,b] [--version X.Y.Z]'); process.exit(2); }
  const manifest = buildRelease({ out: get('out'), platforms: get('platforms')?.split(','), version: get('version') });
  for (const [name, artifact] of Object.entries(manifest.artifacts)) console.log(`${artifact.sha256}  ${artifact.file} (${name}, ${artifact.size} bytes)`);
}
