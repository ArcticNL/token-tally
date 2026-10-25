import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { existsSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { test, expect, preparePage } from './fixtures.mjs';
const REPO = fileURLToPath(new URL('../../', import.meta.url));

async function bounded(promise, ms, message) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), ms); })]);
  } finally { clearTimeout(timer); }
}

test('real Python backend serves the office module graph and initial SSE snapshot', async ({ page }) => {
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
    const scripts = new Map();
    page.on('response', (response) => {
      const path = new URL(response.url()).pathname;
      if (path.endsWith('.js')) scripts.set(path, { status: response.status(), type: response.headers()['content-type'] });
    });
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Network.enable');
    let initial;
    cdp.on('Network.eventSourceMessageReceived', (event) => { initial = JSON.parse(event.data); });
    const names = page.waitForResponse(url + '/names');
    const events = page.waitForResponse(url + '/events');
    await page.goto(url + '/?lite');
    await expect(page.locator('#conn')).toHaveText('Live');
    await expect(page.getByRole('dialog', { name: 'Nobody on shift' })).toBeVisible();
    await expect(page.locator('#stats')).toContainText('0 on shift');
    await expect.poll(() => initial?.agents).toEqual([]);
    expect(typeof initial.now).toBe('number');
    expect((await events).headers()['content-type']).toMatch(/^text\/event-stream/);
    expect((await (await names).json()).names).toEqual(['Sample Worker']);
    expect(await page.evaluate(() => ['actors', 'deskSlots', 'camera', 'controls', 'loadNames', 'maintenance', 'crew', 'advance'].every((key) => key in window.tokenTally))).toBe(true);
    const modules = join(REPO, 'static', 'office');
    const expected = ['/office.js', '/vendor/three.module.js'];
    if (existsSync(modules)) expected.push(...readdirSync(modules).filter((name) => name.endsWith('.js')).map((name) => `/office/${name}`));
    for (const path of expected) expect(scripts.has(path), `${path} loaded by the entrypoint`).toBe(true);
    for (const [path, result] of scripts) {
      expect(result.status, path).toBe(200);
      expect(result.type, path).toMatch(/^(text|application)\/javascript\b/);
    }
    await page.clock.runFor(50);
    await expect(page.locator('#scene > canvas')).toBeVisible();
    expect(problems, 'real backend browser errors and unexpected requests').toEqual([]);
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
