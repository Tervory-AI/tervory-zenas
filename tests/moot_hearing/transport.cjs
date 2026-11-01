// Execute the existing server transport with offline fetch capture.
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../..');
const deps = createRequire(path.join(root, 'workspace/package.json'));
const ts = deps('typescript');
const moduleValue = { exports: {} };
const filename = path.join(root, 'workspace/app/api/zenas/tank.ts');
const js = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const captures = [];
new Function('module', 'exports', 'process', 'fetch', js)(moduleValue, moduleValue.exports,
  { env: { TANK_REGION: 'us-east', TANK_URL: 'http://offline-tank', TANK_API_KEY: 'offline-key' } },
  async (url, init) => { captures.push({ url, body: JSON.parse(init.body) }); return Response.json({}); });
moduleValue.exports.zenas(input.path, { method: 'POST', body: JSON.stringify(input.body) }).then(() => process.stdout.write(JSON.stringify(captures[0])));
