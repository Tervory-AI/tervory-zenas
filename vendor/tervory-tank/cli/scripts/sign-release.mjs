#!/usr/bin/env node
// Detached Ed25519 signatures. The trusted public key comes from release settings,
// never from the downloaded archive. No signing material is written to disk.
import { createPrivateKey, createPublicKey, sign, verify } from 'node:crypto';
import { readFileSync, readdirSync, writeFileSync, existsSync } from 'node:fs';
import { join, resolve, dirname, basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ConfigurationError, missingSetting } from '../src/configuration.mjs';

export function signRelease(directory, privatePem, publicPem) {
  if (!privatePem) throw missingSetting('CLI_RELEASE_SIGNING_KEY', 'Release signing did not start.');
  if (!publicPem) throw missingSetting('CLI_RELEASE_SIGNING_PUBLIC_KEY', 'Release signing did not start.');
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
  // The installed key is independent of archive contents and survives updates.
  if (basename(dirname(directory)) === 'releases') {
    const encoded = trusted.export({type:'spki',format:'der'}).toString('base64');
    for (const name of ['install.sh','opli-install.sh']) {
      const installer = join(dirname(dirname(directory)),name);
      if (existsSync(installer)) writeFileSync(installer,readFileSync(installer,'utf8').replace(/PUBLIC_KEY_DER="[A-Za-z0-9+/=]*" # TERVORY_RELEASE_TRUST/,`PUBLIC_KEY_DER="${encoded}" # TERVORY_RELEASE_TRUST`));
    }
  }
  return files;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    if (process.argv.length !== 3) throw new Error();
    signRelease(resolve(process.argv[2]), process.env.CLI_RELEASE_SIGNING_KEY, process.env.CLI_RELEASE_SIGNING_PUBLIC_KEY);
    console.log('Release signatures verified.');
  } catch (error) {
    console.error(error instanceof ConfigurationError ? error.message : 'Release signing failed. Check CLI_RELEASE_SIGNING_KEY and CLI_RELEASE_SIGNING_PUBLIC_KEY.');
    process.exitCode = 1;
  }
}
