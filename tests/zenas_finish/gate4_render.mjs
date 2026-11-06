import { readFileSync, existsSync, statSync } from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import vm from 'node:vm';

const root = process.cwd();
const require = createRequire(path.join(root, 'workspace/package.json'));
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const cache = new Map();
const Link = ({ children, ...props }) => React.createElement('a', props, children);

function load(name) {
  name = path.normalize(name);
  if (cache.has(name)) return cache.get(name).exports;
  const filename = ['', '.tsx', '.ts', '.js'].map(ext => path.join(root, name + ext)).find(file=>existsSync(file)&&statSync(file).isFile());
  if (!filename) throw Error('Missing UI module: ' + name);
  const module = { exports: {} };
  cache.set(name, module);
  const localRequire = spec => {
    if (spec === 'next/link') return { __esModule: true, default: Link };
    if (spec === 'next/navigation') return { useRouter: () => ({ refresh() {} }) };
    if (spec.endsWith('squad-links')) return { SquadLinks: () => null };
    if (spec.endsWith('plugins-dialog')) return { PluginsDialog: () => null };
    if (spec === './store' && name.startsWith('workspace/app/moot/')) return { listPublishedMatches: async () => ({ status: 'empty', matches: [] }) };
    if (spec.endsWith('.css')) return {};
    if (spec.startsWith('@/')) return load('workspace/' + spec.slice(2));
    if (spec.startsWith('.')) return load(path.join(path.dirname(name), spec));
    return require(spec);
  };
  const compiled = ts.transpileModule(readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true, target: ts.ScriptTarget.ES2022 },
    reportDiagnostics: true,
  });
  const errors = (compiled.diagnostics ?? []).filter(d => d.category === ts.DiagnosticCategory.Error);
  if (errors.length) throw Error(errors.map(d => ts.flattenDiagnosticMessageText(d.messageText, '\n')).join('\n'));
  const fn = vm.runInThisContext('(function(require,module,exports){' + compiled.outputText + '\n})', { filename });
  fn(localRequire, module, module.exports);
  return module.exports;
}

const mode = process.argv[2];
let element;
if (mode === 'view') {
  element = React.createElement(load('workspace/app/zenas/view').ZenasView, {
    plugins: load('workspace/app/zenas/plugins').offPanel('Sign in to use Plugins.'), workspaceName: null,
  });
} else if (mode === 'deadlines') {
  element = React.createElement(load('workspace/app/zenas/deadlines-panel').DeadlinesPanel);
} else if (mode.startsWith('feeds')) {
  const { emptyConvert } = load('workspace/app/api/zenas/feeds/public');
  const scores = mode === 'feeds' ? [] : [{ region: 'us-east', kind: 'holdout', area: '', jurisdiction: 'us', cases: mode === 'feeds-zero' ? 0 : 10,
    outcome_agreement: 99, element_agreement: 99, citations_valid: 100, cost_per_case: .01, set: '', scored_by: '', label: '', error: false, reason: '' }];
  const sources = mode === 'feeds-data' ? [{ id: 'frcp', title: 'Federal Rules of Civil Procedure', region: 'us-east', documents: 42,
    last_load: null, last_status: 'ok', detail: '', errors: [] }] : [];
  element = React.createElement(load('workspace/app/zenas/feeds/board').FeedSwitch, {
    initialRegion: 'us-east', initialSources: sources, initialScores: scores, initialReview: 'off', initialConvert: emptyConvert('us-east'), note: '',
  });
} else if (mode === 'review') {
  element = React.createElement(load('workspace/app/review/console').ReviewConsole, { items: [], note: '', reviewer: '', account: '' });
} else if (mode === 'matters') {
  element = React.createElement(load('workspace/app/matters/board').MatterBoard, { initial: [], note: '' });
} else if (mode === 'moot') {
  element = React.createElement(load('workspace/app/zenas/moot-court').MootCourt);
} else if (mode === 'public-moot') {
  element = await load('workspace/app/moot/page').default({});
} else if (mode === 'configuration') {
  const rules = load('workspace/app/api/zenas/rules');
  const payload = { needs_settings: ['DATABASE_URL', 'TANK_KEY', 'placeholder-private-value'], invalid_settings: ['ZENAS_PII_KEY'], detail: 'placeholder hidden' };
  process.stdout.write(JSON.stringify({ note: rules.configurationNote(payload), ask: rules.askRefusal(503, payload),
    review: rules.reviewRefusal(503, payload), unknown: rules.configurationNote({ needs_settings: ['placeholder-private-value'] }) }));
  process.exit(0);
} else throw Error('Unknown rendering mode');
process.stdout.write(JSON.stringify({ html: renderToStaticMarkup(element) }));
