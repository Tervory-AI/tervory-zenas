// Render the real public pages with their data boundary replaced by fixture data.
const fs = require('node:fs');
const path = require('node:path');
const {createRequire} = require('node:module');
const root = path.resolve(__dirname, '../..');
const deps = createRequire(path.join(root, 'workspace/package.json'));
const ts = deps('typescript');
const React = deps('react');
const {renderToStaticMarkup} = deps('react-dom/server');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const captures = [];
function load(file) {
  const filename = path.join(root, 'workspace/app/moot', file);
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022,
    esModuleInterop: true,
  }}).outputText;
  const mod = {exports: {}};
  function requireLocal(name) {
    if (name.endsWith('.css')) return {};
    if (name === 'react' && input.page === 'panel') {
      const state = input.result.hearing;
      const values = [[], [], [], true, true, state.id, '', {}, state, null, '', false, false, 'question'];
      let index = 0;
      return {...React, useState: () => [values[index++], () => {}], useEffect() {},
        useCallback: fn => fn, useRef: value => ({current: value})};
    }
    if (file.startsWith('../zenas/') && name.startsWith('./')) return load('../zenas/' + name.slice(2) + '.ts');
    if (name === 'next/link') return ({href, children}) => React.createElement('a', {href}, children);
    if (name === 'next/navigation') return {notFound() {throw Error('404');}};
    if (name.endsWith('/WatchPanel')) return load('WatchPanel.tsx');
    if (name.endsWith('/store')) return load('store.ts');
    if (name === 'server-only') return {};
    if (name === '@/lib/tank') return {async mootPublishedMatches(id, _, region) {
      captures.push(id ? [id, region] : region);
      if (input.result.status === 'unavailable') throw Error('offline unavailable');
      if (input.result.status === 'malformed') return {matches: 'invalid response'};
      return {matches: input.result.matches ?? (input.result.match ? [input.result.match] : [])};
    }};
    return deps(name);
  }
  new Function('require', 'module', 'exports', compiled)(requireLocal, mod, mod.exports);
  return mod.exports;
}
(async () => {
  try {
    if (input.page === 'form') {
      const data = new FormData();
      for (const [key, value] of Object.entries(input.result.fields)) data.set(key, value);
      const payload = load('../zenas/moot-hearing-types.ts').turnFromForm(data, input.result.hearing, input.result.seat);
      process.stdout.write(JSON.stringify(payload));
      return;
    }
    const page = input.page === 'panel' ? '../zenas/moot-court.tsx' : input.page === 'index' ? 'page.tsx' : '[match]/page.tsx';
    const component = load(page);
    const node = input.page === 'panel' ? component.MootCourt() : await component.default({params: Promise.resolve({match: input.result.match?.matchId ?? 'b88613ae-7f0a-49e7-bdb5-28620f283041'}),
      searchParams: Promise.resolve({region: input.region})});
    process.stdout.write(JSON.stringify({html: renderToStaticMarkup(node), captures}));
  } catch (error) {
    if (error.message !== '404') throw error;
    process.stdout.write(JSON.stringify({status: 404, captures}));
  }
})();
