/* Explicit legacy tenant-directory protocol for offline transport fixtures. */
const assert = require('node:assert/strict');
exports.legacyHome = function legacyHome(raw, init, {tenants, key, origin, calls}) {
  const url = new URL(raw instanceof Request ? raw.url : String(raw));
  const match = /^\/v1\/tenants\/([^/]+)\/home$/.exec(url.pathname);
  if (!match) return null;
  const tenant = decodeURIComponent(match[1]);
  assert(tenants.includes(tenant), 'Directory lookup must use the fixture actor tenant');
  assert.equal(url.origin, new URL(origin).origin);
  assert.equal(url.search, '');
  assert.equal(init.method, 'GET');
  assert.equal(init.body, undefined, 'Directory metadata never receives private content');
  const headers = new Headers(init.headers);
  assert(key && headers.get('x-tank-key') === key);
  assert(!headers.has('x-tervory-user') && !headers.has('authorization'));
  calls.push({tenant, url: url.href});
  return Response.json({detail:{code:'tenant_home_missing'}}, {status:404});
};
