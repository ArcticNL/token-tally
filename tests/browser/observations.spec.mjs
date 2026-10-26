import { spawn } from 'node:child_process';
import { request } from 'node:http';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import { test, expect, preparePage, row, rows } from './fixtures.mjs';
const REPO = fileURLToPath(new URL('../../', import.meta.url));

async function bounded(promise, ms, message) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), ms); })]);
  } finally { clearTimeout(timer); }
}

// These requests originate in Node, never the browser. The real loopback-only
// server is intentionally keyless; signing is covered by the backend suite.
function jsonRequest(url, data) {
  return new Promise((resolve, reject) => {
    const body = data === undefined ? null : JSON.stringify(data);
    const req = request(url, {
      method: body === null ? 'GET' : 'POST',
      headers: body === null ? {} : { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
    }, (res) => {
      let reply = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { reply += chunk; });
      res.on('error', reject);
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: reply ? JSON.parse(reply) : null }); }
        catch (error) { reject(error); }
      });
    });
    req.setTimeout(3000, () => req.destroy(new Error('Fixture HTTP request timed out')));
    req.on('error', reject);
    req.end(body);
  });
}

test('real Claude hooks retain provider identity, observed status and last receipt in the browser @pr15', async ({ page }) => {
  const child = spawn(process.env.PYTHON || 'python3', ['-I', '-B', '-u', fileURLToPath(new URL('./start_backend.py', import.meta.url)), REPO], { stdio: ['pipe', 'pipe', 'pipe'] });
  let stderr = '';
  child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
  const exited = new Promise((resolve, reject) => { child.once('error', reject); child.once('exit', (code) => resolve(code)); });
  const lines = createInterface({ input: child.stdout });
  const ready = new Promise((resolve, reject) => lines.once('line', (line) => {
    try { resolve(JSON.parse(line)); } catch (error) { reject(error); }
  }));
  try {
    const { url } = await bounded(Promise.race([ready, exited.then((code) => { throw new Error(`Backend helper exited ${code}: ${stderr}`); })]), 12_000, 'Backend readiness timed out');
    const problems = await preparePage(page, url);
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Network.enable');
    const snapshots = [];
    cdp.on('Network.eventSourceMessageReceived', (event) => snapshots.push(JSON.parse(event.data)));
    const post = async (machine, hook_event_name, more = {}) => {
      const result = await jsonRequest(url + '/hook', { machine, hook_event_name, session_id: 'shared-session', cwd: '/work/sample-project', ...more });
      expect(result.status).toBe(204);
    };
    const state = async () => {
      const result = await jsonRequest(url + '/state');
      expect(result.status).toBe(200);
      return result.body;
    };
    const field = (name) => page.locator(`#file-body dd[data-field="${name}"]`);

    await page.goto(url + '/?lite');
    await expect(page.locator('#conn')).toHaveText('Live');
    await expect.poll(() => snapshots.length).toBeGreaterThan(0);
    expect(snapshots[0].agents).toEqual([]);

    await post('Fixture A', 'SessionStart');
    await post('Fixture B', 'SessionStart');
    await post('Fixture A', 'SubagentStart', { agent_id: 'child', parent_session_id: 'shared-session' });
    const initial = await state();
    expect(initial.agents).toHaveLength(3);
    const first = initial.agents.find((a) => a.machine === 'Fixture A' && !a.temp);
    const second = initial.agents.find((a) => a.machine === 'Fixture B');
    const temp = initial.agents.find((a) => a.temp);
    expect(first.provider).toBe('claude');
    expect(second.provider).toBe('claude');
    expect(first.client_id).toBe('legacy-machine:Fixture A');
    expect(second.client_id).toBe('legacy-machine:Fixture B');
    expect(first.identity_quality).toBe('legacy');
    expect(first.id).not.toBe(second.id);
    expect(temp.parent).toBe(first.id);
    expect(temp.client_id).toBe(first.client_id);
    await expect(rows(page)).toHaveCount(3);
    await row(page, first.id).click();

    // Post both tool events while the browser is already connected. This tests
    // the actual four-second MIN_SHOW window without patching backend time.
    await post('Fixture A', 'UserPromptSubmit', { turn_id: 'sample-turn' });
    const call = { turn_id: 'sample-turn', tool_use_id: 'sample-call', tool_name: 'Read', tool_input: { file_path: '/work/sample.txt' } };
    await post('Fixture A', 'PreToolUse', call);
    await post('Fixture A', 'PostToolUse', call);
    const held = (await state()).agents.find((a) => a.id === first.id);
    expect(held.status).toBe('working');
    expect(held.task).toBe('files');
    expect(held.observed).toMatchObject({ status: 'thinking', task: '', source: 'hook', confidence: 'observed' });
    expect(held.last).toBeGreaterThan(0);
    await expect.poll(() => snapshots.some((s) => s.agents.some((a) => a.id === first.id && a.status === 'working' && a.observed.status === 'thinking'))).toBe(true);
    await expect(row(page, first.id).locator('.st')).toHaveText('Thinking');
    await expect(field('observed-status')).toHaveText('Thinking it over');
    await expect(field('provider')).toHaveText('Claude Code');
    await expect(field('computer')).toHaveText('Fixture A');
    await expect(field('quality')).toHaveText(/^Observed\b/);
    await expect(field('source')).toHaveText('Hook');
    await expect(field('last-seen')).toHaveAttribute('data-timestamp', String(held.last));
    await expect(row(page, first.id).locator('.meta')).toContainText('Claude');
    await expect(row(page, first.id).locator('.meta')).toContainText('Fixture A');

    // Another actor causes a new full SSE snapshot; it must not refresh A's
    // receipt timestamp or pretend A sent new evidence.
    await post('Fixture B', 'UserPromptSubmit', { turn_id: 'other-turn' });
    const otherTurn = (s) => s.agents.some((a) => a.id === second.id && a.observed.turn_id === 'other-turn');
    await expect.poll(() => snapshots.some(otherTurn)).toBe(true);
    const unchanged = snapshots.findLast(otherTurn).agents.find((a) => a.id === first.id);
    expect(unchanged.last).toBe(held.last);
    expect((await state()).agents.find((a) => a.id === first.id).last).toBe(held.last);
    await expect(field('last-seen')).toHaveAttribute('data-timestamp', String(held.last));

    const parentName = await row(page, first.id).locator('.nm').innerText();
    await row(page, temp.id).click();
    await expect(field('parent')).toContainText(parentName);
    await expect(field('computer')).toHaveText('Fixture A');
    await post('Fixture A', 'SessionEnd');
    await expect(rows(page)).toHaveCount(1);
    await expect(row(page, second.id)).toBeVisible();
    expect(problems, 'real backend browser errors and unexpected browser requests').toEqual([]);
  } finally {
    lines.close();
    child.stdin.end();
    try { expect(await bounded(exited, 8_000, 'Backend cleanup timed out'), stderr).toBe(0); }
    catch (error) {
      child.kill('SIGTERM');
      await bounded(exited, 8_000, 'Backend did not stop after SIGTERM');
      throw error;
    }
  }
});
