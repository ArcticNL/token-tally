import { test, expect, agent, snapshot, EPOCH, row, rows } from './fixtures.mjs';

// Synthetic fixtures for the public observation contract. These browser
// cases exercise UI interpretation; they do not run a relay or provider session.
// IDs match the public opaque tuple encoding, but the UI must not decode them.
function opaqueId(provider, clientId, sessionId, agentId = null, unattributed = false) {
  return 'tt1.' + Buffer.from(JSON.stringify([
    provider, clientId, sessionId, unattributed ? ['unknown'] : agentId === null ? ['main'] : ['agent', agentId],
  ])).toString('base64url');
}

function observedAgent({
  provider = 'claude', clientId = 'client-a', sessionId = 'shared-native-session',
  agentId = null, unattributed = false, ...overrides
} = {}) {
  const value = agent(opaqueId(provider, clientId, sessionId, agentId, unattributed), {
    provider, client_id: clientId, session_id: sessionId, agent_id: agentId,
    identity_quality: unattributed ? 'unattributed' : clientId.startsWith('legacy-') ? 'legacy' : 'scoped',
    machine: 'Workstation A', temp: agentId !== null || unattributed,
    ...overrides,
  });
  value.observed = {
    status: value.status, task: value.task, tool: value.tool, detail: value.detail,
    since: value.since, source: 'hook', confidence: 'observed', correlation: 'native',
    turn_id: 'native-turn-1', limited: false, ...overrides.observed,
  };
  return value;
}

const field = (page, name) => page.locator(`#file-body dd[data-field="${name}"]`);
const fieldLabel = (page, name) => field(page, name).locator('xpath=preceding-sibling::dt[1]');

test('badge numbers distinguish opaque identities and survive cast, state and page refreshes', async ({ page, office }) => {
  const staff = [
    observedAgent(),
    observedAgent({ clientId: 'client-b' }),
    observedAgent({ sessionId: 'another-native-session' }),
    observedAgent({ agentId: 'child-a' }),
    observedAgent({ agentId: 'child-b' }),
    observedAgent({ unattributed: true }),
  ];
  // These identities share the entire old visible prefix.
  expect(new Set(staff.map((person) => person.id.slice(0, 13))).size).toBe(1);
  office.server.publish(snapshot(staff));
  await office.open();
  const badges = new Map();
  for (const person of staff) {
    await row(page, person.id).click();
    const badge = await field(page, 'badge').innerText();
    expect(badge).not.toContain(person.id);
    expect(badge).not.toContain(person.client_id);
    expect(badge).not.toContain(person.session_id);
    badges.set(person.id, badge);
  }
  expect(new Set(badges.values()).size).toBe(staff.length);

  office.server.setCast({ names: ['New sample name'], manager: 'Manager' });
  await page.evaluate(() => window.tokenTally.loadNames());
  await office.publish(staff.map((person) => ({ ...person, tools: 10, status: 'idle' })));
  for (const person of staff) {
    await row(page, person.id).click();
    await expect(field(page, 'badge')).toHaveText(badges.get(person.id));
  }
  await page.reload();
  for (const person of staff) {
    await row(page, person.id).click();
    await expect(field(page, 'badge')).toHaveText(badges.get(person.id));
  }
});

test('later parent metadata updates grouping without replacing the selected worker @pr15', async ({ page, office }) => {
  const parent = observedAgent(), child = observedAgent({ agentId: 'later-child', temp: false });
  office.server.publish(snapshot([child, parent]));
  await office.open();
  await row(page, child.id).press('Enter');
  await office.publish([{ ...child, temp: true, parent: parent.id }, parent]);
  expect(await rows(page).evaluateAll((nodes) => nodes.map((node) => node.dataset.id))).toEqual([parent.id, child.id]);
  await expect(row(page, child.id).locator('.pill')).toBeVisible();
  await expect(row(page, child.id).locator('.parent')).toBeVisible();
  await expect(row(page, child.id)).toBeFocused();
  await expect(row(page, child.id)).toHaveAttribute('aria-pressed', 'true');
  await expect(field(page, 'parent')).toHaveText(await row(page, parent.id).locator('.name').innerText());
});

test('provider, computer and parent use explicit metadata and opaque identities @pr15', async ({ page, office }) => {
  // The same native session ID represents distinct people across clients/providers.
  const claudeA = observedAgent();
  const claudeB = observedAgent({ clientId: 'client-b', machine: 'Workstation B' });
  const codexA = observedAgent({ provider: 'codex', observed: { source: 'transcript' } });
  const child = observedAgent({ agentId: 'child', parent: claudeA.id });
  const unknownParent = observedAgent({ unattributed: true, parent: null });
  const absentParent = observedAgent({ agentId: 'absent-parent', parent: opaqueId('claude', 'client-a', 'absent-session') });
  const future = observedAgent({ provider: 'future-provider', clientId: 'client-b', machine: 'Workstation B' });
  const staff = [child, claudeA, claudeB, codexA, unknownParent, absentParent, future];
  office.server.publish(snapshot(staff));
  await office.open();
  await expect(rows(page)).toHaveCount(staff.length);
  expect(new Set(await rows(page).evaluateAll((nodes) => nodes.map((node) => node.dataset.id))).size).toBe(staff.length);

  await row(page, claudeB.id).click();
  await expect(field(page, 'provider')).toContainText(/claude/i);
  await expect(field(page, 'computer')).toHaveText('Workstation B');
  await expect(field(page, 'identity')).toContainText(/scoped/i);
  await expect(row(page, claudeB.id).locator('.meta')).toContainText(/claude/i);
  await expect(row(page, claudeB.id).locator('.meta')).toContainText('Workstation B');

  await row(page, codexA.id).click();
  await expect(field(page, 'provider')).toContainText(/codex/i);
  await expect(field(page, 'computer')).toHaveText('Workstation A');
  await expect(field(page, 'source')).toContainText(/transcript/i);
  const parentName = await row(page, claudeA.id).locator('.name').innerText();
  await row(page, child.id).click();
  await expect(field(page, 'parent')).toContainText(parentName);

  // A leaving avatar is still in the actor map but is no longer an active parent.
  await office.publish(staff.filter((person) => person.id !== claudeA.id));
  await expect(field(page, 'parent')).toHaveText(/parent not on shift/i);
  await expect(row(page, child.id)).toHaveAttribute('aria-pressed', 'true');
  await row(page, unknownParent.id).click();
  await expect(field(page, 'parent')).toHaveText(/unknown parent/i);
  await expect(field(page, 'identity')).toContainText(/unattributed/i);
  await row(page, absentParent.id).click();
  await expect(field(page, 'parent')).toHaveText(/parent not on shift/i);
  await row(page, future.id).click();
  await expect(field(page, 'provider')).toHaveText(/future-provider/i);
});

test('missing labels stay unknown even when opaque IDs and client IDs suggest values @pr15', async ({ page, office }) => {
  const value = observedAgent({ provider: 'codex', clientId: 'identifier-is-not-a-computer-name', machine: '', tool: 'mcp__codex__tool' });
  delete value.provider;
  delete value.observed.confidence;
  office.server.publish(snapshot([value]));
  await office.open();
  await row(page, value.id).click();
  await expect(field(page, 'provider')).toHaveText(/unknown provider/i);
  await expect(field(page, 'computer')).toHaveText(/unknown computer/i);
  await expect(field(page, 'computer')).not.toContainText(value.client_id);
  await expect(field(page, 'quality')).toHaveText(/unknown/i);
  await expect(row(page, value.id).locator('.meta')).toContainText(/unknown provider/i);
  await expect(row(page, value.id).locator('.meta')).toContainText(/unknown computer/i);
});

test('legacy snapshots retain inferred status and explicitly label last activity @pr15', async ({ page, office }) => {
  const legacy = agent('legacy-codex-looking-id', { machine: '', tool: 'mcp__codex__tool', last: EPOCH / 1000 - 90 });
  office.server.publish(snapshot([legacy]));
  await office.open();
  await row(page, legacy.id).click();
  await expect(field(page, 'provider')).toHaveText(/unknown provider/i);
  await expect(field(page, 'computer')).toHaveText(/unknown computer/i);
  await expect(field(page, 'quality')).toHaveText(/unknown/i);
  await expect(fieldLabel(page, 'last-seen')).toHaveText(/last activity/i);
  await expect(field(page, 'last-seen')).toContainText(/1m\s*30s/i);

  await office.publish([{ ...legacy, guess: true, status: 'waiting', tool: 'Bash' }]);
  await expect(field(page, 'quality')).toContainText(/inferred/i);
  await expect(row(page, legacy.id).locator('.meta')).toContainText(/inferred/i);
  await expect(row(page, legacy.id)).toHaveAttribute('aria-pressed', 'true');
});

test('observed status and detail take precedence over held animation fields @pr15', async ({ page, office }) => {
  const held = observedAgent({
    status: 'working', task: 'typing', detail: 'held-edit.txt',
    observed: { status: 'idle', task: '', tool: '', detail: '', since: EPOCH / 1000 },
  });
  office.server.publish(snapshot([held]));
  await office.open();
  await row(page, held.id).click();
  await expect(row(page, held.id).locator('.st')).toHaveText(/^idle$/i);
  await expect(page.locator('#stats')).toContainText('1 idle');
  await expect(field(page, 'observed-status')).toContainText(/idle/i);
  await expect(row(page, held.id).locator('.sub')).not.toContainText('held-edit.txt');
  await page.clock.runFor(100);
  expect(await page.evaluate((id) => {
    const actor = window.tokenTally.actors.get(id);
    return { status: actor.info.status, anim: actor.anim };
  }, held.id)).toEqual({ status: 'working', anim: 'type' });

  await office.publish([{ ...held, observed: { ...held.observed, status: 'working', task: 'files', tool: 'Read', detail: 'observed-file.txt' } }]);
  await expect(row(page, held.id).locator('.sub')).toContainText('observed-file.txt');
  await expect(row(page, held.id).locator('.sub')).not.toContainText('held-edit.txt');
  await expect(row(page, held.id).locator('.st')).toHaveText(/^working$/i);
  await expect(field(page, 'observed-status')).toContainText('observed-file.txt');
  await office.publish([{ ...held, observed: { confidence: 'observed', source: 'hook', since: null } }]);
  await expect(row(page, held.id).locator('.st')).toHaveText('Unknown');
  await expect(field(page, 'observed-status')).toHaveText('Unknown status');
  await expect(field(page, 'duration')).toHaveText('Unknown');
  await expect(row(page, held.id).locator('.sub')).not.toContainText('held-edit.txt');
});

test('last seen uses top-level receipt time and survives repeat snapshots and reconnect @pr15', async ({ page, office }) => {
  const value = observedAgent({ last: EPOCH / 1000 - 90 });
  office.server.publish(snapshot([value]));
  await office.open();
  await row(page, value.id).click();
  await expect(fieldLabel(page, 'last-seen')).toHaveText(/last seen/i);
  await expect(field(page, 'last-seen')).toContainText(/1m\s*30s/i);
  await expect(field(page, 'last-seen')).toHaveAttribute('data-timestamp', String(value.last));
  await page.clock.runFor(1100);
  await office.publish([value]);
  await expect(field(page, 'last-seen')).toContainText(/1m\s*31s/i);
  await expect(row(page, value.id).locator('.meta')).toContainText(/1m\s*31s/i);
  const previous = await field(page, 'last-seen').innerText();

  const connections = office.server.connections;
  office.server.disconnect();
  await expect(page.locator('#conn')).toHaveText('Server offline');
  await expect.poll(() => office.server.connections).toBeGreaterThan(connections);
  office.server.publish(snapshot([value], (EPOCH + 1100) / 1000));
  office.server.release();
  await expect(page.locator('#conn')).toHaveText('Live · hooks on');
  await expect(field(page, 'last-seen')).toHaveText(previous);
  await expect(field(page, 'last-seen')).toHaveAttribute('data-timestamp', String(value.last));
  await expect(row(page, value.id)).toHaveAttribute('aria-pressed', 'true');
  await page.clock.runFor(2000);
  await office.publish([value]);
  await expect(field(page, 'last-seen')).toContainText(/1m\s*33s/i);
});

test('status confidence stays independent of identity, correlation and limited evidence @pr15', async ({ page, office }) => {
  const value = observedAgent({ clientId: 'legacy-machine:Workstation A', guess: true });
  office.server.publish(snapshot([value]));
  await office.open();
  await row(page, value.id).click();
  // Explicit observed confidence wins over the legacy flat guess flag.
  await expect(field(page, 'quality')).toContainText(/observed/i);
  await expect(field(page, 'correlation')).toContainText(/native/i);
  await expect(field(page, 'identity')).toContainText(/legacy/i);

  const inferred = { ...value, observed: {
    ...value.observed, confidence: 'inferred', source: 'transcript', correlation: 'ambiguous', limited: true,
  } };
  await office.publish([inferred]);
  await expect(field(page, 'quality')).toContainText(/inferred/i);
  await expect(field(page, 'correlation')).toContainText(/ambiguous/i);
  await expect(field(page, 'identity')).toContainText(/legacy/i);
  await expect(field(page, 'source')).toContainText(/transcript/i);
  await expect(page.locator('#file-body')).toContainText(/limited/i);
  await expect(row(page, value.id).locator('.meta')).toContainText(/inferred/i);

  const unknown = { ...value, guess: false, observed: {
    ...value.observed, correlation: 'partial', turn_id: null,
  } };
  delete unknown.observed.confidence;
  await office.publish([unknown]);
  await expect(field(page, 'quality')).toHaveText(/unknown/i);
  await expect(field(page, 'correlation')).toContainText(/partial/i);
  await expect(field(page, 'identity')).toContainText(/legacy/i);
  await expect(row(page, value.id)).toHaveAttribute('aria-pressed', 'true');
});

test('provider metadata is literal text and stays inside tablet panels @pr15', async ({ page, office }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  const payload = '<img src="invalid" onerror="window.metadataExecuted=true">';
  const provider = 'future-provider-' + payload + '-'.repeat(200);
  const machine = 'Computer <b>sample</b> & "quoted" ' + 'LongUnbrokenLabel'.repeat(24);
  const source = '<svg onload="window.metadataExecuted=true">';
  const value = observedAgent({ provider, machine, observed: { source, detail: payload } });
  office.server.publish(snapshot([value]));
  await office.open();
  await row(page, value.id).click();
  await expect(field(page, 'provider')).toHaveText(provider);
  await expect(field(page, 'computer')).toHaveText(machine);
  await expect(field(page, 'source')).toHaveText(source);
  await expect(row(page, value.id).locator('.sub')).toContainText(payload);
  await expect(page.locator('#roster-list img, #roster-list svg, #file-body img, #file-body svg')).toHaveCount(0);
  expect(await page.evaluate(() => window.metadataExecuted)).toBeUndefined();
  expect(await page.evaluate(() => {
    const within = (element, container) => {
      const box = element.getBoundingClientRect(), bounds = container.getBoundingClientRect();
      return box.left >= bounds.left - 1 && box.right <= bounds.right + 1;
    };
    const list = document.querySelector('#roster-list'), file = document.querySelector('#file-body');
    return {
      rows: [...list.querySelectorAll('.row')].every((element) => within(element, list)),
      metadata: [...list.querySelectorAll('.meta')].every((element) => within(element, list)),
      fields: [...file.querySelectorAll('dd[data-field]')].every((element) => within(element, file)),
      viewport: document.documentElement.scrollWidth <= innerWidth,
    };
  })).toEqual({ rows: true, metadata: true, fields: true, viewport: true });
});

test('missing and future receipt times never render invalid elapsed values @pr15', async ({ page, office }) => {
  const missing = observedAgent();
  delete missing.last;
  office.server.publish(snapshot([missing]));
  await office.open();
  await row(page, missing.id).click();
  await expect(field(page, 'last-seen')).toHaveText(/unknown|unavailable/i);
  for (const last of [null, 'not a timestamp', -1]) {
    await office.publish([{ ...missing, last }]);
    await expect(field(page, 'last-seen')).toHaveText('Unknown');
    await expect(field(page, 'last-seen')).not.toHaveAttribute('data-timestamp');
  }
  await office.publish([{ ...missing, last: EPOCH / 1000 + 60 }]);
  await expect(field(page, 'last-seen')).toHaveText(/unknown|unavailable|0s|just now/i);
  await expect(field(page, 'last-seen')).not.toContainText(/NaN|Infinity|-\d/);
});

test('demo metadata is explicitly simulated @pr15', async ({ page, office }) => {
  await office.open('?demo&lite');
  await expect(rows(page)).toHaveCount(3);
  await rows(page).first().click();
  await expect(rows(page).first().locator('.meta')).toContainText(/simulated/i);
  await expect(field(page, 'quality')).toHaveText(/simulated/i);
});
