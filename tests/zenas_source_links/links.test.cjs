// Offline: real presenter and Ask submission/rendering; framework hooks and transport only are mocked.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const workspace = path.resolve(__dirname, '../../workspace');
const deps = createRequire(path.join(workspace, 'package.json'));
const ts = deps('typescript');
const React = deps('react');
const { renderToStaticMarkup } = deps('react-dom/server');
const citation = '8 Del. C. § 101';
const url = 'https://delcode.delaware.gov/title8/c001/sc01/index.html';

function harness(payload) {
  const cache = new Map(), states = [], refs = [], calls = [];
  let stateIndex = 0, refIndex = 0;
  const hooks = {
    ...React, useEffect() {}, useId: () => 'offline',
    useRef(initial) { const i = refIndex++; return refs[i] ??= { current: initial }; },
    useState(initial) {
      const i = stateIndex++;
      if (!(i in states)) states[i] = initial;
      return [states[i], value => { states[i] = typeof value === 'function' ? value(states[i]) : value; }];
    },
  };
  function load(filename) {
    if (cache.has(filename)) return cache.get(filename).exports;
    const module = { exports: {} };
    cache.set(filename, module);
    const js = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
      fileName: filename,
    }).outputText;
    function requireSource(spec) {
      if (spec === 'react') return hooks;
      if (spec === 'next/link') return { __esModule: true, default: ({ children, ...props }) => React.createElement('a', props, children) };
      if (['./deadlines-panel', './moot-court', './plugins-dialog', '../squad-links'].includes(spec)) {
        return { DeadlinesPanel: () => null, MootCourt: () => null, PluginsDialog: () => null, SquadLinks: () => null };
      }
      if (spec.startsWith('.')) return load(path.resolve(path.dirname(filename), spec + '.ts'));
      return deps(spec);
    }
    new Function('require', 'module', 'exports', 'fetch', js)(requireSource, module, module.exports,
      async (endpoint, options) => {
        calls.push({ endpoint, ...options });
        const { presentAnswer } = load(path.join(workspace, 'app/api/zenas/rules.ts'));
        return { json: async () => presentAnswer(payload, 'offline-matter') };
      });
    return module.exports;
  }
  const { ZenasView } = load(path.join(workspace, 'app/zenas/view.tsx'));
  function render() {
    stateIndex = refIndex = 0;
    return ZenasView({ workspaceName: null, plugins: { enabled: false, secrets: [], files: [], links: [], connections: [] } });
  }
  function find(node, predicate) {
    if (!React.isValidElement(node)) return null;
    if (predicate(node)) return node;
    return React.Children.toArray(node.props.children).map(child => find(child, predicate)).find(Boolean);
  }
  return { calls, async submit() {
    const tree = render();
    find(tree, node => node.props.id === 'zenas-ask').props.ref.current = { value: 'May a corporation be incorporated under Delaware law?' };
    await find(tree, node => node.type === 'form').props.onSubmit({ preventDefault() {} });
    return renderToStaticMarkup(render());
  } };
}

test('Ask renders the retained law URL on the existing citation after a real submit', async () => {
  const h = harness({ ok: true, state: 'released', memo: {
    text: citation, citations: [citation], sources: [{ citation, url }],
  } });
  const html = await h.submit();
  assert.match(html, /<strong><a href="https:\/\/delcode\.delaware\.gov\/title8\/c001\/sc01\/index\.html">8 Del\. C\. § 101<\/a><\/strong>/);
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].endpoint, '/api/zenas/answers');
  assert.equal(h.calls[0].method, 'POST');
  assert.deepEqual(JSON.parse(h.calls[0].body), { question: 'May a corporation be incorporated under Delaware law?' });
});

test('unreleased answers expose neither source links nor answer text', async () => {
  const h = harness({ ok: true, state: 'in_review', memo: {
    text: citation, citations: [citation], sources: [{ citation, url }],
  } });
  const html = await h.submit();
  assert.ok(!html.includes(url));
  assert.ok(!html.includes(citation));
});
