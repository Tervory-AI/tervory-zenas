// Actual Workspace transport and imports; only local HTTP/identity is doubled.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
process.env.COMMAND_TEST_TOOLS ??= path.join(root, 'workspace/node_modules');
const {workspace} = require('../command_acceptance_20261031/workspace_loader.cjs');
const {legacyHome} = require('../legacy_tenant_home.cjs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const captures = [], homes = [];
const load = workspace({
  env:{TANK_REGION:'us-east',TANK_URL:'http://offline-tank',TANK_API_KEY:'offline-key'},
  fetch:async(raw,init)=>{
    const home=legacyHome(raw,init,{tenants:[input.body.tenant_id],key:'offline-key',origin:'http://offline-tank',calls:homes});
    if(home)return home;
    assert.equal(new URL(String(raw)).origin,'http://offline-tank');
    captures.push({url:String(raw),body:JSON.parse(init.body)});
    return Response.json({});
  },
});
load('app/api/zenas/tank.ts').zenas(input.path,{method:'POST',body:JSON.stringify(input.body)})
  .then(()=>{assert.equal(captures.length,1);process.stdout.write(JSON.stringify(captures[0]));})
  .catch(error=>{console.error(error);process.exitCode=1;});
