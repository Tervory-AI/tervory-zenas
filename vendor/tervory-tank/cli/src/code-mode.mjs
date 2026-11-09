import { confirmCard, followThread } from './client.mjs';
import { randomUUID } from 'node:crypto';
import { CodeFolder, redactSecrets } from './code-tools.mjs';
import { MCPClients } from './mcp.mjs';
import { gitSlash } from './code-git.mjs';
import { attachmentsFrom } from './code-attachments.mjs';
import { ConfigurationError } from './configuration.mjs';
import { emptyState } from './empty-state.mjs';

export const CODE_HELP = '/help | /mode ask|auto-edit|plan | /model [name] | /resume [session] | /undo | /cost | /commit | /branch [name] | /pr | /steps [number] | /clear | /exit';
const MODES = new Set(['ask', 'auto-edit', 'plan']);
const READS = new Set(['list_files', 'read_file', 'search', 'git']);
export const dollars = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? `$${value.toFixed(6)}` : 'pending';

export async function codeSession(tank, folder, model, io, formatReceipt, sanitize,
  { createFolder = CodeFolder.create, resume = false, task, headless = false, createMCP = (tank, io) => new MCPClients(tank, io) } = {}) {
  if (!headless && io.interactive !== true) throw new Error('Code mode requires an interactive terminal.');
  let local = await createFolder(folder);
  let thread; let mode = 'ask'; let lastAnswer = ''; let status = 'completed';
  let pendingTask = task; let cost = 0; let knownCost = true;
  let savings = 0; let knownSavings = true;
  const turns = []; const history = [];
  const mcp = createMCP(tank, io);
  const clean = value => sanitize(redactSecrets(mcp.clean ? mcp.clean(String(value)) : String(value)));
  const cleanObject = value => mcp.cleanObject ? mcp.cleanObject(value) : value;
  const statusLine = () => { if (!headless) io.status?.({model:model === 'auto'?'Auto':model,mode,cost_usd:knownCost?cost:null,savings_usd:knownSavings?savings:null}); };
  const notesFolder = await CodeFolder.create(local.gitWorkTree);
  let notes = { exists: false };
  try { notes = await notesFolder.read('AGENTS.md'); }
  catch { io.write('AGENTS.md skipped: Code tool refused.\n'); }
  const project = notes.exists ? { kind: 'project', content: notes.text } : null;
  const remember = (kind, content) => {
    content = redactSecrets(content);
    if (Buffer.byteLength(content) > 200000) return;
    history.push({ kind, content });
    while (history.length > 40 || history.reduce((n, item) => n + Buffer.byteLength(item.content), 0) > 200000) history.shift();
  };
  const fresh = async () => {
    thread = (await tank.json('/v1/command/threads', {})).thread_id;
    if (typeof thread !== 'string' || !thread) throw new Error('Tank sent an invalid answer.');
    history.length = 0; cost = 0; knownCost = true; turns.length = 0; lastAnswer = ''; status = 'completed';
    savings = 0; knownSavings = true;
    io.reset?.();
    io.write(`Thread ${clean(thread)}\n`);
    if (!headless) io.write(emptyState('code'));
  };
  const reopen = async selected => {
    const sessions = await tank.json('/v1/command/code-sessions');
    if (!Array.isArray(sessions)) throw new Error('Tank sent an invalid answer.');
    for (const row of sessions) io.write(`${clean(row.thread_id)} ${clean(row.title ?? '')}\n`);
    if (!sessions.length) { io.write(emptyState('sessions')); return false; }
    selected ||= (await io.prompt('Session: '))?.trim();
    if (!selected) return false;
    if (!sessions.some(row => row.thread_id === selected)) { io.write('Code session not available.\n'); return false; }
    const saved = await tank.json(`/v1/command/code-sessions/${encodeURIComponent(selected)}`);
    if (saved.thread_id !== selected || !Array.isArray(saved.history) || saved.active) throw new Error('Code session cannot be resumed while a turn is active.');
    local = await createFolder(folder); // Undo snapshots and reads belong to this local session only.
    thread = selected; history.length = 0; turns.length = 0; mode = 'ask'; model = 'auto'; lastAnswer = ''; status = 'completed';
    io.reset?.();
    for (const item of saved.history) {
      if (['user', 'assistant'].includes(item.kind) && typeof item.content === 'string') {
        remember(item.kind, item.content); io.write(`${item.kind}: ${clean(item.content)}\n`);
      }
    }
    cost = typeof saved.cost_usd === 'number' && saved.cost_usd >= 0 ? saved.cost_usd : 0;
    knownCost = typeof saved.cost_usd === 'number' && Number.isFinite(saved.cost_usd) && saved.cost_usd >= 0;
    knownSavings = typeof saved.savings_usd === 'number' && Number.isFinite(saved.savings_usd); savings = knownSavings?saved.savings_usd:0;
    io.write(`Thread ${clean(thread)}\n`); return true;
  };
  const chooseModel = async selected => {
    const roster = await tank.json('/v1/command/code-models');
    if (!Array.isArray(roster) || roster.some(row => !row || typeof row.name !== 'string')) throw new Error('Tank sent an invalid answer.');
    io.write('Auto\n');
    if (!roster.length) io.write(emptyState('models'));
    for (const row of roster) io.write(`${clean(row.name)} | in ${dollars(row.input_usd_per_m)}/M | out ${dollars(row.output_usd_per_m)}/M\n`);
    selected ||= (await io.prompt('Model: '))?.trim();
    if (!selected) return;
    if (selected.toLowerCase() === 'auto') model = 'auto';
    else if (roster.some(row => row.name === selected)) model = selected;
    else { io.write('Model not available.\n'); return; }
    io.write(`Model ${clean(model === 'auto' ? 'Auto' : model)}\n`);
  };
  try {
  if (resume) { if (!await reopen(typeof resume === 'string' ? resume : undefined)) return { status: 'cancelled', turns: [] }; }
  else await fresh();
  if (model !== 'auto') await chooseModel(model);
  while (true) {
    if (io.signal?.aborted) throw new Error('Command cancelled; no action was retried.');
    statusLine();
    const input = pendingTask ?? (headless ? null : await io.prompt('> ')); pendingTask = undefined;
    if (input == null || (!headless && input.trim() === '/exit')) return { status, thread_id: thread, answer: lastAnswer, model, cost_usd: knownCost ? cost : null, turns };
    if (!input.trim()) continue;
    if (input.trim().startsWith('/') && !headless) {
      const [slash, ...parts] = input.trim().split(/\s+/); const value = parts.join(' ');
      if (slash === '/help') io.write(CODE_HELP + '\n');
      else if (slash === '/mode') {
        const next = value.toLowerCase() || (await io.prompt('Mode (ask, auto-edit, plan): '))?.trim().toLowerCase();
        if (MODES.has(next)) { mode = next; io.write(`Mode ${mode}\n`); } else io.write('Invalid code mode.\n');
      } else if (slash === '/model') await chooseModel(value);
      else if (slash === '/resume') await reopen(value);
      else if (slash === '/cost') {
        io.write(`Session cost ${dollars(knownCost ? cost : null)}\n`);
        if (!history.length && knownCost && cost === 0) io.write(emptyState('code'));
      }
      else if (slash === '/steps') io.steps?.(value);
      else if (['/commit','/branch','/pr'].includes(slash)) { try { await gitSlash(slash, value, local, io, mode); } catch (error) { io.write(error instanceof ConfigurationError ? error.message + '\n' : 'Code Git refused.\n'); } }
      else if (slash === '/clear') { local = await createFolder(folder); mode = 'ask'; model = 'auto'; await fresh(); }
      else if (slash === '/undo') {
        if (mode === 'plan') io.write('Plan mode is read only. Switch mode before undo.\n');
        else { try { await local.undo(io); } catch { io.write('Code tool refused.\n'); } }
      }
      else io.write(CODE_HELP + '\n');
      continue;
    }
    status = 'completed';
    const text = redactSecrets(input);
    const attachments = await attachmentsFrom(text, local, tank, thread, io);
    let local_mcp;
    try { local_mcp = await mcp.tools(mode, headless); }
    catch (error) {
      if (!(error instanceof ConfigurationError) || headless) throw error;
      io.write(clean(error.message) + '\n'); status = 'needs_input'; continue;
    }
    const promptId = randomUUID();
    const previous = history.slice();
    if (project) {
      while (previous.length && (previous.length >= 40 || previous.reduce((n, item) => n + Buffer.byteLength(item.content), 0) + Buffer.byteLength(project.content) > 200000)) previous.shift();
      previous.unshift(project);
    }
    remember('user', text);
    let results = [];
    for (let round = 0; ; round++) {
      if (io.signal?.aborted) throw new Error('Command cancelled; no action was retried.');
      if (round === 32) throw new Error('Code tool limit reached; nothing was retried.');
      const body = {
        text: clean(text), model, mode, tool_results: results, prompt_id: promptId, request_id: randomUUID(), continuation: round > 0, history: cleanObject(previous),
        ...(attachments.length ? { attachments } : {}), ...(local_mcp.length ? { local_mcp } : {}),
      };
      const plan = !headless && typeof io.preview === 'function' && typeof tank.codeTurn === 'function'
        ? await tank.codeTurn(thread,body,io.preview) : await tank.json(`/v1/command/threads/${encodeURIComponent(thread)}/code-turn`,body);
      if (typeof plan.answer !== 'string' || !Array.isArray(plan.requests) || plan.requests.length > 6) throw new Error('Tank sent an invalid answer.');
      lastAnswer = clean(plan.answer);
      if (plan.answer) { remember('assistant', plan.answer); io.write(lastAnswer + '\n'); }
      const rows = plan.receipt?.rows ?? [];
      const candidateCost = plan.turn?.cost_usd ?? (rows.length && rows.every(row => typeof row.cost_usd === 'number' && Number.isFinite(row.cost_usd) && row.cost_usd >= 0) ? rows.reduce((sum, row) => sum + row.cost_usd, 0) : null);
      const turnCost = typeof candidateCost === 'number' && Number.isFinite(candidateCost) && candidateCost >= 0 ? candidateCost : null;
      const turnModel = clean(plan.turn?.model ?? (model === 'auto' ? 'Auto' : model));
      turns.push({ model: turnModel, cost_usd: turnCost });
      if (typeof turnCost === 'number' && Number.isFinite(turnCost) && turnCost >= 0) cost += turnCost; else knownCost = false;
      const saved = model === 'auto' && turnCost !== null ? 0 : rows.length && rows.every(row=>row.auto_cost_status === 'priced' && typeof row.auto_cost_usd === 'number' && typeof row.cost_usd === 'number') ? rows.reduce((sum,row)=>sum+row.auto_cost_usd-row.cost_usd,0) : null;
      if (typeof saved === 'number' && Number.isFinite(saved)) savings += saved; else knownSavings = false;
      // Older servers still expose their existing receipt; the patch adds the
      // actual permitted model and cost for every code turn, including Auto.
      if (plan.turn) io.write(`Model ${turnModel} | cost ${dollars(turnCost)}\n`);
      io.write(formatReceipt(plan.receipt ?? {}).map(sanitize).join('\n') + '\n');
      statusLine();
      if (!plan.requests.length) {
        if (mode === 'plan') {
          status = 'needs_input';
          if (!headless && plan.answer && (await io.prompt('Approve plan [y/N] '))?.trim().toLowerCase() === 'y') {
            mode = 'ask'; pendingTask = 'Execute the approved plan.';
          }
        }
        break;
      }
      let blocked = false;
      for (const request of plan.requests) {
        if (io.signal?.aborted) throw new Error('Command cancelled; no action was retried.');
        if (!request || typeof request.name !== 'string' || !request.arguments || typeof request.arguments !== 'object') throw new Error('Tank sent an invalid answer.');
        if ((mode === 'plan' || headless) && !READS.has(request.name)) { blocked = true; status = 'needs_input'; break; }
        remember('tool', JSON.stringify(cleanObject({ name: request.name, arguments: request.arguments })));
        if (!headless) io.tool?.({phase:'start',name:request.name,arguments:JSON.parse(JSON.stringify(cleanObject(request.arguments),(key,value)=>typeof value==='string'?clean(value):value))});
        let result;
        try {
          if (request.name === 'local_mcp') result = await mcp.call(request.arguments, mode, headless);
          else if (request.name === 'tervory') {
            const index = plan.requests.slice(0, plan.requests.indexOf(request)).filter(item => item.name === 'tervory').length;
            const card = plan.cards?.[index];
            if (!card || card.tool !== request.arguments.tool || card.status !== 'pending') throw new Error('Tank sent an invalid answer.');
            io.write(clean(JSON.stringify({ tool: card.tool, arguments: card.arguments ?? {} })) + '\n');
            const confirmed = await confirmCard(tank, card, io);
            if (confirmed.status === 'cancelled') result = { tool: card.tool, status: 'cancelled' };
            else {
              await followThread(tank, thread, confirmed, io);
              const snapshot = await tank.json(`/v1/command/threads/${encodeURIComponent(thread)}`);
              if (snapshot.thread && Object.hasOwn(snapshot.thread, 'spent_usd')) {
                const total = snapshot.thread.spent_usd;
                knownCost = typeof total === 'number' && Number.isFinite(total) && total >= 0;
                cost = knownCost ? total : cost;
              }
              const stored = snapshot.cards?.find(item => item.card_id === card.card_id);
              if (!stored) throw new Error('Tank sent an invalid answer.');
              result = { tool: stored.tool, status: stored.status, result: JSON.parse(JSON.stringify(stored.result ?? null, (key, value) => typeof value === 'string' ? redactSecrets(value) : value)) };
            }
          } else {
            // Auto-edit grants exactly the existing edit approval. Validation,
            // atomic writes, undo, redaction and command approval stay unchanged.
            const approval = mode === 'auto-edit' && request.name === 'propose_edit'
              ? { ...io, prompt: async label => label === 'Apply [y/N] ' ? 'y' : io.prompt(label) } : io;
            result = await local.tool(request, approval);
          }
        } catch { result = { error: 'Code tool refused.' }; }
        if (result?.applied === false || result?.ran === false || result?.status === 'cancelled' || result?.error) { blocked = true; status = 'needs_input'; }
        if (!headless) io.tool?.({phase:'end',result:JSON.parse(JSON.stringify(result ?? null,(key,value)=>typeof value==='string'?clean(value):value)),failed:blocked});
        const output = redactSecrets(JSON.stringify(cleanObject({ arguments: request.arguments, result })));
        while (results.length && (results.length >= 6 || results.reduce((n, item) => n + Buffer.byteLength(item.output), 0) + Buffer.byteLength(output) > 1_500_000)) results.shift();
        results.push({ name: request.name, output: Buffer.byteLength(output) > 400000 ? JSON.stringify({ error: 'Code tool result exceeds the limit.' }) : output });
        if (blocked) break;
      }
      if (blocked) { io.write('Code task needs approval or a valid tool request.\n'); break; }
    }
  }
  } finally { io.preview?.(''); await mcp.close(); }
}
