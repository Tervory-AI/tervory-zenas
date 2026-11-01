const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../..');
const deps = createRequire(path.join(root, 'workspace/package.json'));
const ts = deps('typescript');
const filename = path.join(root, 'workspace/app/api/zenas/moot/[[...segments]]/route.ts');
const tenant = { tenantId: 'b734c3df-a3e3-4d25-86ea-0286d63ba4c9', orgId: 'org-local', userId: 'founder' };
function harness({ founder = true, members = ['founder', 'student'], ownMember = true } = {}) {
  const calls = [];
  const module = { exports: {} };
  const require = spec => {
    if (spec === '@/lib/tenant') return { currentTenant: async () => tenant };
    if (spec === '@/lib/admin') return { isFounder: () => founder };
    if (spec === '@/lib/workspace-origin') return { isWorkspaceOrigin: req => req.headers.get('origin') === 'https://zenas.tervory.com' };
    if (spec === '@clerk/nextjs/server') return { auth: async () => ({ userId: tenant.userId }), clerkClient: async () => ({ organizations: { getOrganizationMembershipList: async ({ userId }) => ({
      data: (userId || members).filter(id => members.includes(id) && (ownMember || id !== tenant.userId)).map(userId => ({ publicUserData: { userId, firstName: userId } })),
    }) } }) };
    if (spec === '../../tank') return { zenas: async (url, options) => { calls.push({ url, options }); return Response.json({ hearing: { revision: 1 } }); } };
    return deps(spec);
  };
  const js = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  new Function('require', 'module', 'exports', 'process', js)(require, module, module.exports, { env: { TANK_REGION: 'us-east' } });
  return { ...module.exports, calls };
}
const id = 'c634c3df-a3e3-4d25-86ea-0286d63ba4c9';
const context = segments => ({ params: Promise.resolve({ segments }) });
function post(body, origin = 'https://zenas.tervory.com') { return new Request('https://zenas.tervory.com/api/zenas/moot/hearings', { method: 'POST', headers: { origin, 'content-type': 'application/json' }, body: JSON.stringify(body) }); }
const create = () => ({ request_id: id, worked_id: id, seats: { applicant: { kind: 'human', user_id: 'student' }, respondent: { kind: 'ai' }, judge: { kind: 'ai' } } });

test('Moot adapter derives identity, verifies membership and forwards no client capability', async () => {
  const h = harness();
  const res = await h.POST(post(create()), context(['hearings']));
  assert.equal(res.status, 200);
  const { url, options } = h.calls[0];
  assert.equal(url, 'moot/hearings?home_region=us-east');
  assert.deepEqual(options.headers, { 'X-Moot-Actor': 'founder', 'X-Moot-Tenant': tenant.tenantId });
  assert.equal(res.headers.get('cache-control'), 'no-store');
  assert.equal(JSON.parse(options.body).seats.applicant.user_id, 'student');
});

test('Moot adapter blocks cross-origin mutations, forged identities and foreign participants', async () => {
  for (const [h, body, origin, expected] of [
    [harness(), create(), 'https://attacker.invalid', 403],
    [harness(), { ...create(), founder: true }, undefined, 400],
    [harness(), { ...create(), tenant_id: 'other' }, undefined, 400],
    [harness({ founder: false }), create(), undefined, 403],
    [harness({ members: ['founder'] }), create(), undefined, 403],
    [harness({ ownMember: false }), create(), undefined, 404],
  ]) {
    const res = await h.POST(post(body, origin), context(['hearings']));
    assert.equal(res.status, expected);
    assert.equal(h.calls.length, 0);
  }
});

test('Moot adapter confines proxy paths and seat identifiers', async () => {
  const h = harness();
  assert.equal((await h.GET(new Request('https://zenas.tervory.com/api/zenas/moot'), context(['..', 'pool']))).status, 404);
  assert.equal((await h.GET(new Request('https://zenas.tervory.com/api/zenas/moot?seat=bench_notes'), context(['hearings', id]))).status, 400);
  assert.equal(h.calls.length, 0);
});

test('hearing client ignores old revisions and preserves the selected hearing', () => {
  const file = path.join(root, 'workspace/app/zenas/moot-hearing-types.ts');
  const module = { exports: {} };
  const js = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  new Function('module', 'exports', js)(module, module.exports);
  const current = { id, revision: 12 };
  assert.equal(module.exports.newerHearing(current, { id, revision: 11 }, id), current);
  assert.equal(module.exports.newerHearing(current, { id: tenant.tenantId, revision: 99 }, id), current);
  assert.equal(module.exports.newerHearing(current, { id, revision: 13 }, id).revision, 13);
});
