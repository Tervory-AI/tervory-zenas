/* Load actual workspace/*.ts modules (TypeScript transpile only) into a sandbox
 * with a chosen environment and fetch. Named mocks replace Trigger sign-in-bound modules. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const ts = require(path.join(process.env.COMMAND_TEST_TOOLS, 'typescript'));

function resolveFile(base) {
  for (const candidate of [base, `${base}.ts`, `${base}.tsx`, path.join(base, 'index.ts')]) {
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
  }
  throw new Error(`Cannot resolve ${base}`);
}

exports.workspace = function workspace({ env, fetch, mocks = {}, browser }) {
  const cache = new Map();
  function load(file) {
    if (cache.has(file)) return cache.get(file).exports;
    const output = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
      fileName: file,
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    const module = { exports: {} };
    cache.set(file, module);
    const local = spec => {
      if (Object.hasOwn(mocks, spec)) return mocks[spec];
      if (spec === 'server-only') return {};
      if (spec === '@/lib/trigger-signin-fetch') return {signinFetch: fetch};
      if (spec.startsWith('node:')) return require(spec);
      if (spec.startsWith('@/')) return load(resolveFile(path.join(root, 'workspace', spec.slice(2))));
      if (spec.startsWith('.')) return load(resolveFile(path.join(path.dirname(file), spec)));
      throw new Error(`Unexpected import ${spec} from ${path.relative(root, file)}`);
    };
    vm.runInNewContext(output, {
      module, exports: module.exports, require: local, process: { env },
      URL, URLSearchParams, Request, Response, Headers, Uint8Array, TextEncoder, TextDecoder, ReadableStream,
      AbortController, AbortSignal, setTimeout, clearTimeout, console, fetch, window: browser,
    });
    return module.exports;
  }
  return relative => load(resolveFile(path.join(root, 'workspace', relative)));
};
