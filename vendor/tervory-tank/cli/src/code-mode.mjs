import { confirmCard, followThread } from './client.mjs';
import { randomUUID } from 'node:crypto';
import { CodeFolder, redactSecrets } from './code-tools.mjs';

export async function codeSession(tank, folder, model, io, formatReceipt, sanitize, { createFolder = CodeFolder.create } = {}) {
  if (io.interactive !== true) throw new Error('Code mode requires an interactive terminal.');
  const local = await createFolder(folder);
  const thread = (await tank.json('/v1/command/threads', {})).thread_id;
  io.write(`Thread ${sanitize(thread)}\n`);
  const notesFolder = await CodeFolder.create(local.gitWorkTree);
  let notes = { exists: false };
  try { notes = await notesFolder.read('AGENTS.md'); }
  catch { io.write('AGENTS.md skipped: Code tool refused.\n'); }
  const project = notes.exists ? { kind: 'project', content: notes.text } : null;
  const history = [];
  const remember = (kind, content) => {
    content = redactSecrets(content);
    if (Buffer.byteLength(content) > 200000) return;
    history.push({ kind, content });
    while (history.length > 40 || history.reduce((n, item) => n + Buffer.byteLength(item.content), 0) > 200000) history.shift();
  };
  while (true) {
    if (io.signal?.aborted) throw new Error('Command cancelled; no action was retried.');
    const input = await io.prompt('> ');
    if (input === null || input.trim() === '/exit') return;
    if (!input.trim()) continue;
    if (input.trim() === '/undo') {
      try { await local.undo(io); } catch { io.write('Code tool refused.\n'); }
      continue;
    }
    const text = redactSecrets(input);
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
      // Existing token-chosen origin and the existing bounded JSON transport.
      // No request is retried, including an interrupted/charged code turn.
      const plan = await tank.json(`/v1/command/threads/${encodeURIComponent(thread)}/code-turn`, {
        text, model, tool_results: results, prompt_id: promptId, continuation: round > 0, history: previous,
      });
      if (typeof plan.answer !== 'string' || !Array.isArray(plan.requests) || plan.requests.length > 6) throw new Error('Tank sent an invalid answer.');
      if (plan.answer) remember('assistant', plan.answer);
      if (plan.answer) io.write(sanitize(redactSecrets(plan.answer)) + '\n');
      io.write(formatReceipt(plan.receipt ?? {}).map(sanitize).join('\n') + '\n');
      if (!plan.requests.length) break;
      for (const request of plan.requests) {
        if (io.signal?.aborted) throw new Error('Command cancelled; no action was retried.');
        remember('tool', JSON.stringify({ name: request.name, arguments: request.arguments }));
        let result;
        try {
          if (request.name === 'tervory') {
            const index = plan.requests.slice(0, plan.requests.indexOf(request)).filter(item => item.name === 'tervory').length;
            const card = plan.cards?.[index];
            if (!card || card.tool !== request.arguments.tool || card.status !== 'pending') throw new Error('Tank sent an invalid answer.');
            io.write(sanitize(redactSecrets(JSON.stringify({ tool: card.tool, arguments: card.arguments ?? {} }))) + '\n');
            const confirmed = await confirmCard(tank, card, io);
            if (confirmed.status === 'cancelled') result = { tool: card.tool, status: 'cancelled' };
            else {
              await followThread(tank, thread, confirmed, io);
              const snapshot = await tank.json(`/v1/command/threads/${encodeURIComponent(thread)}`);
              const stored = snapshot.cards?.find(item => item.card_id === card.card_id);
              if (!stored) throw new Error('Tank sent an invalid answer.');
              result = { tool: stored.tool, status: stored.status, result: JSON.parse(JSON.stringify(stored.result ?? null, (key, value) => typeof value === 'string' ? redactSecrets(value) : value)) };
            }
          } else result = await local.tool(request, io);
        }
        catch { result = { error: 'Code tool refused.' }; }
        // The tool's output has already been redacted line-by-line. Redact the
        // envelope too, so model-supplied argument strings cannot smuggle secrets.
        const output = redactSecrets(JSON.stringify({ arguments: request.arguments, result }));
        while (results.length && (results.length >= 6 || results.reduce((n, item) => n + Buffer.byteLength(item.output), 0) + Buffer.byteLength(output) > 1_500_000)) results.shift();
        if (Buffer.byteLength(output) > 400000) {
          results.push({ name: request.name, output: JSON.stringify({ error: 'Code tool result exceeds the limit.' }) });
        } else results.push({ name: request.name, output });
      }
    }
  }
}
