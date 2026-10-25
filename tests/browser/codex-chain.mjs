import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';

const REPO = fileURLToPath(new URL('../../', import.meta.url));

async function bounded(promise, ms, message) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), ms); })]);
  } finally { clearTimeout(timer); }
}

// The helper installs into temporary HOME/CODEX_HOME and executes the command
// actually written to hooks.json. It never launches Codex or reveals its key.
export function startChain() {
  const child = spawn(process.env.PYTHON || 'python3', ['-I', '-B', '-u',
    fileURLToPath(new URL('./start_codex_chain.py', import.meta.url)), REPO], { stdio: ['pipe', 'pipe', 'pipe'] });
  return connectChain(child);
}

// Exported separately so process failures can be exercised without a browser,
// credentials, a Python installation or intentionally failing the real server.
export function connectChain(child, { readyMs = 45_000, hookMs = 8_000, closeMs = 8_000 } = {}) {
  const lines = createInterface({ input: child.stdout });
  const pending = new Map();
  let resolveReady, rejectReady, nextId = 0, stderrBytes = 0, failure, isReady = false;
  const ready = new Promise((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
  // A spawn failure can arrive before the caller starts awaiting readiness.
  ready.catch(() => {});
  const fail = (error) => {
    failure ??= error;
    rejectReady(failure);
    for (const waiter of pending.values()) waiter.reject(failure);
  };
  child.stderr.on('data', (chunk) => { stderrBytes += chunk.length; });
  child.stdin.on('error', () => fail(new Error('Codex fixture input pipe failed')));
  child.once('error', () => fail(new Error('Codex fixture could not start')));
  const closed = new Promise((resolve) => {
    // Unlike exit, close follows the draining of stdout/stderr. A final safe
    // protocol error must be retained before considering the generic exit code.
    child.once('close', (code, signal) => {
      const status = signal ? `Codex fixture stopped by ${signal}` : `Codex fixture exited ${code}`;
      if (code !== 0) fail(new Error(status));
      else if (!isReady || pending.size) fail(new Error('Codex fixture closed before its response'));
      resolve(status);
    });
  });
  lines.on('line', (line) => {
    let message;
    try { message = JSON.parse(line); } catch { fail(new Error('Invalid Codex fixture response')); return; }
    if (message?.type === 'ready') { isReady = true; resolveReady(message); }
    else if (message?.type === 'result') pending.get(message.id)?.resolve(message);
    else if (message?.type === 'error') {
      // Python emits fixed codes or an exception class, never exception text.
      const code = typeof message.code === 'string' && /^(?:[a-z][a-z0-9-]{0,63}|fixture-[A-Za-z][A-Za-z0-9]{0,63})$/.test(message.code)
        ? message.code : 'invalid-error-code';
      fail(new Error(`Codex fixture: ${code}`));
    } else fail(new Error('Unknown Codex fixture response'));
  });
  return {
    ready: () => bounded(ready, readyMs, 'Codex fixture readiness timed out'),
    async hook(event) {
      if (failure) throw failure;
      const id = ++nextId;
      const result = new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
      child.stdin.write(JSON.stringify({ id, op: 'hook', event }) + '\n');
      try { return await bounded(result, hookMs, 'Installed Codex hook timed out'); }
      finally { pending.delete(id); }
    },
    async close() {
      child.stdin.end();
      let status;
      try {
        try { status = await bounded(closed, closeMs, 'Codex fixture cleanup timed out'); }
        catch (error) {
          fail(error);
          child.kill('SIGTERM');
          try { status = await bounded(closed, closeMs, 'Codex fixture did not stop after SIGTERM'); }
          catch {
            child.kill('SIGKILL');
            try { status = await bounded(closed, closeMs, 'Codex fixture did not stop after SIGKILL'); }
            catch (stopError) { throw new Error(`${failure.message}; ${stopError.message}`); }
          }
        }
        if (failure) throw new Error(failure.message === status ? status : `${failure.message} (${status})`);
        if (stderrBytes) throw new Error(`Codex fixture stderr must stay empty: ${stderrBytes} bytes (${status})`);
      } finally { lines.close(); }
    },
  };
}
