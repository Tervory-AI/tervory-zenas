#!/usr/bin/env node
import { createInterface } from 'node:readline/promises';
import { spawn } from 'node:child_process';
import { hostname } from 'node:os';
import { Writable } from 'node:stream';
import { terminalIO } from '../src/terminal.mjs';
import { keychain, run, safe } from '../src/client.mjs';
import { updateNotice } from '../src/update.mjs';

// Sign-in is a browser device approval: no credential is typed, pasted, passed
// in argv or the environment, or written anywhere except the OS keychain.
function openBrowser(url) {
  if (!/^https:\/\/auth\.tervory\.com(?:\/|$)/.test(url)) return;
  const [command, args] = process.platform === 'darwin' ? ['open', [url]]
    : process.platform === 'win32' ? ['rundll32', ['url.dll,FileProtocolHandler', url]] : ['xdg-open', [url]];
  try {
    return new Promise((resolve, reject) => {
      const child = spawn(command, args, { stdio: 'ignore' });
      const timer = setTimeout(() => { child.kill('SIGKILL'); reject(new Error('Browser launch timed out')); }, 10_000);
      child.once('error', () => { clearTimeout(timer); reject(new Error('Browser unavailable')); });
      child.once('exit', code => { clearTimeout(timer); code === 0 ? resolve() : reject(new Error('Browser unavailable')); });
    });
  } catch { throw new Error('Browser unavailable'); }
}

let readline; let terminal;
const controller = new AbortController();
const cancel = () => { controller.abort(); readline?.close(); };
process.on('SIGINT', cancel);
// Passive "new version available" check for copies installed with install.sh: at
// most once a day, 1.5 s budget, from app.tervory.com/cli only, never fails a command.
// TERVORY_NO_UPDATE_CHECK=1 turns it off; a TERVORY_CLI_VERSION pin turns it off too.
const command = process.argv[2];
let noticeFailed = false;
const notice = command && command !== '-p' && command !== 'update' && !process.argv.includes('--dry-run') && process.env.TERVORY_NO_UPDATE_CHECK !== '1'
  && !(process.env.TERVORY_CLI_VERSION ?? '').trim()
  ? updateNotice({ transport: fetch, onFailure: () => { noticeFailed = true; } }) : Promise.resolve(null);
try {
  if (Number(process.versions.node.split('.')[0]) !== 22) throw new Error('Node 22 is required.');
  await run(process.argv.slice(2), {
    store: {
      get: async () => (await keychain()).get(),
      set: async token => (await keychain()).set(token),
      remove: async () => (await keychain()).remove(),
    },
    io: terminal = terminalIO({
      signal: controller.signal,
      interactive: Boolean(process.stdin.isTTY && process.stdout.isTTY),
      write: value => process.stdout.write(value),
      open: openBrowser,
      deviceName: () => hostname(),
      secret: async label => {
        if (!process.stdin.isTTY || !process.stdout.isTTY) return null;
        process.stdout.write(label);
        const muted = new Writable({ write(chunk, encoding, done) { done(); } });
        const hidden = createInterface({ input: process.stdin, output: muted, terminal: true });
        hidden.on('SIGINT', () => { controller.abort(); hidden.close(); });
        try { return await hidden.question(''); } catch { return null; }
        finally { hidden.close(); process.stdout.write('\n'); }
      },
      prompt: async label => {
        if (!process.stdin.isTTY) return null;
        if (!readline) {
          readline = createInterface({ input: process.stdin, output: process.stdout });
          readline.on('SIGINT', cancel);
        }
        try { return await readline.question(label); } catch { return null; }
      },
    }, {live:value=>process.stderr.write(value),columns:()=>process.stdout.columns||80,tty:Boolean(process.stdout.isTTY && process.stderr.isTTY),env:process.env,plain:process.argv.includes('--plain') || process.argv.includes('--output')}),
  });
} catch (error) {
  terminal?.preview('');
  // Never print native-keychain or transport errors: they can include input.
  const known = /^(?:Personal token refused\.|Tank |Login |Signed |Node 22|Unknown command|Code |Invalid |A thread|Command |Run requires|This tool|Update |Missing setting )/;
  if (!error.reported) process.stderr.write(known.test(error.message) ? `${safe(error.message)}\n` : 'Command failed. Check your OS keychain and Tank connection.\n');
  process.exitCode = error.exitCode ?? 1;
} finally {
  readline?.close(); process.off('SIGINT', cancel);
  const line = await notice;
  if (line) process.stderr.write(safe(line));
  else if (noticeFailed) process.stderr.write('Update check unavailable. Run tervory update to check again.\n');
}
