import test from 'node:test';
import assert from 'node:assert/strict';
import { update, updateNotice } from '../src/update.mjs';

test('Windows updates refuse and passive checks never suggest the unsupported updater', async () => {
  const descriptor = Object.getOwnPropertyDescriptor(process, 'platform');
  const transport = () => assert.fail('Windows archive installs must not call the POSIX updater');
  try {
    Object.defineProperty(process, 'platform', { value: 'win32' });
    assert.equal(await updateNotice({ transport }), null);
    await assert.rejects(update({ transport, io: { write() { assert.fail('No update started'); } } }),
      { message: 'Update refused: on Windows, verify and extract the signed release archive again.' });
  } finally { Object.defineProperty(process, 'platform', descriptor); }
});
