#!/usr/bin/env node
// Detached Ed25519 signatures. The trusted public key comes from release settings,
// never from the downloaded archive. No signing material is written to disk.
import { createPrivateKey, createPublicKey, sign, verify } from 'node:crypto';
import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export function signRelease(directory, privatePem, publicPem) {
  const key = createPrivateKey(privatePem);
  const trusted = createPublicKey(publicPem);
  if (key.asymmetricKeyType !== 'ed25519' || trusted.asymmetricKeyType !== 'ed25519'
      || !createPublicKey(key).export({ type: 'spki', format: 'der' }).equals(trusted.export({ type: 'spki', format: 'der' }))) {
    throw new Error('Release signing key refused.');
  }
  const files = readdirSync(directory).filter(name => name.endsWith('.tar.gz') || ['SHA256SUMS', 'manifest.json'].includes(name)).sort();
  if (!files.includes('SHA256SUMS') || !files.includes('manifest.json') || !files.some(name => name.endsWith('.tar.gz'))) {
    throw new Error('Release files are missing.');
  }
  for (const name of files) {
    const bytes = readFileSync(join(directory, name));
    const signature = sign(null, bytes, key);
    if (!verify(null, bytes, trusted, signature)) throw new Error('Release signature verification failed.');
    writeFileSync(join(directory, name + '.sig'), signature.toString('base64') + '\n');
  }
  return files;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    if (process.argv.length !== 3) throw new Error();
    signRelease(resolve(process.argv[2]), process.env.CLI_RELEASE_SIGNING_KEY, process.env.CLI_RELEASE_SIGNING_PUBLIC_KEY);
    console.log('Release signatures verified.');
  } catch {
    console.error('Release signing failed. Check CLI_RELEASE_SIGNING_KEY and CLI_RELEASE_SIGNING_PUBLIC_KEY.');
    process.exitCode = 1;
  }
}
