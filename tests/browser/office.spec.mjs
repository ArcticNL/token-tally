import { test, expect, agent, snapshot, EPOCH, preparePage, row, rows, deskOwners } from './fixtures.mjs';

for (const query of ['', '?lite']) {
  test(`empty live office loads local modules ${query || 'normal'}`, async ({ page, office }) => {
    await office.open(query);
    await expect(page.locator('#conn')).toHaveText('Live · hooks on');
    await expect(page.getByRole('dialog', { name: 'Nobody on shift' })).toBeVisible();
    await expect(page.locator('#stats')).toContainText('0 on shift');
    const canvas = page.locator('#scene > canvas');
    await expect(canvas).toBeVisible();
    expect(await canvas.evaluate((node) => [node.width, node.height])).toEqual(query ? [1280, 766] : [2560, 1532]);
    await page.getByRole('dialog', { name: 'Nobody on shift' }).getByRole('button', { name: 'OK', exact: true }).click();
    await expect(page.locator('#empty')).toBeHidden();
    await page.locator('#demo').click();
    await expect(page.locator('#conn')).toHaveText('Demo shift');
    await expect(rows(page)).toHaveCount(3);
  });
}

test('full snapshots update the roster without duplicating arrivals', async ({ page, office }) => {
  const staff = [agent('one'), agent('two')];
  office.server.publish(snapshot(staff));
  await office.open();
  await expect(rows(page)).toHaveCount(2);
  await expect(row(page, 'one').locator('.meta')).toContainText('Test computer');
  await expect(row(page, 'one').locator('.sub')).toContainText('project-one');
  const memos = await page.locator('#memos').allTextContents();
  await office.publish(staff);
  await expect(rows(page)).toHaveCount(2);
  expect(await page.locator('#memos').allTextContents()).toEqual(memos);
  await office.publish([staff[1]]);
  await expect(rows(page)).toHaveCount(1);
  await expect(row(page, 'two')).toBeVisible();
});

test('parents and subagents group correctly even when children arrive first', async ({ page, office }) => {
  office.server.publish(snapshot([agent('child-a', { parent: 'parent', temp: true }), agent('parent'), agent('child-b', { parent: 'parent', temp: true })]));
  await office.open();
  await expect(rows(page)).toHaveCount(3);
  expect(await rows(page).evaluateAll((nodes) => nodes.map((node) => node.dataset.id))).toEqual(['parent', 'child-a', 'child-b']);
  await expect(row(page, 'child-a')).toContainText('Temp');
  const parentName = await row(page, 'parent').locator('.nm').innerText();
  await row(page, 'child-a').click();
  await expect(page.locator('[data-field="position"]')).toHaveText('Temp');
  await expect(page.locator('[data-field="parent"]')).toHaveText(parentName);
});

test('more than twelve staff stay selectable with twelve unique desk owners', async ({ page, office }) => {
  office.server.publish(snapshot(Array.from({ length: 13 }, (_, i) => agent(`staff-${i}`))));
  await office.open();
  await expect(rows(page)).toHaveCount(13);
  const owners = (await deskOwners(page)).filter(Boolean);
  expect(owners).toHaveLength(12);
  expect(new Set(owners).size).toBe(12);
  await row(page, 'staff-12').click();
  await expect(page.locator('#file')).toBeVisible();
  await expect(page.locator('#file-body')).toContainText('project-staff-12');
});

test('click, Enter and Space select the same file and snapshots preserve selection', async ({ page, office }) => {
  office.server.publish(snapshot([agent('selected')]));
  await office.open();
  await row(page, 'selected').click();
  await expect(page.locator('#file-body')).toContainText('project-selected');
  await page.getByRole('button', { name: 'Close personnel file' }).click();
  await expect(page.locator('#file')).toBeHidden();
  await row(page, 'selected').press('Enter');
  await expect(page.locator('#file')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#file')).toBeHidden();
  await row(page, 'selected').press('Space');
  await office.publish([agent('selected', { assignment: 'Updated sample assignment', tools: 2 })]);
  await expect(page.locator('#file-body')).toContainText('Updated sample assignment');
  await expect(row(page, 'selected')).toHaveClass(/sel/);
});

test('native SSE reconnect keeps the roster and applies the latest complete snapshot', async ({ page, office }) => {
  office.server.publish(snapshot([agent('before')]));
  await office.open();
  await expect(row(page, 'before')).toBeVisible();
  const count = office.server.connections;
  office.server.disconnect();
  await expect(page.locator('#conn')).toHaveText('Server offline');
  await expect(row(page, 'before')).toBeVisible();
  await expect.poll(() => office.server.connections).toBeGreaterThan(count);
  await page.clock.runFor(3100);
  await expect(page.locator('#demo')).toHaveAttribute('aria-pressed', 'false');
  office.server.publish(snapshot([agent('after')], (EPOCH + 3100) / 1000));
  office.server.release();
  await expect(page.locator('#conn')).toHaveText('Live · hooks on');
  await expect(rows(page)).toHaveCount(1);
  await expect(row(page, 'after')).toBeVisible();
});

test('only an initial missing connection automatically starts demo after three seconds', async ({ page, office }) => {
  office.server.hold();
  await office.open();
  await page.clock.runFor(2999);
  await expect(page.locator('#demo')).toHaveAttribute('aria-pressed', 'false');
  await page.clock.runFor(1);
  await expect(page.locator('#demo')).toHaveAttribute('aria-pressed', 'true');
  await expect(rows(page)).toHaveCount(3);
});

test('demo hides live updates and returning to live uses the latest snapshot', async ({ page, office }) => {
  office.server.publish(snapshot([agent('before')]));
  await office.open();
  await expect(row(page, 'before')).toBeVisible();
  await page.locator('#demo').click();
  await expect(page.locator('#conn')).toHaveText('Demo shift');
  await expect(rows(page)).toHaveCount(3);
  await office.publish([agent('after')]);
  // publish waits for native SSE delivery, so this checks processed input.
  await expect(row(page, 'after')).toHaveCount(0);
  await expect(rows(page)).toHaveCount(3);
  await page.locator('#demo').click();
  await expect(row(page, 'after')).toBeVisible();
  await expect(rows(page)).toHaveCount(1);
});

test('fixed clock and seed reproduce demo output in fresh browser contexts', async ({ page, browser, office }) => {
  await office.open('?demo&lite');
  await expect(rows(page)).toHaveCount(3);
  expect(await rows(page).evaluateAll((nodes) => nodes.map((node) => node.dataset.id))).toEqual([0, 1, 2].map((i) => `demo-${i}-${EPOCH}`));
  await page.clock.runFor(6000);
  const first = await page.locator('#roster-list').innerText();
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: 'en-GB', timezoneId: 'UTC', serviceWorkers: 'block' });
  try {
    const second = await context.newPage();
    const problems = await preparePage(second, office.server.url);
    await second.goto(office.server.url + '/?demo&lite');
    await expect(rows(second)).toHaveCount(3);
    expect(await second.evaluate(() => Date.now())).toBe(EPOCH);
    await second.clock.runFor(6000);
    expect(await second.locator('#roster-list').innerText()).toBe(first);
    expect(problems).toEqual([]);
  } finally { await context.close(); }
});

test('lite preference survives navigation and the explicit URL overrides it', async ({ page, office }) => {
  office.server.publish(snapshot([agent('one')]));
  await office.open('');
  await page.locator('#start').click();
  await page.getByRole('button', { name: 'Low quality mode', exact: true }).click();
  await expect(page).toHaveURL(/lite=1/);
  await expect(page.locator('[data-cmd="lite"]')).toHaveText('High quality mode');
  await page.goto(office.server.url);
  await expect(page.locator('[data-cmd="lite"]')).toHaveText('High quality mode');
  await page.goto(office.server.url + '/?lite=0');
  await expect(page.locator('[data-cmd="lite"]')).toHaveText('Low quality mode');
});

for (const initialSource of ['live', 'demo']) {
  test(`quality reload preserves the source chosen after opening ${initialSource}`, async ({ page, office }) => {
    office.server.publish(snapshot([agent('source-choice')]));
    await office.open(initialSource === 'demo' ? '?demo&lite&keep=value' : '?lite&keep=value');
    await page.locator('#demo').click();
    const demo = initialSource === 'live';
    await expect(page.locator('#demo')).toHaveAttribute('aria-pressed', String(demo));
    for (const lite of [false, true]) {
      await page.locator('#start').click();
      await page.getByRole('button', { name: lite ? 'Low quality mode' : 'High quality mode', exact: true }).click();
      await expect(page).toHaveURL((url) => url.searchParams.get('lite') === (lite ? '1' : '0'));
      await expect(page.locator('#conn')).toHaveText(demo ? 'Demo shift' : 'Live · hooks on');
      await expect(page.locator('#demo')).toHaveAttribute('aria-pressed', String(demo));
      await expect(rows(page)).toHaveCount(demo ? 3 : 1);
      if (!demo) await expect(row(page, 'source-choice')).toBeVisible();
      const url = new URL(page.url());
      expect(url.searchParams.has('demo')).toBe(demo);
      expect(url.searchParams.get('keep')).toBe('value');
    }
  });
}

test('tablet viewport keeps roster and personnel selection usable', async ({ page, office }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  office.server.publish(snapshot([agent('tablet')]));
  await office.open();
  await row(page, 'tablet').click();
  await expect(page.locator('#file')).toBeVisible();
  await expect(page.locator('#file-body')).toContainText('project-tablet');
  await page.getByRole('button', { name: 'Close personnel file' }).click();
  await page.getByRole('button', { name: 'Minimise roster' }).click();
  await expect(row(page, 'tablet')).toBeHidden();
  await page.getByRole('button', { name: 'Minimise roster' }).click();
  await expect(row(page, 'tablet')).toBeVisible();
});

test('scheduled cast refresh keeps the selected personnel file', async ({ page, office }) => {
  office.server.publish(snapshot([agent('cast')]));
  await office.open();
  await row(page, 'cast').click();
  await expect(page.locator('#file')).toBeVisible();
  office.server.setCast({ names: ['New sample name'], manager: 'Manager', maintenance: 'Caretaker', says: ['Hello.'] });
  // Deliberately simulate waking after a pause: only this refresh needs to fire,
  // unlike the demo/animation scenarios that run every timer with runFor().
  await page.clock.fastForward(30_000);
  await expect(row(page, 'cast')).toContainText('New sample name');
  await expect(row(page, 'cast')).toHaveClass(/sel/);
  await expect(page.locator('#file-title')).toHaveText('Personnel File: New sample name');
});

test('replacement snapshot immediately reuses the departing staff desk @pr13', async ({ page, office }) => {
  const staff = Array.from({ length: 12 }, (_, i) => agent(`desk-${i}`));
  office.server.publish(snapshot(staff));
  await office.open();
  await expect(rows(page)).toHaveCount(12);
  expect((await deskOwners(page)).filter(Boolean)).toHaveLength(12);
  await office.publish([agent('newcomer'), ...staff.slice(1)]);
  await expect(row(page, 'newcomer')).toBeVisible();
  await expect(rows(page)).toHaveCount(12);
  expect(await deskOwners(page)).toContain('newcomer');
});

test('roster focus survives periodic HUD refresh and an identical snapshot @pr13', async ({ page, office }) => {
  const staff = [agent('focused'), agent('other')];
  office.server.publish(snapshot(staff));
  await office.open();
  await expect(rows(page)).toHaveCount(2);
  await row(page, 'focused').focus();
  await expect(row(page, 'focused')).toBeFocused();
  await page.clock.runFor(1200);
  await expect(row(page, 'focused')).toBeVisible();
  await expect(row(page, 'focused')).toBeFocused();
  await office.publish(staff);
  await expect(row(page, 'focused')).toBeFocused();
});
