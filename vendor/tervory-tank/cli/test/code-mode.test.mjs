import test from 'node:test';
import assert from 'node:assert/strict';
import fsPromises from 'node:fs/promises';
import { syncBuiltinESMExports } from 'node:module';
import { mkdtemp, writeFile, readFile, mkdir, symlink as nativeSymlink, stat, rm, copyFile, chmod } from 'node:fs/promises';
import { tmpdir, homedir } from 'node:os';
import { createHash } from 'node:crypto';
import { dirname, join, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import { createServer, createConnection } from 'node:net';
import { once } from 'node:events';
import { CodeFolder, LIMITS, redactSecrets, processOutput, applyDiff } from '../src/code-tools.mjs';
import { run } from '../src/client.mjs';

// Windows needs the target kind when creating directory symlinks.
const symlink = async (target, path) => {
  const absolute = resolve(dirname(path), target);
  const kind = process.platform === 'win32' && (await stat(absolute).catch(() => null))?.isDirectory() ? 'junction' : 'file';
  return nativeSymlink(kind === 'junction' ? absolute : target, path, kind);
};
const work = await mkdtemp(join(tmpdir(), 'code-mode-'));
test.after(() => rm(work, { recursive: true, force: true }));
async function folder() {
  const root = await mkdtemp(join(work, 'repo-'));
  execFileSync('git', ['init', '-q', root]);
  await writeFile(join(root, 'file.txt'), 'old\n');
  return CodeFolder.create(root);
}
const io = (answer = 'y', interactive = true) => ({ interactive, write() {}, prompt: async () => answer });
const patch = '--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-old\n+new\n';

test('folder must be a git repository; paths and symlinks cannot escape', async () => {
  const nongit = join(work, 'nongit'); await mkdir(nongit);
  await writeFile(join(nongit, '.git'), 'not a git repository');
  await assert.rejects(CodeFolder.create(nongit), /git repository/);
  const f = await folder();
  await writeFile(join(work, 'outside.txt'), 'outside');
  await symlink(work, join(f.root, 'escape'));
  await symlink(join(work, 'outside.txt'), join(f.root, 'link.txt'));
  for (const name of ['../outside.txt', join(work, 'outside.txt'), 'escape/outside.txt', 'escape/new.txt', 'link.txt', '.git/config']) {
    await assert.rejects(f.read(name));
  }
  await assert.rejects(f.edit('../outside.txt', patch, io()));
  assert.equal(await readFile(join(work, 'outside.txt'), 'utf8'), 'outside');
});

test('secret filenames, binary data and oversized reads are refused; listing respects ignore', async () => {
  const f = await folder();
  for (const name of ['.env', '.env.local', 'cert.pem', 'cert.key', 'id_rsa']) {
    await writeFile(join(f.root, name), 'not read');
    await assert.rejects(f.read(name));
  }
  await writeFile(join(f.root, '.gitignore'), 'ignored.txt\n');
  await writeFile(join(f.root, 'ignored.txt'), 'ignored');
  await writeFile(join(f.root, 'binary'), Buffer.from([0, 1, 2]));
  await writeFile(join(f.root, 'huge'), 'a'.repeat(LIMITS.fileBytes + 1));
  await assert.rejects(f.read('binary')); await assert.rejects(f.read('huge'));
  const names = (await f.files()).files;
  assert(names.includes('file.txt')); assert(!names.includes('ignored.txt')); assert(!names.includes('.env'));
});

test('secret patterns are redacted as whole lines before file/search/git/command results leave', async () => {
  const f = await folder();
  const secret = 'ghp_' + 'A'.repeat(24), assigned = 'password=' + 'Z'.repeat(14);
  await writeFile(join(f.root, 'source.txt'), `safe\n${secret}\n${assigned}\n`);
  const result = await f.read('source.txt');
  assert.equal(result.text, 'safe\n[redacted]\n[redacted]\n');
  assert(!JSON.stringify(await f.search('redacted')).includes(secret));
  const output = await processOutput(process.execPath, ['-e', `process.stdout.write(${JSON.stringify(secret + '\n')})`], { cwd: f.root });
  assert.equal(output.output, '[redacted]\n');
  assert.equal(redactSecrets('sk-' + 'a'.repeat(24)), '[redacted]');
});

test('literal/regex search caps at 200 matches and regex execution is bounded', async () => {
  const f = await folder();
  await writeFile(join(f.root, 'source.txt'), 'match\n'.repeat(230));
  assert.equal((await f.search('match')).matches.length, 200);
  assert.equal((await f.search('^match$', true)).matches.length, 200);
  await writeFile(join(f.root, 'source.txt'), 'a'.repeat(10000) + '!');
  await assert.rejects(f.search('(a+)+$', true));
});

test('edits require a prior read and literal y; stale file and approval-time mutations refuse', async () => {
  const f = await folder();
  await assert.rejects(f.edit('file.txt', patch, io()));
  await f.read('file.txt');
  for (const answer of ['N', 'yes', '', null]) assert.equal((await f.edit('file.txt', patch, io(answer))).applied, false);
  assert.equal((await f.edit('file.txt', patch, io('y', false))).applied, false);
  assert.equal(await readFile(join(f.root, 'file.txt'), 'utf8'), 'old\n');
  assert.equal((await f.edit('file.txt', patch, io())).applied, true);
  await writeFile(join(f.root, 'file.txt'), 'changed\n');
  await assert.rejects(f.edit('file.txt', patch, io()));
  await writeFile(join(f.root, 'file.txt'), 'old\n'); await f.read('file.txt');
  await assert.rejects(f.edit('file.txt', patch, { ...io(), prompt: async () => {
    await writeFile(join(f.root, 'file.txt'), 'during approval\n'); return 'y';
  } }));
});

test('new file diff requires a missing-file read; malformed/context-mismatched diffs refuse', async () => {
  const f = await folder();
  const add = '--- /dev/null\n+++ b/test.txt\n@@ -0,0 +1 @@\n+test\n';
  await assert.rejects(f.edit('test.txt', add, io()));
  await f.read('test.txt'); assert.equal((await f.edit('test.txt', add, io())).applied, true);
  assert.equal(await readFile(join(f.root, 'test.txt'), 'utf8'), 'test\n');
  await assert.rejects(applyDiff('different\n', patch, 'file.txt', false, f.root));
  await assert.rejects(applyDiff('old\n', patch.replace('b/file.txt', 'b/../outside.txt'), 'file.txt', false, f.root));
});

test('commands show in full, require y and cannot run non-interactively; cwd and output cap', { skip: process.platform === 'win32' && 'Sandbox confinement applies where the OS sandbox exists' }, async () => {
  const f = await folder();
  const command = 'printf done > marker'; const request = { name: 'run_command', arguments: { command } };
  let shown = '';
  for (const terminal of [io('N'), io('yes'), io('y', false)]) {
    terminal.write = text => { shown += text; };
    assert.equal((await f.tool(request, terminal)).ran, false);
  }
  await assert.rejects(readFile(join(f.root, 'marker')));
  assert(shown.includes(command));
  const executed = await f.tool(request, io());
  assert.equal(executed.ran, true); assert.equal(executed.code, 0, executed.output);
  assert.equal(await readFile(join(f.root, 'marker'), 'utf8'), 'done');
  const output = await processOutput(process.execPath, ['-e', 'process.stdout.write("x".repeat(100000))'], { cwd: f.root });
  assert(output.truncated); assert(Buffer.byteLength(output.output) <= LIMITS.outputBytes);
  const timed = await processOutput(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { cwd: f.root, timeoutMs: 80 });
  assert(timed.timed_out);
});

test('fake Tank code loop uses existing region/token, redacts results and never retries a failed turn', async () => {
  const f = await folder();
  const secret = 'ghp_' + 'B'.repeat(24);
  await writeFile(join(f.root, 'file.txt'), `safe\n${secret}\n`);
  const token = 'tv1.za.' + '1'.repeat(32) + '.' + ('placeholder' + 'x'.repeat(43)).slice(0, 43);
  const received = []; let output = ''; let prompts = ['inspect file', '/exit'];
  await run(['code', f.root], { store: { get: async () => token },
    io: { interactive: true, prompt: async () => prompts.shift(), write: value => { output += value; } },
    transport: async (url, init) => {
      assert.equal(new URL(url).origin, 'https://za.tervory.com');
      assert.equal(init.headers.Authorization, 'Bearer ' + token);
      if (url.endsWith('/threads')) return Response.json({ thread_id: 'thread' });
      received.push(JSON.parse(init.body));
      if (received.length === 1) return Response.json({ answer: '', requests: [{ name: 'read_file', arguments: { path: 'file.txt' } }] });
      assert(!init.body.includes(secret)); assert(init.body.includes('[redacted]'));
      return Response.json({ answer: 'Ready', requests: [], receipt: { rows: [] } });
    } });
  assert.equal(received.length, 2); assert(output.includes('Ready')); assert(!output.includes(secret));
  let calls = 0; prompts = ['inspect file'];
  await assert.rejects(run(['code', f.root], { store: { get: async () => token },
    io: { interactive: true, prompt: async () => prompts.shift(), write() {} },
    transport: async url => { if (url.endsWith('/threads')) return Response.json({ thread_id: 'thread' }); calls++; throw Error('offline'); } }));
  assert.equal(calls, 1);
});

test('list caps at 2000 entries, including repositories with more than 64 KB of names', async () => {
  const f = await folder();
  await mkdir(join(f.root, 'many'));
  await Promise.all(Array.from({ length: 2010 }, (_, i) => writeFile(join(f.root, 'many', `${'long-name-'.repeat(4)}${i}.txt`), '')));
  const result = await f.files();
  assert.equal(result.files.length, 2000); assert(result.truncated);
});

test('approved command cannot read secret paths, outside symlinks or inherited host credentials', { skip: process.platform === 'win32' && 'Sandbox confinement applies where the OS sandbox exists' }, async () => {
  const f = await folder();
  await writeFile(join(f.root, '.env'), 'private-file-canary');
  await writeFile(join(work, 'host-private.txt'), 'outside-file-canary');
  await symlink(join(work, 'host-private.txt'), join(f.root, 'outside-link'));
  const result = await f.tool({ name: 'run_command', arguments: {
    command: `/bin/cat .env; /bin/cat outside-link; /bin/cat ${join(work, 'host-private.txt')}; /usr/bin/printenv; echo sandbox-ok`,
  } }, io());
  assert.equal(result.code, 0, result.output);
  assert(result.output.includes('sandbox-ok'), result.output);
  assert(!result.output.includes('private-file-canary'));
  assert(!result.output.includes('outside-file-canary'));
  assert(!result.output.includes('TANK_API_KEY='));
  await assert.rejects(f.edit('.env', patch, io()));
});

test('fake Tank can request a missing-file read, explicit edit and explicit test run without a new sign-in', { skip: process.platform === 'win32' && 'Sandbox confinement applies where the OS sandbox exists' }, async () => {
  const f = await folder();
  const token = 'tv1.us-east.' + '1'.repeat(32) + '.' + ('placeholder' + 'x'.repeat(43)).slice(0, 43);
  const requests = [
    { name: 'read_file', arguments: { path: 'small.test.mjs' } },
    { name: 'propose_edit', arguments: { path: 'small.test.mjs', diff: '--- /dev/null\n+++ b/small.test.mjs\n@@ -0,0 +1,2 @@\n+import test from "node:test";\n+test("small test", () => {});\n' } },
    { name: 'run_command', arguments: { command: 'node --test small.test.mjs' } },
  ];
  let count = 0; let prompts = ['add a small test', 'y', 'y', '/exit']; const results = [];
  await run(['code', f.root], { store: { get: async () => token }, io: {
    interactive: true, prompt: async () => prompts.shift(), write() {},
  }, transport: async (url, init) => {
    if (url.endsWith('/threads')) return Response.json({ thread_id: 'thread' });
    const body = JSON.parse(init.body); results.push(body.tool_results);
    assert.equal(new URL(url).origin, 'https://app.tervory.com');
    return Response.json({ answer: '', requests: count < requests.length ? [requests[count++]] : [] });
  } });
  assert((await readFile(join(f.root, 'small.test.mjs'), 'utf8')).includes('small test'));
  const execution = JSON.parse(results.at(-1).find(r => r.name === 'run_command').output).result;
  assert.equal(execution.code, 0); assert(execution.output.includes('# pass 1'));
});

test('all repository live-key families and assigned-secret lines are redacted', () => {
  // Synthetic values assembled at runtime, never credentials or live data.
  const samples = [
    '-----BEGIN ' + 'PRIVATE KEY-----', 'AKIA' + '0'.repeat(16), 'ghp_' + 'a'.repeat(24),
    'github_pat_' + 'a'.repeat(24), 'xoxb-' + 'a'.repeat(12), 'sk-ant-' + 'a'.repeat(12),
    'sk-' + 'a'.repeat(24), ...['sk', 'pk', 'rk'].map(prefix => prefix + '_live_' + 'a'.repeat(12)),
    'AIza' + 'a'.repeat(30), 'eyJ' + 'a'.repeat(20) + '.' + 'a'.repeat(10) + '.' + 'a'.repeat(10),
    ...['API_KEY', 'secret-key', 'client_secret', 'password', 'passwd', 'access_token', 'auth-token']
      .map(name => name + '=' + 'a'.repeat(14)),
  ];
  for (const value of samples) assert.equal(redactSecrets(value), '[redacted]');
});

test('git status and diff work without exposing secret-file contents', async () => {
  const f = await folder();
  execFileSync('git', ['add', 'file.txt'], { cwd: f.root });
  await writeFile(join(f.root, 'file.txt'), 'changed\n');
  await writeFile(join(f.root, '.e[n]v'), 'old glob file\n');
  await writeFile(join(f.root, '.env'), 'old private file\n');
  execFileSync('git', ['--literal-pathspecs', 'add', '.e[n]v', '.env'], { cwd: f.root });
  await writeFile(join(f.root, '.e[n]v'), 'changed glob file\n');
  await writeFile(join(f.root, '.env'), 'private-git-canary');
  const status = await f.tool({ name: 'git', arguments: { action: 'status' } }, io());
  const diff = await f.tool({ name: 'git', arguments: { action: 'diff' } }, io());
  assert(status.output.includes('file.txt')); assert(diff.output.includes('+changed'));
  assert(diff.output.includes('+changed glob file'));
  assert(!diff.output.includes('private-git-canary'));
  assert(!diff.output.includes('old private file'));
});

test('cancelling at command approval stops execution and any following model turn', async () => {
  const f = await folder(); const controller = new AbortController(); let calls = 0;
  const token = 'tv1.us-east.' + '1'.repeat(32) + '.' + ('placeholder' + 'x'.repeat(43)).slice(0, 43);
  await assert.rejects(run(['code', f.root], { store: { get: async () => token },
    io: { interactive: true, signal: controller.signal, write() {}, prompt: async label => {
      if (label.startsWith('Run')) { controller.abort(); return 'y'; }
      return 'run a test';
    } },
    transport: async url => {
      if (url.endsWith('/threads')) return Response.json({ thread_id: 'thread' });
      calls++;
      return Response.json({ answer: '', requests: [{ name: 'run_command', arguments: { command: 'printf wrong > cancel-marker' } }] });
    },
  }), /cancelled/);
  assert.equal(calls, 1);
  await assert.rejects(readFile(join(f.root, 'cancel-marker')));
});

test('git read tools cannot execute repository clean/process filters without approval', async () => {
  const f = await folder();
  const sub = join(f.root, 'sub'); await mkdir(sub);
  execFileSync('git', ['init', '-q', sub]);
  await writeFile(join(sub, 'file.txt'), 'sub original\n');
  execFileSync('git', ['add', 'file.txt'], { cwd: sub });
  execFileSync('git', ['-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'sub'], { cwd: sub });
  await writeFile(join(f.root, '.gitmodules'), '[submodule "sub"]\npath = sub\nurl = ./sub\n');
  execFileSync('git', ['add', 'sub', '.gitmodules'], { cwd: f.root, stdio: 'pipe' });
  execFileSync('git', ['-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'parent'], { cwd: f.root });
  execFileSync('git', ['config', 'filter.subguard.clean', 'sh -c "printf wrong > sub-filter-marker; cat"'], { cwd: sub });
  await writeFile(join(sub, '.gitattributes'), 'file.txt filter=subguard\n');
  await writeFile(join(sub, 'file.txt'), 'sub modified\n');
  // Positive control: a normal status must execute this exact fixture's
  // submodule filter. Size changes alone can skip it and give false assurance.
  execFileSync('git', ['--literal-pathspecs', 'status', '--short', '--', '.'], { cwd: f.root });
  assert.equal(await readFile(join(sub, 'sub-filter-marker'), 'utf8'), 'wrong');
  await rm(join(sub, 'sub-filter-marker'));
  execFileSync('git', ['add', 'file.txt'], { cwd: f.root });
  execFileSync('git', ['config', 'filter.guard.clean', 'sh -c "printf wrong > filter-marker; cat"'], { cwd: f.root });
  await writeFile(join(f.root, '.gitattributes'), 'file.txt filter=guard\n');
  await writeFile(join(f.root, 'file.txt'), 'changed\n');
  const diff = await f.tool({ name: 'git', arguments: { action: 'diff' } }, io());
  await f.tool({ name: 'git', arguments: { action: 'status' } }, io());
  assert(diff.output.includes('+changed'));
  await assert.rejects(readFile(join(f.root, 'filter-marker')));
  await assert.rejects(readFile(join(sub, 'sub-filter-marker')));
});


test('missing files and approval-time ancestor swaps cannot create Git hooks', async () => {
  const f = await folder();
  const add = '--- /dev/null\n+++ b/lnk/hooks/post-checkout\n@@ -0,0 +1 @@\n+wrong\n';
  await symlink('.git', join(f.root, 'lnk'));
  await assert.rejects(f.read('lnk/hooks/post-checkout'));
  await assert.rejects(f.edit('lnk/hooks/post-checkout', add, io()));
  await assert.rejects(readFile(join(f.root, '.git/hooks/post-checkout')));
  await rm(join(f.root, 'lnk'));
  await mkdir(join(f.root, 'lnk/hooks'), { recursive: true });
  await f.read('lnk/hooks/post-checkout');
  await assert.rejects(f.edit('lnk/hooks/post-checkout', add, { ...io(), prompt: async () => {
    await rm(join(f.root, 'lnk'), { recursive: true });
    await symlink('.git', join(f.root, 'lnk')); return 'y';
  } }));
  await assert.rejects(readFile(join(f.root, '.git/hooks/post-checkout')));
  await mkdir(join(f.root, '.GIT/hooks'), { recursive: true });
  await assert.rejects(f.read('.GIT/hooks/new'));
});

test('Git reads remain pinned after a nested repository appears', async () => {
  const f = await folder();
  await mkdir(join(f.root, 'selected'));
  await writeFile(join(f.root, 'selected/original.txt'), 'parent tracked\n');
  execFileSync('git', ['add', 'selected/original.txt'], { cwd: f.root });
  const selected = await CodeFolder.create(join(f.root, 'selected'));
  // Simulate another process creating the nested repository: Seatbelt refuses .git writes.
  execFileSync('git', ['init', '-q'], { cwd: selected.root });
  await writeFile(join(selected.root, 'original.txt'), 'changed parent\n');
  const status = await selected.tool({ name: 'git', arguments: { action: 'status' } }, io());
  assert(status.output.includes('AM original.txt'), status.output);
  assert((await selected.diff()).output.includes('+changed parent'));
  assert((await selected.files()).files.includes('original.txt'));
});

test('external Node runtime is read-only, named before approval and cannot expose HOME', { skip: process.platform === 'win32' && 'Sandbox confinement applies where the OS sandbox exists' }, async () => {
  const f = await folder();
  const runtime = await mkdtemp(join(work, 'runtime-'));
  await mkdir(join(runtime, 'bin'));
  const node = join(runtime, 'bin/node'); await copyFile(process.execPath, node);
  await writeFile(join(runtime, 'bin/.npmrc'), 'neighbor-file-canary');
  await writeFile(join(f.root, 'real.test.mjs'), 'import test from "node:test"; test("external runner", () => {});\n');
  let shown = '';
  const result = await f.tool({ name: 'run_command', arguments: { command: `${node} --test real.test.mjs` } }, {
    ...io(), write: value => { shown += value; }, prompt: async () => { assert(shown.includes(node)); return 'y'; },
  });
  assert.equal(result.code, 0, result.output); assert(result.output.includes('# pass 1'));
  const neighbor = await f.tool({ name: 'run_command', arguments: { command: `${node} -e 'console.log("runner-ok"); try { console.log(require("fs").readFileSync("${runtime}/bin/.npmrc", "utf8")); } catch {}'` } }, io());
  assert.equal(neighbor.code, 0, neighbor.output); assert(neighbor.output.includes('runner-ok'));
  assert(!neighbor.output.includes('neighbor-file-canary'));
  const beforeHash = createHash('sha256').update(await readFile(node)).digest('hex');
  const write = await f.tool({ name: 'run_command', arguments: { command: `${node} -e 'require("fs").writeFileSync("${node}", "wrong")'` } }, io());
  assert.notEqual(write.code, 0);
  assert.equal(createHash('sha256').update(await readFile(node)).digest('hex'), beforeHash);
});

test('session history is bounded, redacted and transient; continuation rounds share one prompt id', async () => {
  const f = await folder();
  const token = 'tv1.za.' + '1'.repeat(32) + '.' + ('placeholder' + 'x'.repeat(43)).slice(0, 43);
  const secret = 'ghp_' + 'C'.repeat(24);
  const received = []; const prompts = ['first prompt', `second prompt ${secret}`, '/exit'];
  await run(['code', f.root], { store: { get: async () => token }, io: {
    interactive: true, prompt: async () => prompts.shift(), write() {},
  }, transport: async (url, init) => {
    if (url.endsWith('/threads')) return Response.json({ thread_id: 'thread' });
    const body = JSON.parse(init.body); received.push(body);
    assert(!init.body.includes(secret));
    return Response.json({ answer: 'earlier answer', requests: received.length < 3 ? [{ name: 'read_file', arguments: { path: 'file.txt' } }] : [] });
  } });
  assert.deepEqual(received.map(r => r.continuation), [false, true, true, false]);
  assert.equal(new Set(received.slice(0, 3).map(r => r.prompt_id)).size, 1);
  assert.notEqual(received[0].prompt_id, received[3].prompt_id);
  const history = received[3].history;
  assert(history.some(h => h.kind === 'user' && h.content === 'first prompt'));
  assert(history.some(h => h.kind === 'assistant' && h.content === 'earlier answer'));
  assert(history.some(h => h.kind === 'tool' && h.content.includes('read_file') && h.content.includes('file.txt')));
  assert.equal(received[3].text, '[redacted]');
  let next = 0; let lastHistory;
  await run(['code', f.root], { store: { get: async () => token }, io: {
    interactive: true, prompt: async () => next < 60 ? `turn${next++}` : '/exit', write() {},
  }, transport: async (url, init) => {
    if (url.endsWith('/threads')) return Response.json({ thread_id: 'bounded-thread' });
    const body = JSON.parse(init.body); lastHistory = body.history;
    assert(body.history.length <= 40);
    assert(body.history.reduce((n, item) => n + Buffer.byteLength(item.content), 0) <= 200000);
    return Response.json({ answer: 'a'.repeat(39000), requests: [] });
  } });
  assert(!lastHistory.some(item => item.content === 'turn0'));
  assert(lastHistory.some(item => item.content === 'turn58'));
});


test('external Node intermediate links do not expose their host directory', { skip: process.platform === 'win32' && 'Sandbox confinement applies where the OS sandbox exists' }, async () => {
  const f = await folder();
  const install = await mkdtemp(join(work, 'node-install-'));
  const middle = await mkdtemp(join(work, 'node-links-'));
  await mkdir(join(install, 'bin'));
  await symlink(process.execPath, join(middle, 'node'));
  await symlink(join(middle, 'node'), join(install, 'bin/node'));
  await writeFile(join(middle, 'private.txt'), 'unmounted-directory-canary');
  const command = `${join(install, 'bin/node')} -e 'console.log("runtime-ok"); try { console.log(require("fs").readFileSync("${middle}/private.txt", "utf8")); } catch {}'`;
  const result = await f.tool({ name: 'run_command', arguments: { command } }, io());
  assert.equal(result.code, 0, result.output);
  assert(result.output.includes('runtime-ok'));
  assert(!result.output.includes('unmounted-directory-canary'));
});


test('external runtime rejects directories, nonexecutables and their symlinks before approval', { skip: process.platform === 'win32' && 'Sandbox confinement applies where the OS sandbox exists' }, async () => {
  const f = await folder(); const outside = await mkdtemp(join(work, 'other-projects-'));
  await mkdir(join(outside, 'client-a/.aws'), { recursive: true });
  await writeFile(join(outside, 'client-a/.npmrc'), 'unrelated-npm-canary');
  await writeFile(join(outside, 'client-a/.aws/credentials'), 'unrelated-aws-canary');
  let prompts = 0;
  for (const name of ['node', 'python', 'pytest']) {
    const directory = join(outside, name); await mkdir(directory);
    await assert.rejects(f.tool({ name: 'run_command', arguments: { command: `'${directory}' --version; cat '${outside}/client-a/.npmrc'` } }, {
      ...io(), prompt: async () => { prompts++; return 'y'; },
    }));
    const aliases = join(outside, name + '-links'); await mkdir(aliases);
    await symlink(directory, join(aliases, name));
    await assert.rejects(f.prepareCommand(`'${join(aliases, name)}' --version`));
  }
  const bin = join(outside, 'nonexec'); await mkdir(bin);
  const file = join(bin, 'node'); await writeFile(file, 'not executable'); await chmod(file, 0o600);
  await symlink(file, join(bin, 'python'));
  await assert.rejects(f.prepareCommand(`'${file}' --version`));
  await assert.rejects(f.prepareCommand(`'${join(bin, 'python')}' --version`));
  assert.equal(prompts, 0);
});

test('external runtimes refuse pip-user and uv launchers without exposing .local user data', { skip: process.platform === 'win32' && 'Sandbox confinement applies where the OS sandbox exists' }, async () => {
  const f = await folder(); const home = await mkdtemp(join(work, 'user-home-'));
  const local = join(home, '.local'); await mkdir(join(local, 'bin'), { recursive: true });
  await mkdir(join(local, 'share/python_keyring'), { recursive: true });
  await mkdir(join(local, 'share/fish'), { recursive: true });
  await writeFile(join(local, 'share/python_keyring/keyring_pass.cfg'), 'unrelated-keyring-canary');
  await writeFile(join(local, 'share/fish/fish_history'), 'unrelated-history-canary');
  const python = join(local, 'bin/python3'); await symlink(process.execPath, python);
  const pytest = join(local, 'bin/pytest'); await writeFile(pytest, `#!${python}\n`); await chmod(pytest, 0o700);
  // Include plausible pip-user libraries: they still cannot authorize a .local bind.
  await mkdir(join(local, 'lib/python3.12/site-packages'), { recursive: true });
  for (const runner of [python, pytest]) {
    let prompts = 0;
    await assert.rejects(f.tool({ name: 'run_command', arguments: {
      command: `'${runner}' --version; cat '${local}/share/python_keyring/keyring_pass.cfg' '${local}/share/fish/fish_history'`,
    } }, { ...io(), prompt: async () => { prompts++; return 'y'; } }));
    assert.equal(prompts, 0);
  }
});

test('code catalogue cards use Confirm, follow the thread and return redacted results as DATA', async () => {
  for (const answer of ['y', 'n', null]) {
    const f = await folder();
    const placeholderSecret = 'ghp_' + 'placeholder'.repeat(3);
    await writeFile(join(f.root, 'AGENTS.md'), `Branches use codex/task.\n${placeholderSecret}\n`);
    const token = 'tv1.us-east.' + '1'.repeat(32) + '.' + ('placeholder' + 'x'.repeat(43)).slice(0, 43);
    const prompts = ['read findings', answer, '/cost', '/exit'];
    const requests = []; let turns = 0; let action; let shown = '';
    const card = { card_id: 'placeholder-card', tool: 'radix.repo.findings', status: 'pending', summary: 'Read findings', arguments: { scan_id: 'placeholder-scan' } };
    const stored = { ...card, status: answer === 'y' ? 'confirmed' : 'cancelled', result: { text: placeholderSecret } };
    await run(['code', f.root], { store: { get: async () => token },
      io: { interactive: true, write: value => { shown += value; }, prompt: async () => prompts.shift() ?? null },
      transport: async (url, init) => {
        if (url.endsWith('/threads')) return Response.json({ thread_id: 'placeholder-thread' });
        if (url.endsWith('/code-turn')) {
          const body = JSON.parse(init.body); requests.push(body);
          assert.equal(body.history[0].kind, 'project');
          assert(body.history[0].content.includes('Branches use codex/task.'));
          assert(!init.body.includes(placeholderSecret));
          return Response.json(turns++ === 0 ? { answer: '', requests: [{ name: 'tervory', arguments: { tool: card.tool, arguments: {} } }], cards: [card] } : { answer: '', requests: [], turn: { model: 'Auto', cost_usd: 0.002 } });
        }
        if (url.endsWith('/cards/placeholder-card')) {
          action = JSON.parse(init.body).action;
          return Response.json(stored);
        }
        assert(url.endsWith('/threads/placeholder-thread'));
        return Response.json({ thread: { spent_usd: 0.1 }, cards: [stored], steps: [{ step_id: card.card_id, status: 'done' }] });
      } });
    assert(shown.includes(JSON.stringify({ tool: card.tool, arguments: card.arguments }) + '\n'));
    assert.equal(action, answer === 'y' ? 'confirm' : 'cancel');
    if (answer !== 'y') {
      assert.equal(requests.length, 1, 'denial stops before another model turn');
      continue;
    }
    const result = JSON.parse(requests[1].tool_results[0].output).result;
    assert(shown.includes('Session cost $0.102000'));
    assert.equal(result.tool, card.tool);
    assert.equal(result.status, stored.status);
    if (answer !== 'y') assert(!('result' in result));
    assert(!JSON.stringify(result).includes(placeholderSecret));
  }
});

test('project notes use the git root and skip invalid files without aborting', async () => {
  const f = await folder();
  await mkdir(join(f.root, 'child'));
  await writeFile(join(f.root, 'AGENTS.md'), 'placeholder notes');
  const { codeSession } = await import('../src/code-mode.mjs');
  const bodies = [];
  const tank = { json: async (path, body) => {
    if (path.endsWith('/threads')) return { thread_id: 'placeholder-thread' };
    bodies.push(body); return { answer: '', requests: [] };
  } };
  const prompts = ['hello', '/exit'];
  await codeSession(tank, join(f.root, 'child'), 'auto', { interactive: true, write() {}, prompt: async () => prompts.shift() }, () => [], x => x);
  assert.deepEqual(bodies[0].history, [{ kind: 'project', content: 'placeholder notes' }]);
  for (const kind of ['oversized', 'symlink', 'non-UTF8']) {
    await rm(join(f.root, 'AGENTS.md'), { force: true });
    if (kind === 'symlink') await symlink(join(f.root, 'file.txt'), join(f.root, 'AGENTS.md'));
    else await writeFile(join(f.root, 'AGENTS.md'), kind === 'oversized' ? 'x'.repeat(200001) : Buffer.from([0xff]));
    const inputs = ['placeholder prompt', '/exit']; let shown = '';
    await codeSession(tank, f.root, 'auto', { ...io(), write: value => { shown += value; }, prompt: async () => inputs.shift() }, () => [], x => x);
    assert(shown.includes('AGENTS.md skipped: Code tool refused.\n'));
    assert.deepEqual(bodies.at(-1).history, []);
  }
  assert.equal(bodies.length, 4);
  await assert.rejects(codeSession(tank, f.root, 'auto', io('y', false), () => [], x => x));
});

async function changeSet() {
  const f = await folder();
  const names = ['file.txt', 'second.txt', 'third.txt'];
  for (const name of names) { await writeFile(join(f.root, name), 'old\n'); await f.read(name); }
  const diff = names.map(name => `--- a/${name}\n+++ b/${name}\n@@ -1 +1 @@\n-old\n+new\n`).join('');
  return { f, names, diff };
}

test('three-file change set uses one approval; undo restores all files with one explicit y', async () => {
  const { f, names, diff } = await changeSet(); let approvals = 0; let shown = '';
  const applied = await f.edit(undefined, diff, { ...io(), write: text => { shown += text; }, prompt: async () => { approvals++; return 'y'; } });
  assert(applied.applied); assert.equal(approvals, 1);
  for (const name of names) { assert(shown.includes(`+++ b/${name}`)); assert.equal(await readFile(join(f.root, name), 'utf8'), 'new\n'); }
  for (const answer of ['n', 'yes', null]) assert.equal((await f.undo(io(answer))).applied, false);
  await assert.rejects(f.undo(io('y', false)));
  await f.undo(io());
  for (const name of names) assert.equal(await readFile(join(f.root, name), 'utf8'), 'old\n');
  await assert.rejects(f.undo(io()));
});

test('stale files, failing hunks, duplicate paths, escapes and secrets refuse the whole set', async () => {
  for (const reason of ['stale', 'hunk', 'escape', 'secret', 'duplicate']) {
    const { f, names, diff } = await changeSet(); let approvals = 0;
    let proposal = diff;
    if (reason === 'stale') await writeFile(join(f.root, names[2]), 'changed\n');
    if (reason === 'hunk') proposal = diff.replaceAll('--- a/third.txt\n+++ b/third.txt\n@@ -1 +1 @@\n-old', '--- a/third.txt\n+++ b/third.txt\n@@ -1 +1 @@\n-wrong');
    if (reason === 'escape') proposal = diff.replaceAll('third.txt', '../placeholder-outside.txt');
    if (reason === 'secret') proposal = diff.replaceAll('third.txt', '.env.placeholder');
    if (reason === 'duplicate') proposal = diff + patch;
    await assert.rejects(f.edit(undefined, proposal, { ...io(), prompt: async () => { approvals++; return 'y'; } }));
    assert.equal(approvals, 0);
    for (const name of names) assert.equal(await readFile(join(f.root, name), 'utf8'), reason === 'stale' && name === names[2] ? 'changed\n' : 'old\n');
  }
  const { f, names, diff } = await changeSet();
  await assert.rejects(f.edit(undefined, diff, { ...io(), prompt: async () => {
    await writeFile(join(f.root, names[2]), 'approval-change\n'); return 'y';
  } }));
  assert.equal(await readFile(join(f.root, names[0]), 'utf8'), 'old\n');
  const twentyOne = Array.from({ length: 21 }, (_, i) => `--- /dev/null\n+++ b/placeholder-${i}.txt\n@@ -0,0 +1 @@\n+placeholder\n`).join('');
  await assert.rejects(f.edit(undefined, twentyOne, io()));
});

test('undo refuses a later file change, including changes during approval, without changing others', async () => {
  const { f, names, diff } = await changeSet();
  await f.edit(undefined, diff, io());
  await writeFile(join(f.root, names[1]), 'later\n');
  await assert.rejects(f.undo(io()));
  assert.equal(await readFile(join(f.root, names[0]), 'utf8'), 'new\n');
  await writeFile(join(f.root, names[1]), 'new\n');
  await assert.rejects(f.undo({ ...io(), prompt: async () => { await writeFile(join(f.root, names[2]), 'during-undo\n'); return 'y'; } }));
  assert.equal(await readFile(join(f.root, names[0]), 'utf8'), 'new\n');
});

test('normal rename failure rolls back; a killed process leaves each file entirely old or new', async () => {
  const { f, names, diff } = await changeSet();
  const renameFile = f.renameFile.bind(f); let renames = 0;
  f.renameFile = async (...args) => { if (++renames === 2) throw Error('placeholder disk error'); await renameFile(...args); };
  await assert.rejects(f.edit(undefined, diff, io()));
  for (const name of names) assert.equal(await readFile(join(f.root, name), 'utf8'), 'old\n');
  for (const stop of [1, 2]) {
    const fixture = await changeSet();
    const source = `import { CodeFolder } from ${JSON.stringify(new URL('../src/code-tools.mjs', import.meta.url).href)};
      const f = await CodeFolder.create(${JSON.stringify(fixture.f.root)});
      for (const name of ${JSON.stringify(fixture.names)}) await f.read(name);
      const rename = f.renameFile.bind(f); let n = 0;
      f.renameFile = async (...args) => { await rename(...args); if (++n === ${stop}) process.kill(process.pid, 'SIGKILL'); };
      await f.edit(undefined, ${JSON.stringify(fixture.diff)}, { interactive: true, write() {}, prompt: async () => 'y' });`;
    assert.throws(() => execFileSync(process.execPath, ['--input-type=module', '-e', source]));
    for (let i = 0; i < fixture.names.length; i++) assert.equal(await readFile(join(fixture.f.root, fixture.names[i]), 'utf8'), i < stop ? 'new\n' : 'old\n');
  }
});

test('undo retains ten sets in memory and removes a newly created file', async () => {
  const f = await folder();
  await f.read('placeholder-new.txt');
  await f.edit('placeholder-new.txt', '--- /dev/null\n+++ b/placeholder-new.txt\n@@ -0,0 +1 @@\n+placeholder\n', io());
  await f.undo(io()); await assert.rejects(readFile(join(f.root, 'placeholder-new.txt')));
  await f.read('file.txt');
  let before = 'old';
  for (let i = 0; i < 11; i++) {
    const after = `placeholder-${i}`;
    await f.edit('file.txt', `--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-${before}\n+${after}\n`, io());
    before = after;
  }
  for (let i = 0; i < 10; i++) await f.undo(io());
  assert.equal(await readFile(join(f.root, 'file.txt'), 'utf8'), 'placeholder-0\n');
  await assert.rejects(f.undo(io()));
});

test('/undo is local and never sends its command or snapshot to Tank', async () => {
  const f = await folder(); const prompts = ['placeholder edit', 'y', '/undo', 'y', '/exit']; let calls = 0;
  const { codeSession } = await import('../src/code-mode.mjs');
  await codeSession({ json: async (path, body) => {
    if (path.endsWith('/threads')) return { thread_id: 'placeholder-thread' };
    assert(!JSON.stringify(body).includes('/undo'));
    calls++;
    return { answer: '', requests: calls === 1 ? [{ name: 'read_file', arguments: { path: 'file.txt' } }] : calls === 2 ? [{ name: 'propose_edit', arguments: { path: 'file.txt', diff: patch } }] : [] };
  } }, f.root, 'auto', { interactive: true, write() {}, prompt: async () => prompts.shift() }, () => [], x => x);
  assert.equal(calls, 3);
  assert.equal(await readFile(join(f.root, 'file.txt'), 'utf8'), 'old\n');
});

test('sandbox confines outside writes, secret reads and network while echo succeeds', { skip: process.platform === 'win32' && 'Sandbox confinement applies where the OS sandbox exists' }, async () => {
  const f = await folder(); const outside = join(work, 'placeholder-outside-write');
  await writeFile(join(f.root, '.env.placeholder'), 'placeholder-private-canary');
  const server = createServer(socket => socket.end());
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  try {
    const port = server.address().port;
    const control = createConnection(port, '127.0.0.1'); await once(control, 'connect'); control.end();
    const prepared = await f.prepareCommand(`node -e 'const fs=require("fs"),net=require("net");
      try { fs.writeFileSync(${JSON.stringify(outside)}, "wrong"); process.exit(11); } catch {}
      try { if(fs.readFileSync(".env.placeholder","utf8").includes("placeholder-private-canary")) process.exit(12); } catch {}
      const s=net.connect(${port},"127.0.0.1"); s.on("connect",()=>process.exit(13)); s.on("error",()=>console.log("sandbox-ok"));'`);
    const result = await f.command(prepared.command, io(), 10000, prepared.mounts);
    assert.equal(result.code, 0, result.output); assert(result.output.includes('sandbox-ok'));
    await assert.rejects(readFile(outside));
    const echo = await f.command('echo sandbox-ok', io(), 10000);
    assert.equal(echo.code, 0, echo.output); assert(echo.output.includes('sandbox-ok'));
  } finally { await new Promise(resolve => server.close(resolve)); }
});

test('missing sandbox runs approved commands with restricted environment', async () => {
  const f = await folder();
  f.commandBackend = async () => 'host';
  for (const answer of ['n', 'yes', '', null]) {
    const result = await f.tool({ name: 'run_command', arguments: { command: 'echo wrong > marker' } }, io(answer));
    assert.equal(result.ran, false); await assert.rejects(readFile(join(f.root, 'marker')));
  }
  const result = await f.tool({ name: 'run_command', arguments: { command: 'echo approved > marker' } }, io());
  assert.equal(result.code, 0); assert.equal((await readFile(join(f.root, 'marker'), 'utf8')).trim(), 'approved');
  process.env.TERVORY_PLACEHOLDER_PRIVATE = 'placeholder-private';
  try {
    const env = await f.tool({ name: 'run_command', arguments: { command: `"${process.execPath}" -e "console.log(process.env.TERVORY_PLACEHOLDER_PRIVATE ?? 'absent')"` } }, io());
    assert.equal(env.output.trim(), 'absent');
  } finally { delete process.env.TERVORY_PLACEHOLDER_PRIVATE; }
});

test('Darwin falls back after approval when Seatbelt cannot be built', { skip: process.platform !== 'darwin' }, async () => {
  const f = await folder(); const read = fsPromises.readFile;
  try {
    fsPromises.readFile = async (path, ...args) => path instanceof URL && path.pathname.endsWith('/seatbelt-base.sbpl') ? '(version 1) (placeholder-invalid-operation)' : read(path, ...args);
    syncBuiltinESMExports();
    const result = await f.tool({ name: 'run_command', arguments: { command: 'echo approved > placeholder-marker' } }, io());
    assert.equal(result.code, 0);
    assert.equal((await readFile(join(f.root, 'placeholder-marker'), 'utf8')).trim(), 'approved');
  } finally { fsPromises.readFile = read; syncBuiltinESMExports(); }
});

test('undo restores the exact UTF-8 bytes, including a byte-order mark', async () => {
  const f = await folder(); const original = Buffer.from('\ufeffplaceholder-old\n');
  await writeFile(join(f.root, 'file.txt'), original); await f.read('file.txt');
  await f.edit('file.txt', '--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-\ufeffplaceholder-old\n+placeholder-new\n', io());
  await f.undo(io());
  assert.deepEqual(await readFile(join(f.root, 'file.txt')), original);
});

test('Confirm without an explicit interactive true cancels without reading a yes', async () => {
  for (const interactive of [false, undefined, null, 1, 'true']) {
  const { confirmCard } = await import('../src/client.mjs');
  const requests = [];
  const result = await confirmCard({ json: async (path, body) => { requests.push({ path, body }); return { status: 'cancelled' }; } },
    { card_id: 'placeholder-card', summary: 'placeholder summary' },
    { interactive, write() {}, prompt: async () => { throw Error('must not prompt'); } });
  assert.equal(result.status, 'cancelled');
  assert.deepEqual(requests, [{ path: '/v1/command/cards/placeholder-card', body: { action: 'cancel' } }]);
  }
});


test('hard-linked files cannot bypass read refusal through Git diff', async () => {
  const f = await folder(); const outside = join(work, 'placeholder-hardlink');
  await writeFile(outside, 'placeholder private contents');
  await writeFile(join(f.root, 'x'), 'placeholder tracked contents\n');
  execFileSync('git', ['add', 'x'], { cwd: f.root });
  await fsPromises.unlink(join(f.root, 'x'));
  await fsPromises.link(outside, join(f.root, 'x'));
  await assert.rejects(f.read('x'), /Code tool refused/);
  assert(!(await f.files()).files.includes('x'));
  assert(!(await f.diff()).output.includes('placeholder private contents'));
});

test('non-Linux read rechecks the opened file identity after reading', async () => {
  const f = await folder(); const original = fsPromises.open;
  const descriptor = Object.getOwnPropertyDescriptor(process, 'platform');
  try {
    Object.defineProperty(process, 'platform', { value: 'darwin' });
    fsPromises.open = async (...args) => {
      const handle = await original(...args);
      const read = handle.read.bind(handle);
      handle.read = async (...readArgs) => {
        const result = await read(...readArgs);
        await fsPromises.rename(join(f.root, 'file.txt'), join(f.root, 'placeholder-old'));
        await writeFile(join(f.root, 'file.txt'), 'old\n');
        return result;
      };
      return handle;
    };
    syncBuiltinESMExports();
    await assert.rejects(f.raw('file.txt'), /Code tool refused/);
  } finally { fsPromises.open = original; syncBuiltinESMExports(); Object.defineProperty(process, 'platform', descriptor); }
});

test('rename identity mismatch refuses and still rolls back independent files', async () => {
  const { f, diff, names } = await changeSet(); const rename = f.renameFile.bind(f); let calls = 0;
  f.renameFile = async (source, target) => {
    await rename(source, target);
    if (++calls === 2) {
      // Keep the displaced inode allocated: ext4 may immediately reuse an unlinked inode.
      await fsPromises.rename(target, target + '.placeholder-displaced');
      await writeFile(target, 'placeholder concurrent replacement\n');
    }
  };
  await assert.rejects(f.edit(null, diff, io()), /Code tool refused/);
  assert.equal(await readFile(join(f.root, names[0]), 'utf8'), 'old\n');
  assert.equal(await readFile(join(f.root, names[1]), 'utf8'), 'placeholder concurrent replacement\n');
  assert.equal(await readFile(join(f.root, names[2]), 'utf8'), 'old\n');
});

test('parent swap during rename cannot write through the replacement symlink', async () => {
  const f = await folder(); const parent = join(f.root, 'placeholder-parent');
  const outside = await mkdtemp(join(work, 'placeholder-parent-outside-'));
  await mkdir(parent); await writeFile(join(parent, 'file.txt'), 'old\n');
  await writeFile(join(outside, 'file.txt'), 'placeholder outside\n'); await f.read('placeholder-parent/file.txt');
  const rename = f.renameFile.bind(f);
  f.renameFile = async (source, target) => {
    await fsPromises.rename(parent, parent + '-moved');
    await symlink(outside, parent);
    await rename(source.replace('placeholder-parent/', 'placeholder-parent-moved/'), target.replace('placeholder-parent/', 'placeholder-parent-moved/'));
  };
  await assert.rejects(f.edit('placeholder-parent/file.txt', patch.replaceAll('a/file.txt', 'a/placeholder-parent/file.txt').replaceAll('b/file.txt', 'b/placeholder-parent/file.txt'), io()), /Code tool refused/);
  assert.equal(await readFile(join(outside, 'file.txt'), 'utf8'), 'placeholder outside\n');
});

test('sandbox denies home, secret directories, git config, outside reads and Unix sockets', { skip: process.platform === 'win32' && 'Sandbox confinement applies where the OS sandbox exists' }, async () => {
  const f = await folder(); const home = homedir();
  await mkdir(join(home, '.ssh'), { recursive: true });
  const directories = [home, join(home, '.ssh')];
  if (process.platform === 'darwin') { await mkdir(join(home, 'Library/Keychains'), { recursive: true }); directories.push(join(home, 'Library/Keychains')); }
  for (const name of ['.ssh', 'keychains', 'keyrings']) {
    await mkdir(join(f.root, name)); await writeFile(join(f.root, name, 'placeholder'), 'placeholder private');
    await assert.rejects(f.read(name + '/placeholder'));
  }
  const outside = join(work, 'placeholder-outside-read'); await writeFile(outside, 'placeholder outside');
  // Place the socket inside the allowed folder: even filesystem-reachable Unix sockets must be denied.
  const socketPath = join(f.root, 'placeholder.sock');
  const server = createServer(socket => socket.end()); server.listen(socketPath); await once(server, 'listening');
  try {
    const control = createConnection(socketPath); await once(control, 'connect'); control.end();
    const source = `const fs=require("fs"),net=require("net");
      for(const p of ${JSON.stringify(directories)}) { try { fs.readdirSync(p); process.exit(21); } catch {} }
      for(const p of ${JSON.stringify(['.git/config', outside, '.ssh/placeholder', 'keychains/placeholder', 'keyrings/placeholder'])}) {
        try { if(fs.readFileSync(p).length) process.exit(22); } catch {}
      }
      const s=net.connect("placeholder.sock"); s.on("connect",()=>process.exit(23)); s.on("error",()=>console.log("placeholder-confined"));`;
    const prepared = await f.prepareCommand('node -e ' + "'" + source + "'");
    const result = await f.command(prepared.command, io(), 10000, prepared.mounts);
    assert.equal(result.code, 0, result.output); assert(result.output.includes('placeholder-confined'));
  } finally { await new Promise(resolve => server.close(resolve)); }
});

test('Darwin denies hard links to an outside file', { skip: process.platform !== 'darwin' }, async () => {
  const f = await folder(); const outside = join(work, 'placeholder-darwin-link');
  await writeFile(outside, 'placeholder outside');
  const result = await f.command(`ln '${outside}' ./x`, io(), 10000);
  assert.notEqual(result.code, 0, result.output); await assert.rejects(readFile(join(f.root, 'x')));
});

test('Darwin kills background descendants when the command exits', { skip: process.platform !== 'darwin' }, async () => {
  const f = await folder();
  for (const redirect of ['', '>/dev/null 2>&1']) {
    const result = await f.command(`(sleep 2; touch marker) ${redirect} &`, io(), 10000);
    assert.equal(result.code, 0, result.output);
    await new Promise(resolve => setTimeout(resolve, 2300));
    await assert.rejects(readFile(join(f.root, 'marker')));
  }
});

test('AWS assignments and complete or truncated PEM blocks are redacted', async () => {
  const begin = '-----BEGIN ' + 'PRIVATE KEY-----', end = '-----END ' + 'PRIVATE KEY-----';
  const placeholder = `${begin}\nplaceholder-private-body\n${end}`;
  assert.equal(redactSecrets(`safe\n${placeholder}\nafter`), 'safe\n[redacted]\nafter');
  assert.equal(redactSecrets(`${begin}\nplaceholder-private-body`), '[redacted]');
  assert.equal(redactSecrets('aws_secret_access_key=placeholder-long-value'), '[redacted]');
  assert.equal(redactSecrets(JSON.stringify({ aws_secret_access_key: 'placeholder-long-value' })), '[redacted]');
  const f = await folder();
  const source = `process.stdout.write(${JSON.stringify(begin + '\nplaceholder-private-body\n' + end + '\n')})`;
  const result = await processOutput(process.execPath, ['-e', source], { cwd: f.root });
  assert.equal(result.output, '[redacted]\n');
});

test('approved commands execute on Windows and every host fallback', async () => {
  const f = await folder(); f.commandBackend = async () => 'host';
  const result = await f.tool({ name: 'run_command', arguments: { command: 'echo command-ok' } }, io());
  assert.equal(result.ran, true); assert.equal(result.code, 0); assert.equal(result.output.trim(), 'command-ok');
  const large = await f.tool({ name: 'run_command', arguments: { command: `"${process.execPath}" -e "console.log('x'.repeat(70000))"` } }, io());
  assert(large.truncated); assert(Buffer.byteLength(large.output) <= LIMITS.outputBytes);
  const timeout = await f.tool({ name: 'run_command', arguments: { command: `"${process.execPath}" -e "setTimeout(()=>{},10000)"` } }, io(), { timeoutMs: 100 });
  assert(timeout.timed_out);
});
