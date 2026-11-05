#!/usr/bin/env node
import { createInterface } from 'node:readline/promises';
import { clearLine, cursorTo } from 'node:readline';
import { spawn } from 'node:child_process';
import { hostname } from 'node:os';
import { keychain, run, safe } from '../src/client.mjs';
import { updateNotice } from '../src/update.mjs';

// Sign-in is a browser device approval: no credential is typed, pasted, passed
// in argv or the environment, or written anywhere except the OS keychain.
function openBrowser(url) {
  if (!/^https:\/\/auth\.tervory\.com(?:\/|$)/.test(url)) return;
  const [command, args] = process.platform === 'darwin' ? ['open', [url]]
    : process.platform === 'win32' ? ['rundll32', ['url.dll,FileProtocolHandler', url]] : ['xdg-open', [url]];
  try {
    const child = spawn(command, args, { stdio: 'ignore', detached: true });
    child.on('error', () => {}); child.unref();
  } catch { /* The printed link still works. */ }
}

let readline;
const controller = new AbortController();
const cancel = () => { controller.abort(); readline?.close(); };
process.on('SIGINT', cancel);
// Passive "new version available" check for copies installed with install.sh: at
// most once a day, 1.5 s budget, from app.tervory.com/cli only, never fails a command.
// TERVORY_NO_UPDATE_CHECK=1 turns it off; a TERVORY_CLI_VERSION pin turns it off too.
const command = process.argv[2];
const notice = command && command !== 'update' && process.env.TERVORY_NO_UPDATE_CHECK !== '1'
  && !(process.env.TERVORY_CLI_VERSION ?? '').trim()
  ? updateNotice({ transport: fetch }) : Promise.resolve(null);
try {
  if (Number(process.versions.node.split('.')[0]) !== 22) throw new Error('Node 22 is required.');
  await run(process.argv.slice(2), {
    store: {
      get: async () => (await keychain()).get(),
      set: async token => (await keychain()).set(token),
      remove: async () => (await keychain()).remove(),
    },
    io: {
      signal: controller.signal,
      interactive: Boolean(process.stdin.isTTY && process.stdout.isTTY),
      write: value => process.stdout.write(safe(value)),
      // Provisional answer stays on one stderr line. Only the validated final
      // answer enters stdout, so replace events never append stale previews.
      preview: value => {
        if (!process.stderr.isTTY) return;
        clearLine(process.stderr, 0); cursorTo(process.stderr, 0);
        const width = Math.max(1, (process.stderr.columns || 80) - 1);
        process.stderr.write(Array.from(safe(value).replace(/\s/g, ' ')).slice(-width).join(''));
      },
      open: openBrowser,
      deviceName: () => hostname(),
      prompt: async label => {
        if (!process.stdin.isTTY) return null;
        if (!readline) {
          readline = createInterface({ input: process.stdin, output: process.stdout });
          readline.on('SIGINT', cancel);
        }
        try { return await readline.question(label); } catch { return null; }
      },
    },
  });
} catch (error) {
  // Never print native-keychain or transport errors: they can include input.
  const known = /^(?:Personal token refused\.|Tank |Login |Signed |Node 22|Unknown command|Code |Invalid |A thread|Command |Run requires|This tool|Update )/;
  process.stderr.write(known.test(error.message) ? `${safe(error.message)}\n` : 'Command failed. Check your OS keychain and Tank connection.\n');
  process.exitCode = 1;
} finally {
  readline?.close(); process.off('SIGINT', cancel);
  const line = await notice;
  if (line) process.stderr.write(safe(line));
}
