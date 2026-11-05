import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, generateKeyPairSync, verify } from 'node:crypto';
import { mkdtempSync, readFileSync, writeFileSync, rmSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { signRelease } from '../scripts/sign-release.mjs';
import { verifyRelease } from '../scripts/verify-release.mjs';

test('every platform archive, checksum and manifest is signed; tampering and another key fail', () => {
  const root = mkdtempSync(join(tmpdir(), 'cli-sign-placeholder-'));
  const keys = () => generateKeyPairSync('ed25519', { privateKeyEncoding: { type: 'pkcs8', format: 'pem' }, publicKeyEncoding: { type: 'spki', format: 'pem' } });
  const pair = keys(); const other = keys();
  try {
    const files = ['SHA256SUMS', 'manifest.json', ...['linux-x64-gnu', 'darwin-arm64', 'win32-x64-msvc'].map(p => `tervory-cli-0.5.0-${p}.tar.gz`)];
    for (const name of files) writeFileSync(join(root, name), `placeholder ${name}\n`);
    assert.deepEqual(signRelease(root, pair.privateKey, pair.publicKey), [...files].sort());
    for (const name of files) {
      const bytes = readFileSync(join(root, name)); const signature = Buffer.from(readFileSync(join(root, name + '.sig'), 'utf8').trim(), 'base64');
      assert(verify(null, bytes, pair.publicKey, signature));
      assert(!verify(null, Buffer.concat([bytes, Buffer.from('tampered')]), pair.publicKey, signature));
      assert(!verify(null, bytes, other.publicKey, signature));
    }
    assert.throws(() => signRelease(root, pair.privateKey, other.publicKey), /key refused/);
    assert(!readdirSync(root).some(name => name.endsWith('.pem')));
  } finally { rmSync(root, { recursive: true, force: true }); }
});


test('consumer verifies signatures, sizes and checksum agreement before extraction', () => {
  const root = mkdtempSync(join(tmpdir(), 'cli-verify-placeholder-'));
  const pair = generateKeyPairSync('ed25519', { privateKeyEncoding: { type: 'pkcs8', format: 'pem' }, publicKeyEncoding: { type: 'spki', format: 'pem' } });
  try {
    const file = 'tervory-cli-0.5.0-win32-x64-msvc.tar.gz'; const bytes = Buffer.from('placeholder archive');
    const sha256 = createHash('sha256').update(bytes).digest('hex');
    writeFileSync(join(root, file), bytes);
    writeFileSync(join(root, 'SHA256SUMS'), `${sha256}  ${file}\n`);
    writeFileSync(join(root, 'manifest.json'), JSON.stringify({ version: '0.5.0', artifacts: { 'win32-x64-msvc': { file, size: bytes.length, sha256 } } }));
    signRelease(root, pair.privateKey, pair.publicKey);
    assert.equal(verifyRelease(root, pair.publicKey, 'win32-x64-msvc'), 1);
    assert.throws(() => verifyRelease(root, pair.publicKey, 'win32-x64-msvc', '0.4.0'), /version refused/);
    assert.throws(() => verifyRelease(root, pair.publicKey, 'missing'), /platform refused/);
    writeFileSync(join(root, file), 'tampered');
    assert.throws(() => verifyRelease(root, pair.publicKey), /signature refused/);
    // Even correctly signed bytes must agree with the signed manifest.
    signRelease(root, pair.privateKey, pair.publicKey);
    assert.throws(() => verifyRelease(root, pair.publicKey), /checksum refused/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
