const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createRequire} = require('node:module');
const root = path.resolve(__dirname, '../..');
const deps = createRequire(path.join(root, 'workspace/package.json'));
const ts = deps('typescript');
const filename = path.join(root, 'workspace/app/moot/WatchPanel.tsx');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022}}).outputText;
const loaded = {exports: {}};
new Function('require', 'module', 'exports', compiled)(deps, loaded, loaded.exports);
const React = deps('react');
const {renderToStaticMarkup} = deps('react-dom/server');
for (const verificationStatus of [undefined, 'unverified', 'verified']) {
  const row = {matchId: 'placeholder', published: true, title: 'Recorded civil appeal', judgmentOrder: 'Independent moot result', reveal: 'Stored court result', verificationStatus};
  const html = renderToStaticMarkup(React.createElement(loaded.exports.WatchPanel, {row}));
  assert(html.includes('<p>unverified</p>'));
  assert(!html.includes('<p>verified</p>'));
}
assert(fs.readFileSync(path.join(root, 'workspace/app/moot/page.tsx'), 'utf8').includes('<span>unverified</span>'));
console.log('Published index and reader retain the required unverified label.');
