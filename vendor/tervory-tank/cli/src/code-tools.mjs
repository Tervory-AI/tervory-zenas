import { seatbeltProfile } from './seatbelt.mjs';
import { constants } from 'node:fs';
import { lstat, stat, access, realpath, readlink, open, readdir, readFile, writeFile, rename, unlink, mkdtemp, rm } from 'node:fs/promises';
import { resolve, relative, isAbsolute, sep, dirname, basename, delimiter as pathDelimiter } from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { Worker } from 'node:worker_threads';

export const LIMITS = Object.freeze({ files: 2000, fileBytes: 200_000, matches: 200, outputBytes: 65536, commandMs: 120_000 });
// Kept in parity with tests/test_secrets.py _LIVE / _ASSIGNED. Do not exempt
// placeholders here: local source sent to a model gets the stricter treatment.
const LIVE = [
  /-----BEGIN (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY-----/,
  /\bAKIA[0-9A-Z]{16}\b/, /\bghp_[A-Za-z0-9]{20,}\b/, /\bgithub_pat_[A-Za-z0-9_]{20,}\b/,
  /\bxox[baprs]-[A-Za-z0-9-]{10,}/, /\bsk-ant-[A-Za-z0-9_\-]{10,}/,
  /(?<![A-Za-z0-9_])sk-[A-Za-z0-9]{20,}/,
  /\b(?:sk|pk|rk)_live_[A-Za-z0-9]{8,}/, /\bAIza[0-9A-Za-z0-9_\-]{30,}/,
  /\beyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}/,
  /(?:^|[^A-Za-z0-9])(?:aws_secret_access_key|api[_-]?key|secret[_-]?key|client[_-]?secret|password|passwd|access[_-]?token|auth[_-]?token)['"]?\s*[:=]\s*['"]?[^\s'"#]{12,}/i,
  /tv[rd]?1\.(?:us-east|za)\.[A-Za-z0-9_.-]+/,
];
export function redactSecrets(value) {
  return String(value).replace(/-----BEGIN ((?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY)-----[\s\S]*?(?:-----END \1-----|$)/g, '[redacted]').split('\n').map(line => LIVE.some(pattern => pattern.test(line)) ? '[redacted]' : line).join('\n');
}
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const refused = () => new Error('Code tool refused.');
const inside = (root, path) => { const rel = relative(root, path); return rel !== '..' && !rel.startsWith('..' + sep) && !isAbsolute(rel); };
export function secretPath(path) {
  return path.split(/[\\/]/).some(part => ['.git', '.ssh', 'keychains', 'keyrings'].includes(part.toLowerCase()) || /^\.env/i.test(part) || /^id_/i.test(part) || /\.(pem|key)$/i.test(part));
}

// The process output is collected, not streamed: redaction must see complete lines
// even when a secret arrives split across stdout/stderr chunks.
export async function processOutput(command, args, { cwd, gitDirectory, gitWorkTree, timeoutMs = LIMITS.commandMs, signal, input, outputBytes = LIMITS.outputBytes, delimiter = '\n', temporaryDirectory, windowsVerbatimArguments = false } = {}) {
  return new Promise((resolveResult, reject) => {
    let child; let timer; let killed = false; let size = 0; let total = 0; const chunks = [];
    const stop = () => {
      killed = true;
      try { if (process.platform !== 'win32') process.kill(-child.pid, 'SIGKILL'); else {
        // taskkill owns the whole Windows process tree, including shell children.
        const killer = spawn(resolve(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'taskkill.exe'), ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
        killer.on('error', () => child.kill('SIGKILL'));
      } } catch { /* already ended */ }
    };
    if (signal?.aborted) return reject(refused());
    try {
      child = spawn(command, args, { cwd, shell: false, detached: process.platform !== 'win32', windowsHide: true, windowsVerbatimArguments,
        env: { PATH: process.env.PATH, ...(process.platform === 'win32' ? { SystemRoot: process.env.SystemRoot } : {}), LANG: 'C.UTF-8', GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: process.platform === 'win32' ? 'NUL' : '/dev/null', GIT_LITERAL_PATHSPECS: '1',
          ...(temporaryDirectory ? { TMPDIR: temporaryDirectory, ...(process.platform === 'win32' ? { TEMP: temporaryDirectory, TMP: temporaryDirectory } : {}) } : {}),
          ...(gitDirectory ? { GIT_DIR: gitDirectory, GIT_WORK_TREE: gitWorkTree } : {}) },
        stdio: [input === undefined ? 'ignore' : 'pipe', 'pipe', 'pipe'] });
    } catch { return reject(refused()); }
    if (input !== undefined) { child.stdin.on('error', () => {}); child.stdin.end(input); }
    const collect = chunk => { total += chunk.length; const keep = chunk.subarray(0, Math.max(0, outputBytes - size)); size += keep.length; if (keep.length) chunks.push(Buffer.from(keep)); };
    child.stdout.on('data', collect); child.stderr.on('data', collect);
    child.on('error', () => { clearTimeout(timer); signal?.removeEventListener('abort', stop); reject(refused()); });
    timer = setTimeout(stop, timeoutMs); signal?.addEventListener('abort', stop, { once: true });
    const killGroup = () => { if (process.platform !== 'win32') try { process.kill(-child.pid, 'SIGKILL'); } catch { /* already ended */ } };
    // Kill at exit too: descendants may still hold stdout/stderr open.
    child.on('exit', killGroup);
    child.on('close', code => {
      killGroup();
      clearTimeout(timer); signal?.removeEventListener('abort', stop);
      // An incomplete retained last line is omitted, so a secret whose suffix is
      // beyond the cap cannot leak its prefix.
      let output = Buffer.concat(chunks).toString('utf8');
      if (total > size) output = output.slice(0, output.lastIndexOf(delimiter) + 1);
      resolveResult({ code, timed_out: killed, truncated: total > size, output: redactSecrets(output) });
    });
  });
}

export class CodeFolder {
  #reads = new Map();
  #undo = [];
  static async create(folder = '.') {
    const root = await realpath(resolve(folder));
    if (!(await lstat(root)).isDirectory()) throw refused();
    const tool = new CodeFolder(root);
    const check = await tool.git(['rev-parse', '--absolute-git-dir']);
    if (check.code !== 0) throw new Error('Code mode requires a git repository.');
    const tree = await tool.git(['rev-parse', '--show-toplevel']);
    if (tree.code !== 0) throw new Error('Code mode requires a git repository.');
    tool.gitDirectory = await realpath(check.output.trim());
    tool.gitWorkTree = await realpath(tree.output.trim());
    return tool;
  }
  constructor(root) { this.root = root; }
  async git(args, input) {
    const environment = { cwd: this.root, gitDirectory: this.gitDirectory, gitWorkTree: this.gitWorkTree ?? this.root };
    const options = ['--literal-pathspecs', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-c', 'core.attributesFile=/dev/null', '--no-pager'];
    if (['status', 'diff'].includes(args[0])) {
      // Git can invoke clean/process filters even with --no-ext-diff and
      // --no-textconv. Disable driver execution: read tools do not approve a
      // repository-configured shell command. Ask for names only, never values.
      const drivers = await processOutput('git', [...options, 'config', '--name-only', '--get-regexp', '^filter\\..*\\.(clean|process|required)$'], environment);
      if (drivers.truncated || ![0, 1].includes(drivers.code)) throw refused();
      for (const key of drivers.output.trim().split('\n').filter(Boolean)) {
        if (!/^filter\.[A-Za-z0-9_.-]+\.(clean|process|required)$/.test(key)) throw refused();
        options.push('-c', key + (key.endsWith('.required') ? '=false' : '='));
      }
    }
    return processOutput('git', [...options, ...args], { ...environment, input, outputBytes: args[0] === 'ls-files' ? 8_388_608 : LIMITS.outputBytes, delimiter: args.includes('-z') ? '\0' : '\n' });
  }
  async path(name, { missing = false } = {}) {
    if (typeof name !== 'string' || !name || /[\x00-\x1f\x7f]/.test(name) || name.includes('\\')) throw refused();
    const path = resolve(this.root, name);
    if (!inside(this.root, path) || secretPath(relative(this.root, path))) throw refused();
    // Follow every ancestor, even when the final file does not exist yet.
    const parent = await realpath(dirname(path));
    if (!inside(this.root, parent) || secretPath(relative(this.root, resolve(parent, basename(path))))) throw refused();
    try {
      const canonical = await realpath(path);
      if (!inside(this.root, canonical) || secretPath(relative(this.root, canonical))) throw refused();
      if ((await lstat(path)).isSymbolicLink()) throw refused();
    } catch (error) { if (!missing || error.code !== 'ENOENT') throw error; }
    return path;
  }
  async files() {
    // Git supplies ignore semantics, including nested .gitignore and info/exclude.
    // Output is bounded; return only complete NUL-delimited names if truncated.
    const result = await this.git(['ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', '.']);
    if (result.code !== 0) throw refused();
    const names = result.output.split('\0'); names.pop();
    // check-ignore rejects Git's global literal mode; ls-files supplies the
    // same ignored tracked names without parsing user-controlled pathspecs.
    const ignored = await this.git(['ls-files', '-z', '--cached', '--ignored', '--exclude-standard', '--', '.']);
    if (![0, 1].includes(ignored.code) || ignored.truncated) throw refused();
    const excluded = new Set(ignored.output.split('\0'));
    const files = [];
    for (const name of names) {
      try {
        const path = await this.path(name);
        const info = await lstat(path);
        if (!excluded.has(name) && info.isFile() && info.nlink === 1) files.push(name);
      } catch { /* ignored, secret, symlink or vanished */ }
      if (files.length === LIMITS.files) break;
    }
    return { files, truncated: result.truncated || names.length > files.length && files.length === LIMITS.files };
  }
  async raw(name, { binary = false, maxBytes = LIMITS.fileBytes } = {}) {
    const path = await this.path(name, { missing: true });
    let handle;
    try {
      handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0) | (constants.O_NONBLOCK ?? 0));
      const openedStat = await handle.stat();
      if (openedStat.nlink > 1) throw refused();
      if (!openedStat.isFile() || openedStat.size > maxBytes) throw refused();
      // On Linux verify the opened descriptor before reading, closing ancestor
      // symlink-swap races; other platforms recheck the canonical path below.
      if (process.platform === 'linux') {
        const canonical = await realpath(`/proc/self/fd/${handle.fd}`);
        if (!inside(this.root, canonical) || secretPath(relative(this.root, canonical))) throw refused();
      }
      const bytes = Buffer.alloc(maxBytes + 1); const { bytesRead } = await handle.read(bytes, 0, bytes.length, 0);
      if (bytesRead > maxBytes) throw refused();
      const raw = bytes.subarray(0, bytesRead); const text = binary ? null : new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw);
      if (!binary && text.includes('\0')) throw refused();
      if (process.platform !== 'linux') {
        const c = await realpath(path); const s2 = await stat(c);
        if (!inside(this.root, c) || secretPath(relative(this.root, c)) || s2.dev !== openedStat.dev || s2.ino !== openedStat.ino) throw refused();
      }
      await this.path(name);
      return { bytes: raw, text };
    } catch (error) {
      if (error.code === 'ENOENT' && !handle) return null;
      throw refused();
    } finally { await handle?.close(); }
  }
  async read(name) {
    const path = await this.path(name, { missing: true });
    const value = await this.raw(name);
    this.#reads.set(path, value === null ? null : digest(value.bytes));
    return value === null ? { exists: false, text: '', size: 0 }
      : { exists: true, text: redactSecrets(value.text), size: value.bytes.length };
  }
  async search(pattern, regex = false) {
    if (typeof pattern !== 'string' || pattern.length > 500 || !pattern) throw refused();
    const files = (await this.files()).files; const matches = [];
    // Execute user/model regexes off the main thread with a hard bound; a
    // catastrophic expression cannot pin the interactive CLI.
    const worker = new Worker(`const {parentPort,workerData}=require('node:worker_threads');
      let re; try { re=workerData.regex?new RegExp(workerData.pattern):null; } catch { parentPort.postMessage({invalid:true}); }
      parentPort.on('message', ({text})=> {const lines=text.split('\\n');const rows=[];
        for(let i=0;i<lines.length;i++) if(re?re.test(lines[i]):lines[i].includes(workerData.pattern)) {
          rows.push({line:i+1,text:lines[i]});if(rows.length===200)break;}
        parentPort.postMessage({rows});});`, { eval: true, workerData: { pattern, regex } });
    try {
      // Worker boot is separate from the strict per-file regex execution budget.
      // A busy Windows runner can take longer to start a thread than to search.
      await new Promise((resolveReady, rejectReady) => {
        const cleanup = () => {
          clearTimeout(timer);
          worker.off('online', ready);
          worker.off('error', fail);
          worker.off('exit', fail);
        };
        const ready = () => { cleanup(); resolveReady(); };
        const fail = () => { cleanup(); rejectReady(refused()); };
        const timer = setTimeout(fail, 5000);
        worker.once('online', ready);
        worker.once('error', fail);
        worker.once('exit', fail);
      });
      for (const file of files) {
        let text; try { text = (await this.read(file)).text; } catch { continue; }
        const result = await new Promise((resolveResult, reject) => {
          const timer = setTimeout(() => reject(refused()), 1000);
          const done = value => { clearTimeout(timer); worker.off('error', fail); resolveResult(value); };
          const fail = () => { clearTimeout(timer); worker.off('message', done); reject(refused()); };
          worker.once('message', done); worker.once('error', fail); worker.postMessage({ text });
        });
        if (result.invalid) throw refused();
        matches.push(...result.rows.slice(0, LIMITS.matches - matches.length).map(row => ({ file, ...row })));
        if (matches.length >= LIMITS.matches) break;
      }
      return { matches, truncated: matches.length === LIMITS.matches };
    } finally { await worker.terminate(); }
  }
  async diff() {
    const { files } = await this.files();
    if (!files.length) return { output: '' };
    return this.git(['diff', '--no-ext-diff', '--no-textconv', '--ignore-submodules', '--', ...files]);
  }
  async verify(name, expected) {
    const value = await this.raw(name);
    if ((value === null ? null : value.text) !== expected) throw refused();
  }
  async renameFile(source, target) { await rename(source, target); }
  async writeSet(entries) {
    const temporary = []; const renamed = [];
    const checkParent = async item => {
      if (await realpath(dirname(item.path)) !== item.parent
          || await realpath(item.parent) !== item.parent
          || !inside(this.root, item.parent) || secretPath(relative(this.root, item.target))) throw refused();
    };
    const sameFile = (a, b) => a.dev === b.dev && a.ino === b.ino && a.isFile() && a.nlink === 1;
    try {
      for (const entry of entries) {
        await this.verify(entry.name, entry.before);
        const path = await this.path(entry.name, { missing: true });
        const parent = await realpath(dirname(path));
        const item = { path, parent, target: resolve(parent, basename(path)), entry };
        temporary.push(item);
        await checkParent(item);
        if (entry.after !== null) {
          item.temp = resolve(parent, `.tervory-${randomUUID()}`);
          await writeFile(item.temp, entry.after, { flag: 'wx', mode: entry.mode });
          item.identity = await lstat(item.temp);
        } else item.identity = await lstat(item.target);
      }
      // All validation and temporary writes finish before the first rename.
      for (const entry of entries) await this.verify(entry.name, entry.before);
      for (const item of temporary) {
        await this.verify(item.entry.name, item.entry.before);
        await checkParent(item);
        if (!sameFile(await lstat(item.temp ?? item.target), item.identity)) throw refused();
        if (item.temp) await this.renameFile(item.temp, item.target);
        else await unlink(item.target);
        renamed.push(item);
        await checkParent(item);
        if (item.temp && !sameFile(await lstat(item.target), item.identity)) throw refused();
        if (!item.temp) {
          try { await lstat(item.target); throw refused(); } catch (error) { if (error.code !== 'ENOENT') throw error; }
        }
      }
    } catch {
      // Keep restoring independent files even if another rollback is unsafe.
      for (const item of renamed.reverse()) {
        try {
          await checkParent(item);
          await this.verify(item.entry.name, item.entry.after);
          if (item.temp && !sameFile(await lstat(item.target), item.identity)) throw refused();
          if (item.entry.before === null) await unlink(item.target);
          else {
            const temp = resolve(item.parent, `.tervory-${randomUUID()}`);
            try {
              await writeFile(temp, item.entry.before, { flag: 'wx', mode: item.entry.mode });
              const identity = await lstat(temp);
              await checkParent(item);
              await this.renameFile(temp, item.target);
              await checkParent(item);
              if (!sameFile(await lstat(item.target), identity)) throw refused();
            } finally { await unlink(temp).catch(() => {}); }
          }
        } catch { /* unsafe or failed restoration must not skip other items */ }
      }
      throw refused();
    } finally {
      for (const item of temporary) if (item.temp) {
        try { await checkParent(item); await unlink(item.temp); } catch { /* vanished or parent moved */ }
      }
    }
    for (const entry of entries) {
      const path = await this.path(entry.name, { missing: true });
      this.#reads.set(path, entry.after === null ? null : digest(Buffer.from(entry.after)));
    }
  }
  async edit(name, patch, io) {
    if (typeof patch !== 'string' || Buffer.byteLength(patch) > LIMITS.fileBytes || redactSecrets(patch) !== patch) throw refused();
    const files = splitDiff(patch);
    if (name !== undefined && name !== null && name !== files[0].name) throw refused();
    const entries = []; const targets = new Set();
    for (const file of files) {
      const path = await this.path(file.name, { missing: true });
      const canonical = resolve(await realpath(dirname(path)), basename(path));
      if (targets.has(canonical) || !this.#reads.has(path)) throw refused();
      targets.add(canonical);
      const value = await this.raw(file.name);
      const expected = this.#reads.get(path);
      if ((value === null ? null : digest(value.bytes)) !== expected || (value && redactSecrets(value.text) !== value.text)) throw refused();
      const before = value?.text ?? null;
      const after = await applyDiff(before ?? '', file.patch, file.name, before === null, this.root);
      if (Buffer.byteLength(after) > LIMITS.fileBytes || redactSecrets(after) !== after) throw refused();
      entries.push({ name: file.name, before, after, mode: before === null ? 0o600 : (await lstat(path)).mode & 0o777 });
    }
    io.write(patch + '\n');
    if (io.interactive !== true || (await io.prompt('Apply [y/N] '))?.trim().toLowerCase() !== 'y') return { applied: false };
    await this.writeSet(entries);
    this.#undo.push(entries);
    if (this.#undo.length > 10) this.#undo.shift();
    return { applied: true };
  }
  async undo(io) {
    const entries = this.#undo.at(-1);
    if (io.interactive !== true || !entries) throw refused();
    for (const entry of entries) await this.verify(entry.name, entry.after);
    if ((await io.prompt('Undo [y/N] '))?.trim().toLowerCase() !== 'y') return { applied: false };
    await this.writeSet(entries.map(entry => ({ ...entry, before: entry.after, after: entry.before })));
    this.#undo.pop();
    return { applied: true };
  }
  async prepareCommand(command) {
    if (await this.commandBackend() === 'host') return { command, mounts: [] };
    const match = /^(?:'([^']+)'|"([^"]+)"|([^\s;|&<>]+))(?=\s|$)/.exec(command);
    if (!match) return { command, mounts: [] };
    const executable = match[1] ?? match[2] ?? match[3];
    // These are POSIX shell builtins, even if PATH also contains a host binary.
    if (['printf', 'echo', 'true', 'false', 'pwd', 'cd', 'read', 'exit', 'test', ':', '['].includes(executable)) return { command, mounts: [] };
    let candidate;
    if (isAbsolute(executable)) candidate = executable;
    else if (!executable.includes('/')) {
      for (const directory of (process.env.PATH ?? '').split(':').filter(isAbsolute)) {
        try {
          const path = resolve(directory, executable);
          if ((await lstat(path)).isFile() || (await lstat(path)).isSymbolicLink()) { candidate = path; break; }
        } catch { /* not on this PATH entry */ }
      }
    }
    if (!candidate) return { command, mounts: [] };
    const executableFile = async path => {
      const canonical = await realpath(path);
      if (!(await stat(path)).isFile() || !(await stat(canonical)).isFile()) throw refused();
      await access(path, constants.X_OK); await access(canonical, constants.X_OK);
      return canonical;
    };
    const resolved = await executableFile(candidate);
    if (inside('/usr', candidate) || inside('/bin', candidate) || inside(this.root, candidate)) return { command, mounts: [] };
    if (!/^(node|python(?:3(?:\.\d+)?)?|pytest)$/.test(basename(candidate))) throw refused();
    const mounts = []; const runners = []; const bound = new Set();
    const safeHostPath = path => {
      if (path === '/' || inside(path, process.env.HOME ?? '/') || secretPath(path)
          || path.split(sep).some(part => ['.config', '.ssh', 'keyrings', 'keychains'].includes(part.toLowerCase()))) throw refused();
    };
    const bind = async (source, target = source) => {
      safeHostPath(source); safeHostPath(target);
      if (inside('/usr', target) || inside(this.root, target) || bound.has(target)) return;
      bound.add(target); mounts.push('--ro-bind', source, target); runners.push(target);
    };
    const pythonInstall = async executable => {
      const canonical = await executableFile(executable);
      const prefix = dirname(dirname(executable));
      // A bin in a general user-data directory is not a Python installation.
      // In particular never infer ~/.local from a pip --user / uv launcher.
      if (basename(dirname(executable)) !== 'bin' || basename(prefix) === '.local') throw refused();
      if (inside('/usr', executable)) return;
      const version = /^python(3\.\d+)$/.exec(basename(canonical))?.[1];
      const libraries = (await readdir(resolve(prefix, 'lib'))).filter(name => /^python3\.\d+$/.test(name) && (!version || name === 'python' + version));
      if (libraries.length !== 1) throw refused();
      const library = resolve(prefix, 'lib', libraries[0]);
      const actualLibrary = await realpath(library);
      if (!inside(prefix, actualLibrary) || basename(actualLibrary) !== libraries[0] || !(await stat(actualLibrary)).isDirectory()) throw refused();
      const config = resolve(prefix, 'pyvenv.cfg');
      let venv = false;
      try { venv = (await lstat(config)).isFile(); } catch { /* base interpreter */ }
      if (venv) await bind(config);
      else if (!(await stat(resolve(library, 'os.py'))).isFile()) throw refused();
      await bind(actualLibrary, library);
      // Standalone Python may dynamically link its own libpython. Bind matching
      // regular library files, never the containing lib or installation parent.
      for (const name of await readdir(resolve(prefix, 'lib'))) {
        if (/^libpython3\.\d+(?:\.so(?:\.\d+)*|\.dylib)$/.test(name)) {
          const path = resolve(prefix, 'lib', name); const actual = await realpath(path);
          if (!inside(prefix, actual) || !(await stat(actual)).isFile()) throw refused();
          await bind(actual, path);
        }
      }
    };
    const executables = [candidate];
    if (basename(candidate) === 'pytest') {
      // Refuse broad user installs before reading even the launcher shebang.
      if (basename(dirname(candidate)) !== 'bin' || basename(dirname(dirname(candidate))) === '.local') throw refused();
      const handle = await open(candidate, constants.O_RDONLY);
      try {
        const bytes = Buffer.alloc(1024); const { bytesRead } = await handle.read(bytes, 0, bytes.length, 0);
        const interpreter = /^#!(\/[^\s]+)\s*$/.exec(bytes.subarray(0, bytesRead).toString('utf8').split('\n')[0])?.[1];
        if (!interpreter || !/^python(?:3(?:\.\d+)?)?$/.test(basename(interpreter))) throw refused();
        executables.push(interpreter);
      } finally { await handle.close(); }
    }
    for (const executable of executables) {
      const target = await executableFile(executable);
      if (basename(executable) !== 'node' && basename(executable) !== 'pytest') {
        await pythonInstall(executable);
        if (target !== executable && !inside('/usr', target)) await pythonInstall(target);
      }
      await bind(target);
      let link = executable; const seen = new Set();
      while ((await lstat(link)).isSymbolicLink()) {
        if (seen.has(link)) throw refused(); seen.add(link); safeHostPath(link);
        if (!inside('/usr', link) && !inside(this.root, link) && !bound.has(link)) {
          bound.add(link); mounts.push('--symlink', target, link);
        }
        link = resolve(dirname(link), await readlink(link));
      }
    }
    // Mask secret paths within the specific Python library trees as before.
    const scan = async directory => {
      for (const entry of await readdir(directory, { withFileTypes: true })) {
        const path = resolve(directory, entry.name);
        if (secretPath(entry.name) || ['.config', '.ssh', 'keychains', 'keyrings'].includes(entry.name.toLowerCase())) {
          mounts.push(...(entry.isDirectory() ? ['--tmpfs', path] : ['--ro-bind', '/dev/null', path]));
        } else if (entry.isDirectory()) await scan(path);
      }
    };
    for (const path of runners) if ((await stat(path)).isDirectory()) await scan(path);
    const quoted = "'" + candidate.replaceAll("'", "'\"'\"'") + "'";
    return { command: quoted + command.slice(match[0].length), mounts, runners };
  }
  #backend;
  async commandBackend() {
    return this.#backend ??= this.detectCommandBackend();
  }
  async detectCommandBackend() {
    if (process.platform === 'darwin') {
      try {
        const probe = await processOutput('/usr/bin/sandbox-exec', ['-p', await seatbeltProfile(this.root, []), '/usr/bin/true'], { cwd: this.root, timeoutMs: 2000 });
        return probe.code === 0 && !probe.timed_out ? 'seatbelt' : 'host';
      } catch { return 'host'; }
    }
    if (process.platform === 'linux') {
      for (const directory of (process.env.PATH ?? '').split(pathDelimiter)) {
        if (!isAbsolute(directory)) continue;
        try {
          const probe = await processOutput(resolve(directory, 'bwrap'), ['--unshare-all', '--ro-bind', '/', '/', '/bin/true'], { cwd: this.root, timeoutMs: 2000 });
          if (probe.code === 0 && !probe.timed_out) return 'bwrap';
        } catch { /* unavailable sandbox: approval still permits commands */ }
      }
    }
    return 'host';
  }
  async command(command, io, timeoutMs = LIMITS.commandMs, mounts = []) {
    timeoutMs = Math.min(timeoutMs, LIMITS.commandMs);
    const backend = await this.commandBackend();
    if (backend === 'host') {
      const temp = await mkdtemp(resolve(this.root, '.tervory-command-'));
      try {
        const windows = process.platform === 'win32';
        const shell = windows ? resolve(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'cmd.exe') : '/bin/sh';
        // cmd.exe does not understand the \" escaping Node applies to arguments. Pass the
        // command verbatim inside one pair of quotes, as Node's own shell: true does (/s
        // strips exactly that outer pair).
        return await processOutput(shell, windows ? ['/d', '/s', '/c', `"${command}"`] : ['-c', command],
          { cwd: this.root, timeoutMs, signal: io.signal, temporaryDirectory: temp, windowsVerbatimArguments: windows });
      } finally { await rm(temp, { recursive: true, force: true }); }
    }
    if (backend === 'seatbelt') {
      await access('/usr/bin/sandbox-exec', constants.X_OK).catch(() => { throw refused(); });
      const profile = await seatbeltProfile(this.root, mounts);
      const temp = await mkdtemp(resolve(this.root, '.tervory-command-'));
      try {
        const options = { cwd: this.root, timeoutMs, signal: io.signal, temporaryDirectory: temp };
        const probe = await processOutput('/usr/bin/sandbox-exec', ['-p', profile, '/usr/bin/true'], options);
        if (probe.code !== 0 || probe.timed_out) throw refused();
        return await processOutput('/usr/bin/sandbox-exec', ['-p', profile, '/bin/sh', '-c', command], options);
      } finally { await rm(temp, { recursive: true, force: true }); }
    }
    // A confirmed command must not bypass the folder/secret-file boundary by
    // reading an absolute host path, a symlink, .env or the user's environment.
    // Use Bubblewrap when installed; host fallback still requires Run approval.
    const masks = [];
    const scan = async (directory, prefix = '') => {
      for (const entry of await readdir(directory, { withFileTypes: true })) {
        const name = prefix + entry.name;
        // Pathname Unix sockets can cross network namespaces through bind mounts.
        if (secretPath(name) || entry.isSocket()) {
          masks.push(...(entry.isDirectory() ? ['--tmpfs', '/work/' + name] : ['--ro-bind', '/dev/null', '/work/' + name]));
        } else if (entry.isDirectory()) await scan(resolve(directory, entry.name), name + '/');
      }
    };
    await scan(this.root);
    return processOutput('bwrap', ['--unshare-all', '--die-with-parent', '--new-session',
      '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib',
      '--symlink', 'usr/lib64', '/lib64', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
      ...mounts, '--bind', this.root, '/work', ...masks, '--chdir', '/work', '/bin/sh', '-c', command],
      { cwd: this.root, timeoutMs, signal: io.signal });
  }
  async tool(request, io, { timeoutMs = LIMITS.commandMs } = {}) {
    const args = request.arguments ?? {};
    switch (request.name) {
      case 'list_files': return this.files();
      case 'read_file': return this.read(args.path);
      case 'search': return this.search(args.pattern, args.regex === true);
      case 'git': return args.action === 'status' ? this.git(['status', '--short', '--ignore-submodules=all', '--', '.']) : args.action === 'diff' ? this.diff() : Promise.reject(refused());
      case 'propose_edit': return this.edit(args.path, args.diff, io);
      case 'run_command': {
        if (typeof args.command !== 'string' || !args.command || args.command.length > 8000 || /[\x00-\x1f\x7f]/.test(args.command) || redactSecrets(args.command) !== args.command) throw refused();
        const prepared = await this.prepareCommand(args.command);
        io.write(prepared.command + '\n');
        if (await this.commandBackend() === 'host') io.write('Command runs with your user permissions.\n');
        for (const root of prepared.runners ?? []) io.write(`Read-only runner: ${root}\n`);
        if (io.interactive !== true || (await io.prompt('Run [y/N] '))?.trim().toLowerCase() !== 'y') return { ran: false };
        const result = await this.command(prepared.command, io, timeoutMs, prepared.mounts);
        io.write(result.output);
        return { ran: true, ...result };
      }
      default: throw refused();
    }
  }
}

export function splitDiff(patch) {
  const lines = patch.split('\n'); const result = []; let i = 0;
  while (i < lines.length - 1) {
    const start = i;
    const old = /^--- (a\/(.+)|\/dev\/null)$/.exec(lines[i++]);
    const next = /^\+\+\+ b\/(.+)$/.exec(lines[i++] ?? '');
    if (!old || !next || (old[2] && old[2] !== next[1])) throw refused();
    const name = next[1]; let hunks = 0;
    while (i < lines.length && lines[i].startsWith('@@')) {
      const header = /^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@(?:.*)$/.exec(lines[i++]);
      if (!header) throw refused();
      let before = header[1] === undefined ? 1 : Number(header[1]);
      let after = header[2] === undefined ? 1 : Number(header[2]);
      while (before > 0 || after > 0) {
        const line = lines[i++];
        if (line === undefined) throw refused();
        if (line.startsWith(' ')) { before--; after--; }
        else if (line.startsWith('-')) before--;
        else if (line.startsWith('+')) after--;
        else if (line !== '\\ No newline at end of file') throw refused();
        if (before < 0 || after < 0) throw refused();
      }
      if (lines[i] === '\\ No newline at end of file') i++;
      hunks++;
    }
    if (!hunks) throw refused();
    result.push({ name, patch: lines.slice(start, i).join('\n') + '\n' });
    if (result.length > 20) throw refused();
  }
  if (!result.length || lines.at(-1) !== '' || i !== lines.length - 1) throw refused();
  return result;
}

// Use Git's existing unified-diff parser/applicator, not a second patch engine.
// Stage against a fixed neutral filename in a fresh scratch repo. No caller path,
// Git configuration, filter or hook can turn a patch into another file operation.
export async function applyDiff(before, patch, name, creating = false, root = process.cwd()) {
  const lines = patch.split('\n');
  if (lines[0] !== `--- ${creating ? '/dev/null' : 'a/' + name}` || lines[1] !== `+++ b/${name}`) throw refused();
  lines[0] = `--- ${creating ? '/dev/null' : 'a/target'}`; lines[1] = '+++ b/target';
  const input = lines.join('\n');
  const scratch = await mkdtemp(resolve(root, '.tervory-edit-'));
  const git = args => processOutput('git', ['--literal-pathspecs', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
    '-c', 'core.attributesFile=/dev/null', ...args], { cwd: scratch, gitDirectory: resolve(scratch, '.git'), gitWorkTree: scratch, input });
  try {
    if ((await git(['init', '--quiet'])).code !== 0) throw refused();
    if (!creating) await writeFile(resolve(scratch, 'target'), before, { flag: 'wx', mode: 0o600 });
    const parsed = await git(['apply', '--numstat', '-z', '--unidiff-zero', '--whitespace=nowarn']);
    // The original patch may describe exactly one ordinary text file. Additional
    // headers, binary patches, renames and deleted files cannot escape this check.
    if (parsed.code !== 0 || !/^\d+\t\d+\ttarget\x00$/.test(parsed.output)) throw refused();
    if ((await git(['apply', '--check', '--unidiff-zero', '--whitespace=nowarn'])).code !== 0) throw refused();
    if ((await git(['apply', '--unidiff-zero', '--whitespace=nowarn'])).code !== 0) throw refused();
    const target = resolve(scratch, 'target'); const stat = await lstat(target);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > LIMITS.fileBytes) throw refused();
    const result = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await readFile(target));
    if (result.includes('\0')) throw refused();
    return result;
  } finally { await rm(scratch, { recursive: true, force: true }); }
}
