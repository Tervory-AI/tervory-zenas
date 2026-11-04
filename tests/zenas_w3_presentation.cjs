const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const localTypeScript = path.join(root, 'workspace/node_modules/typescript');
const ts = require(process.env.TYPESCRIPT_PATH || localTypeScript);
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(
  fs.readFileSync(filename, 'utf8'),
  {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}
).outputText, filename);
const {presentCard} = require('../workspace/app/chats/presentation.ts');

const draft = presentCard({card_id:'placeholder-draft',kind:'paid_run',summary:'Draft a document',
  tool:'zenas.draft',status:'pending',arguments:{template:'letter',jurisdiction:'us',
    terms:{subject:'placeholder-hidden-term'},text:'placeholder document',matter_id:'placeholder-matter'}},false);
const shown = JSON.stringify(draft);
assert(shown.includes('Jurisdiction'));
assert(shown.includes('Matter'));
assert(shown.includes('Document'));
assert(!shown.includes('placeholder-hidden-term'));
assert(!shown.includes('template'));

const add = presentCard({card_id:'placeholder-guide',kind:'other',summary:'Add a review guide',
  tool:'zenas.playbook.add',status:'pending',arguments:{name:'placeholder guide',template:'letter',
    positions:[{kind:'subject',text:'placeholder-hidden-position'}]}},false);
const guide = JSON.stringify(add);
assert(guide.includes('placeholder guide'));
assert(!guide.includes('placeholder-hidden-position'));
assert(!guide.includes('template'));
console.log('PASS Zenas W3 approved labels and hidden drafting fields');
