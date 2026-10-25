import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, sep, extname } from 'node:path';
import { test as base, expect } from '@playwright/test';

export const EPOCH = Date.parse('2026-01-01T12:01:00Z');
const STATIC = fileURLToPath(new URL('../../static/', import.meta.url));
const CAST = { names: ['Alex', 'Blair', 'Casey', 'Drew', 'Ellis', 'Frankie', 'Gray', 'Harper', 'Indigo', 'Jules', 'Kit', 'Lane', 'Morgan'], manager: 'Manager', maintenance: 'Caretaker', says: ['Hello.'] };

export function agent(id, overrides = {}) {
  return { id, parent: null, temp: false, project: `project-${id}`, machine: 'Test computer',
    title: 'Synthetic session', assignment: 'Review the sample', status: 'working', task: 'typing',
    tool: 'Edit', detail: 'sample.txt', guess: false, since: EPOCH / 1000, last: EPOCH / 1000,
    tools: 1, log: [{ t: EPOCH / 1000, text: 'Edit sample.txt' }], ...overrides };
}
export function snapshot(agents = [], now = EPOCH / 1000, overrides = {}) {
  return { now, hooks: true, agents, ...overrides };
}

// No production server, transcript directory, hook endpoint or control HTTP route.
// The Node test process alone controls this loopback server's synthetic state.
async function startFixture() {
  const clients = new Set(), held = new Set(), sockets = new Set();
  let state, cast, blocked, connections, requests, sequence;
  const event = () => `id: ${sequence}\ndata: ${JSON.stringify(state)}\n\n`;
  const json = (res, body) => { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
  function activate(res) {
    held.delete(res);
    clients.add(res);
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' });
    res.write(`retry: 50\n${event()}`);
  }
  const server = createServer(async (req, res) => {
    try {
      const path = new URL(req.url, 'http://fixture.invalid').pathname;
      requests.push({ method: req.method, path });
      if (req.method !== 'GET') { res.writeHead(405); res.end(); return; }
      if (path === '/events') {
        connections++;
        res.on('close', () => { clients.delete(res); held.delete(res); });
        if (blocked) held.add(res); else activate(res);
        return;
      }
      if (path === '/state') { json(res, state); return; }
      if (path === '/names') { json(res, cast); return; }
      if (path === '/favicon.ico') { res.writeHead(204); res.end(); return; }
      const file = resolve(STATIC, path === '/' ? 'index.html' : `.${decodeURIComponent(path)}`);
      const type = { '.html': 'text/html', '.js': 'text/javascript', '.txt': 'text/plain' }[extname(file)];
      if (!file.startsWith(STATIC.endsWith(sep) ? STATIC : STATIC + sep) || !type) {
        res.writeHead(404); res.end(); return;
      }
      const data = await readFile(file);
      res.writeHead(200, { 'Content-Type': type, 'Cache-Control': 'no-store' }); res.end(data);
    } catch { if (!res.headersSent) res.writeHead(404); res.end(); }
  });
  server.on('connection', (socket) => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
  function reset() {
    for (const res of [...clients, ...held]) res.destroy();
    clients.clear(); held.clear();
    state = snapshot(); cast = structuredClone(CAST); blocked = false; connections = 0; requests = []; sequence = 0;
  }
  reset();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  return {
    url: `http://127.0.0.1:${server.address().port}`,
    reset,
    get requests() { return requests; },
    get connections() { return connections; },
    publish(next) { state = structuredClone(next); sequence++; for (const res of clients) res.write(event()); return sequence; },
    setCast(next) { cast = structuredClone(next); },
    hold() { blocked = true; },
    disconnect() { blocked = true; for (const res of clients) res.end(); clients.clear(); },
    release() { blocked = false; for (const res of [...held]) activate(res); },
    async close() {
      for (const socket of sockets) socket.destroy();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

export async function preparePage(page, url) {
  const problems = [];
  page.on('pageerror', (error) => problems.push(error.message));
  page.on('console', (message) => { if (message.type() === 'error') problems.push(message.text()); });
  await page.context().route('**/*', async (route) => {
    const target = new URL(route.request().url());
    if (target.origin === url && route.request().method() === 'GET') return route.continue();
    if (target.hostname === 'fonts.googleapis.com') return route.fulfill({ contentType: 'text/css', body: '' });
    problems.push(`Unexpected request: ${route.request().method()} ${target.origin}${target.pathname}`);
    return route.abort();
  });
  await page.clock.install({ time: EPOCH });
  await page.clock.pauseAt(EPOCH);
  await page.addInitScript(() => {
    let seed = 123456;
    Math.random = () => {
      seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  });
  return problems;
}

export const test = base.extend({
  fixtureServer: [async ({}, use) => {
    const fixture = await startFixture();
    try { await use(fixture); } finally { await fixture.close(); }
  }, { scope: 'worker' }],
  office: async ({ page, fixtureServer }, use, testInfo) => {
    fixtureServer.reset();
    const problems = await preparePage(page, fixtureServer.url);
    // Chromium's native SSE notification gives tests a delivery barrier without
    // replacing EventSource, adding production hooks or sleeping on real time.
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Network.enable');
    const received = new Set();
    cdp.on('Network.eventSourceMessageReceived', (event) => received.add(event.eventId));
    await use({
      ...fixtureServer,
      server: fixtureServer,
      async open(query = '?lite') {
        await page.goto(fixtureServer.url + '/' + query);
        await expect.poll(() => page.evaluate(() => !!window.tokenTally)).toBe(true);
        await expect.poll(() => fixtureServer.connections).toBeGreaterThan(0);
        expect(await page.evaluate(() => Date.now())).toBe(EPOCH);
      },
      async publish(agents, overrides = {}) {
        const now = await page.evaluate(() => Date.now() / 1000);
        const sequence = fixtureServer.publish(snapshot(agents, now, overrides));
        await expect.poll(() => received.has(String(sequence)), { message: 'native SSE delivery' }).toBe(true);
      },
    });
    const mutations = fixtureServer.requests.filter((req) => req.method !== 'GET');
    // A known product regression must never swallow a broken harness, browser
    // error or a forbidden request as its expected failure.
    if (problems.length || mutations.length) testInfo.expectedStatus = 'passed';
    expect(problems, 'browser errors and unexpected network requests').toEqual([]);
    expect(mutations).toEqual([]);
  },
});
export { expect };

export const row = (page, id) => page.locator(`#roster-list .row[data-id="${id}"]`);
export const rows = (page) => page.locator('#roster-list .row');
export const deskOwners = (page) => page.evaluate(() => window.tokenTally.deskSlots.map((desk) => desk.owner));
