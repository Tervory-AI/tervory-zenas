#!/usr/bin/env node
// Run from a trusted checkout. Supply the release public key independently of
// the artifact being verified; a key shipped beside an archive is not trusted.
import { createHash, createPublicKey, verify } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { basename, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export function verifyRelease(directory, publicPem, platform, expectedVersion) {
  const key = createPublicKey(publicPem);
  if (key.asymmetricKeyType !== 'ed25519') throw new Error('Release public key refused.');
  const checked = name => {
    if (basename(name) !== name || /[\\/\x00-\x1f]/.test(name)) throw new Error('Release path refused.');
    const bytes = readFileSync(join(directory, name));
    const signature = Buffer.from(readFileSync(join(directory, name + '.sig'), 'utf8').trim(), 'base64');
    if (!verify(null, bytes, key, signature)) throw new Error('Release signature refused.');
    return bytes;
  };
  const manifest = JSON.parse(checked('manifest.json'));
  if (!/^\d{1,4}\.\d{1,4}\.\d{1,4}$/.test(manifest.version)
      || (expectedVersion !== undefined && manifest.version !== expectedVersion)) throw new Error('Release version refused.');
  const sums = checked('SHA256SUMS').toString('utf8');
  const entries = platform ? [[platform, manifest.artifacts?.[platform]]] : Object.entries(manifest.artifacts ?? {});
  if (!entries.length || entries.some(([, item]) => !item)) throw new Error('Release platform refused.');
  for (const [name, entry] of entries) {
    if (entry.file !== `tervory-cli-${manifest.version}-${name}.tar.gz`) throw new Error('Release path refused.');
    const bytes = checked(entry.file);
    const hash = createHash('sha256').update(bytes).digest('hex');
    if (hash !== entry.sha256 || bytes.length !== entry.size || !sums.split('\n').includes(`${hash}  ${entry.file}`)) {
      throw new Error('Release checksum refused.');
    }
  }
  return entries.length;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const [directory, keyFile, platform, version] = process.argv.slice(2);
    if (!directory || !keyFile || !platform || !version || process.argv.length !== 6) throw new Error();
    verifyRelease(resolve(directory), readFileSync(keyFile), platform, version);
    console.log('Release signatures and checksums verified.');
  } catch {
    console.error('Release verification failed. Nothing was installed.'); process.exitCode = 1;
  }
}
