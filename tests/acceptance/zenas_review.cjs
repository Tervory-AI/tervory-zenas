// Render the shipped reviewer page; only identity and Tank/Next boundaries are fakes.
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const req = Module.createRequire(path.join(root, 'workspace/package.json'));
const ts = req('typescript');
const React = req('react');
const {renderToStaticMarkup} = req('react-dom/server');
const region = process.argv[2];
assert(['us-east', 'za'].includes(region));
for (const extension of ['.ts', '.tsx']) {
  require.extensions[extension] = (module, filename) => module._compile(ts.transpileModule(
    fs.readFileSync(filename, 'utf8'), {compilerOptions: {
      module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022,
    }}).outputText, filename);
}
let role = 'reviewer';
const calls = [];
const original = Module._load;
Module._load = function(name, parent, main) {
  if (name === 'next/link') return {__esModule:true, default: ({children, ...props}) => React.createElement('a', props, children)};
  if (name === 'next/navigation') return {notFound() {throw new Error('fixture-not-found');}, useRouter: () => ({refresh() {}})};
  if (name === '@clerk/nextjs/server') return {
    auth: async () => ({userId:'fixture-reviewer', orgRole:'org:' + role}),
    clerkClient: async () => ({users: {getUser: async () => ({firstName:'Fixture', lastName:'Reviewer',
      publicMetadata:{jurisdiction:region === 'za' ? 'za' : 'us', admission_no:'fixture-admission'}})}}),
  };
  if (name === '@/lib/tenant') return {currentTenant: async () => ({tenantId:'fixture-tenant'})};
  if (name === '@/app/api/zenas/tank') return {homeRegion: () => region, zenas: async (url) => {
    calls.push(url); return {ok:true, json:async () => ({items:[]})};
  }};
  if (name.startsWith('@/')) return original.call(this, path.join(root,'workspace',name.slice(2)), parent, main);
  return original.call(this, name, parent, main);
};
process.env.TERVORY_ADMIN_USER_IDS = '';
global.fetch = async () => {throw new Error('live network forbidden');};
(async () => {
  const page = require(path.join(root, 'workspace/app/review/page.tsx')).default;
  const html = renderToStaticMarkup(await page());
  assert(html.includes('Worked example'));
  assert(html.includes('/brand/zenas-on-dark.png'));
  assert(html.includes('/brand/tervory-logo-reversed.png'));
  assert(html.includes('Fixture Reviewer'));
  assert(calls.length === 1 && calls[0].includes('tenant_id=fixture-tenant'));
  assert(calls[0].includes('home_region=' + region));
  role = 'member';
  await assert.rejects(page(), /fixture-not-found/);
  assert(calls.length === 1);
  console.log('PASS reviewer page ' + region);
})().catch(error => {console.error(error);process.exitCode=1;});
