import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { spawn } from 'node:child_process';
import { test, expect } from '@playwright/test';
import { connectChain } from './codex-chain.mjs';

function fixture({ onEnd, onKill, ready = true, timeouts } = {}) {
  const child = new EventEmitter();
  child.stdin = new PassThrough();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  child.sentSignals = [];
  child.send = (message) => child.stdout.write(JSON.stringify(message) + '\n');
  child.finish = (code, signal = null) => {
    child.emit('exit', code, signal);
    child.stdout.end();
    child.stderr.end();
    setImmediate(() => child.emit('close', code, signal));
  };
  child.kill = (signal) => {
    child.sentSignals.push(signal);
    if (onKill) onKill(child, signal);
    else child.finish(null, signal);
    return true;
  };
  child.stdin.on('finish', () => onEnd ? onEnd(child) : child.finish(0));
  const chain = connectChain(child, timeouts);
  if (ready) child.send({ type: 'ready' });
  return { child, chain };
}

const shortTimeouts = { readyMs: 20, hookMs: 20, closeMs: 20 };

test('Codex fixture normal EOF exits cleanly', async () => {
  const { child, chain } = fixture();
  await chain.ready();
  await chain.close();
  expect(child.sentSignals).toEqual([]);
});

for (const code of [1, 0]) {
  test(`Codex fixture preserves its first safe error with exit ${code}`, async () => {
    const { chain } = fixture({ onEnd(child) {
      child.send({ type: 'error', code: 'fixture-PermissionError' });
      child.send({ type: 'error', code: 'fixture-OSError' });
      child.finish(code);
    } });
    await chain.ready();
    await expect(chain.close()).rejects.toThrow(`Codex fixture: fixture-PermissionError (Codex fixture exited ${code})`);
  });
}

test('Codex fixture reports a silent abnormal exit', async () => {
  const { chain } = fixture({ onEnd: (child) => child.finish(1) });
  await chain.ready();
  await expect(chain.close()).rejects.toThrow('Codex fixture exited 1');
});

test('Codex fixture drains a final protocol message after exit before closing', async () => {
  const { chain } = fixture({ onEnd(child) {
    child.emit('exit', 1, null);
    setImmediate(() => {
      child.send({ type: 'error', code: 'fixture-RuntimeError' });
      child.stdout.end();
      child.stderr.end();
      setImmediate(() => child.emit('close', 1, null));
    });
  } });
  await chain.ready();
  await expect(chain.close()).rejects.toThrow('Codex fixture: fixture-RuntimeError (Codex fixture exited 1)');
});

test('Codex fixture captures a final message from a real child process', async () => {
  const child = spawn(process.execPath, ['-e', `
    process.stdout.write(JSON.stringify({ type: 'ready' }) + '\\n');
    process.stdin.resume();
    process.stdin.on('end', () => process.stdout.write(
      JSON.stringify({ type: 'error', code: 'fixture-RuntimeError' }) + '\\n',
      () => process.exit(1)));
  `], { stdio: ['pipe', 'pipe', 'pipe'] });
  const chain = connectChain(child);
  await chain.ready();
  await expect(chain.close()).rejects.toThrow('Codex fixture: fixture-RuntimeError (Codex fixture exited 1)');
});

test('Codex fixture startup failure hides raw exception details', async () => {
  const { child, chain } = fixture({ ready: false, onEnd() {} });
  const readiness = expect(chain.ready()).rejects.toThrow('Codex fixture could not start');
  child.emit('error', new Error('PRIVATE_ARGV_FIXTURE'));
  child.finish(-2);
  await readiness;
  await expect(chain.close()).rejects.toThrow(/^Codex fixture could not start \(Codex fixture exited -2\)$/);
});

test('Codex fixture bounds readiness and recognizes premature clean closure', async () => {
  const { chain } = fixture({ ready: false, timeouts: shortTimeouts });
  await expect(chain.ready()).rejects.toThrow('Codex fixture readiness timed out');
  await expect(chain.close()).rejects.toThrow('Codex fixture closed before its response (Codex fixture exited 0)');
});

test('Codex fixture bounds an unanswered hook and removes its pending waiter', async () => {
  const { chain } = fixture({ timeouts: shortTimeouts });
  await chain.ready();
  await expect(chain.hook({ hook_event_name: 'Stop' })).rejects.toThrow('Installed Codex hook timed out');
  await chain.close();
});

test('Codex fixture rejects a pending hook on premature clean closure', async () => {
  const { child, chain } = fixture({ onEnd() {} });
  await chain.ready();
  const hook = expect(chain.hook({ hook_event_name: 'Stop' })).rejects.toThrow('Codex fixture closed before its response');
  child.finish(0);
  await hook;
  await expect(chain.close()).rejects.toThrow('Codex fixture closed before its response (Codex fixture exited 0)');
});

test('Codex fixture bounds cleanup and preserves the timeout after SIGTERM', async () => {
  const { child, chain } = fixture({ onEnd() {}, timeouts: shortTimeouts });
  await chain.ready();
  await expect(chain.close()).rejects.toThrow('Codex fixture cleanup timed out (Codex fixture stopped by SIGTERM)');
  expect(child.sentSignals).toEqual(['SIGTERM']);
});

test('Codex fixture kills a child that ignores SIGTERM without hiding its first error', async () => {
  const { child, chain } = fixture({
    onEnd(child) { child.send({ type: 'error', code: 'fixture-RuntimeError' }); },
    onKill(child, signal) { if (signal === 'SIGKILL') child.finish(null, signal); },
    timeouts: shortTimeouts,
  });
  await chain.ready();
  await expect(chain.close()).rejects.toThrow('Codex fixture: fixture-RuntimeError (Codex fixture stopped by SIGKILL)');
  expect(child.sentSignals).toEqual(['SIGTERM', 'SIGKILL']);
});

test('Codex fixture retains its first error if process closure never arrives', async () => {
  const { child, chain } = fixture({
    onEnd(child) { child.send({ type: 'error', code: 'fixture-RuntimeError' }); },
    onKill() {},
    timeouts: shortTimeouts,
  });
  await chain.ready();
  await expect(chain.close()).rejects.toThrow('Codex fixture: fixture-RuntimeError; Codex fixture did not stop after SIGKILL');
  expect(child.sentSignals).toEqual(['SIGTERM', 'SIGKILL']);
});

test('Codex fixture reports stderr byte count without exposing its contents', async () => {
  const { chain } = fixture({ onEnd(child) { child.stderr.write('PRIVATE'); child.finish(0); } });
  await chain.ready();
  await expect(chain.close()).rejects.toThrow(/^Codex fixture stderr must stay empty: 7 bytes \(Codex fixture exited 0\)$/);
});

test('Codex fixture rejects malformed error text without exposing its contents', async () => {
  const { chain } = fixture({ onEnd(child) {
    child.send({ type: 'error', code: 'Traceback: PRIVATE_ARGV_FIXTURE\nsecret' });
    child.finish(1);
  } });
  await chain.ready();
  await expect(chain.close()).rejects.toThrow(/^Codex fixture: invalid-error-code \(Codex fixture exited 1\)$/);
});
