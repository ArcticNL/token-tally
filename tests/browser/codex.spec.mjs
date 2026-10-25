import { test, expect, preparePage, row, rows } from './fixtures.mjs';
import { startChain } from './codex-chain.mjs';

const EVENTS = ['SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'PermissionRequest',
  'Stop', 'SubagentStart', 'SubagentStop', 'SessionEnd', 'Interrupt', 'PreCompact', 'PostCompact'];

test('installed Codex hooks reach the browser with observed status and honest actor attribution @pr15', async ({ page }) => {
  test.setTimeout(90_000);
  const chain = startChain();
  try {
    const ready = await chain.ready();
    expect(ready).toMatchObject({ type: 'ready', provider: 'codex', payload_version: 2, machine: 'Browser fixture', validated: true });
    expect(ready.hooks).toEqual([...EVENTS].sort());
    expect(ready.client_id).toMatch(/^[0-9a-f-]{36}$/);
    const problems = await preparePage(page, ready.url);
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Network.enable');
    const snapshots = [];
    cdp.on('Network.eventSourceMessageReceived', (event) => snapshots.push(JSON.parse(event.data)));
    const root = (s) => s.agents.find((a) => a.identity_quality === 'scoped' && !a.temp);
    const unknown = (s) => s.agents.find((a) => a.identity_quality === 'unattributed');
    const child = (s) => s.agents.find((a) => a.agent_id === 'sample-child');
    const field = (name) => page.locator(`#file-body dd[data-field="${name}"]`);
    const markers = ['PRIVATE_PROMPT_FIXTURE', 'PRIVATE_PATCH_FIXTURE', 'PRIVATE_RESULT_FIXTURE'];
    const send = async (hook_event_name, fields, accepted) => {
      const before = snapshots.length;
      const result = await chain.hook({ hook_event_name, session_id: 'sample-session', turn_id: 'sample-turn',
        cwd: '/work/sample-project', ...fields });
      expect(result).toMatchObject({ event: hook_event_name, exit_code: 0, stdout: '{}\n', stderr: '', neutral: true });
      // Neutral relay output is also returned on delivery failure. A new real
      // SSE snapshot satisfying the event's meaning proves actual ingestion.
      await expect.poll(() => snapshots.slice(before).some(accepted)).toBe(true);
      return snapshots.slice(before).findLast(accepted);
    };

    await page.goto(ready.url + '/?lite');
    await expect(page.locator('#conn')).toHaveText('Live');
    await expect.poll(() => snapshots.length).toBeGreaterThan(0);
    expect(snapshots[0].agents).toEqual([]);

    let state = await send('SessionStart', { source: 'startup' }, (s) => s.agents.length === 1);
    const rootId = root(state).id;
    expect(root(state)).toMatchObject({ provider: 'codex', client_id: ready.client_id, machine: ready.machine,
      session_id: 'sample-session', identity_quality: 'scoped', tools: 0 });
    await send('UserPromptSubmit', { prompt: markers[0] }, (s) => root(s)?.observed.status === 'thinking');

    state = await send('SubagentStart', { agent_id: 'sample-child' }, (s) => !!child(s));
    const childId = child(state).id;
    expect(child(state).parent).toBeNull();
    await row(page, childId).click();
    await expect(field('parent')).toHaveText('Unknown parent');

    const call = { tool_name: 'apply_patch', tool_use_id: 'sample-call', tool_input: { patch: markers[1] } };
    state = await send('PreToolUse', call, (s) => unknown(s)?.observed.status === 'working');
    const unknownId = unknown(state).id;
    expect(unknown(state)).toMatchObject({ provider: 'codex', client_id: ready.client_id, machine: ready.machine,
      identity_quality: 'unattributed', temp: false, parent: null, tools: 1,
      observed: { source: 'hook', confidence: 'observed', correlation: 'native' } });
    expect(new Set([rootId, childId, unknownId]).size).toBe(3);
    expect(root(state).tools).toBe(0);
    await expect(rows(page)).toHaveCount(3);
    await row(page, unknownId).click();
    await expect(field('provider')).toHaveText('Codex');
    await expect(field('computer')).toHaveText('Browser fixture');
    await expect(field('position')).toHaveText('Unattributed worker');
    await expect(field('parent')).toHaveText('Unknown parent');
    await expect(field('identity')).toHaveText('Unattributed actor');
    await expect(field('quality')).toHaveText('Observed');
    await expect(field('source')).toHaveText('Hook');
    await expect(field('correlation')).toHaveText('Native turn');
    await expect(field('tools')).toHaveText('1');
    await expect(row(page, unknownId).locator('.st')).toHaveText('Working');

    // This wait has turn scope, so finishing one call cannot clear it.
    state = await send('PermissionRequest', { tool_name: 'apply_patch' }, (s) => unknown(s)?.observed.status === 'waiting');
    const lastReceipt = unknown(state).last;
    expect(lastReceipt).toBeGreaterThan(0);
    await expect(row(page, unknownId).locator('.st')).toHaveText('Needs you');
    await expect(field('last-seen')).toHaveAttribute('data-timestamp', String(lastReceipt));
    for (const [event, fields] of [['PreCompact', {}], ['PostCompact', {}], ['SessionStart', { source: 'compact' }]]) {
      const previousRootReceipt = root(state).last;
      state = await send(event, fields, (s) => root(s)?.last > previousRootReceipt);
      expect(unknown(state).observed.status).toBe('waiting');
      expect(unknown(state).last).toBe(lastReceipt);
      await expect(row(page, unknownId).locator('.st')).toHaveText('Needs you');
      await expect(field('last-seen')).toHaveAttribute('data-timestamp', String(lastReceipt));
    }
    state = await send('PostToolUse', { ...call, tool_response: markers[2] }, (s) => unknown(s)?.last > lastReceipt);
    expect(unknown(state).observed.status).toBe('waiting');
    expect(unknown(state).tools).toBe(1);
    await expect(row(page, unknownId).locator('.st')).toHaveText('Needs you');

    state = await send('Stop', {}, (s) => root(s)?.observed.status === 'idle' && unknown(s)?.observed.status === 'idle');
    expect(child(state).observed.status).toBe('thinking');
    expect(root(state).tools).toBe(0);
    await expect(row(page, unknownId).locator('.st')).toHaveText('Idle');
    await expect(field('observed-status')).toHaveText('Idle');
    await expect(row(page, childId).locator('.st')).toHaveText('Thinking');
    for (const marker of markers) {
      expect(JSON.stringify(snapshots)).not.toContain(marker);
      await expect(page.locator('body')).not.toContainText(marker);
    }
    await send('SessionEnd', {}, (s) => s.agents.length === 0);
    await expect(rows(page)).toHaveCount(0);
    expect(problems, 'real Codex chain browser errors and unexpected browser requests').toEqual([]);
  } finally { await chain.close(); }
});
