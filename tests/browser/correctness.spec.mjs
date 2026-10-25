import { test, expect, agent, snapshot, row, rows, deskOwners } from './fixtures.mjs';

for (const query of ['', '?lite']) {
  for (const newcomerFirst of [true, false]) {
    test(`replacement keeps survivors' desks ${query || 'normal'} newcomerFirst=${newcomerFirst} @pr13`, async ({ page, office }) => {
      const staff = Array.from({ length: 12 }, (_, i) => agent(`desk-${i}`));
      office.server.publish(snapshot(staff));
      await office.open(query);
      await expect(rows(page)).toHaveCount(12);
      const before = await deskOwners(page);
      const next = newcomerFirst ? [agent('newcomer'), ...staff.slice(1)] : [...staff.slice(1), agent('newcomer')];
      await office.publish(next);
      await expect(row(page, 'newcomer')).toBeVisible();
      const after = await deskOwners(page);
      expect(after).toEqual(before.map((id) => id === 'desk-0' ? 'newcomer' : id));
      await office.publish(next);
      expect(await deskOwners(page)).toEqual(after);
      expect(await page.evaluate(() => window.tokenTally.actors.get('desk-0')?.desk ?? null)).toBeNull();
    });
  }
}

test('an existing overflow worker gets the next desk and leaves the coffee fallback @pr13', async ({ page, office }) => {
  test.setTimeout(60_000);
  const staff = Array.from({ length: 13 }, (_, i) => agent(`overflow-${i}`));
  office.server.publish(snapshot(staff));
  await office.open();
  await expect(rows(page)).toHaveCount(13);
  await page.clock.runFor(18_000);
  expect(await page.evaluate(() => {
    const a = window.tokenTally.actors.get('overflow-12');
    return { desk: a.desk, targetKey: a.targetKey, walking: a.walking };
  })).toEqual({ desk: null, targetKey: 'desk', walking: false });
  await office.publish([...staff.slice(1), agent('late-arrival')]);
  await expect(row(page, 'late-arrival')).toBeVisible();
  await page.clock.runFor(100);
  expect(await page.evaluate(() => {
    const a = window.tokenTally.actors.get('overflow-12');
    return { owner: a.desk?.owner, routedToDesk: a.target === a.desk?.spot,
      lateDesk: window.tokenTally.actors.get('late-arrival').desk };
  })).toEqual({ owner: 'overflow-12', routedToDesk: true, lateDesk: null });
  expect(new Set((await deskOwners(page)).filter(Boolean)).size).toBe(12);
  await page.clock.runFor(18_000);
  expect(await page.evaluate(() => {
    const a = window.tokenTally.actors.get('overflow-12');
    return !a.walking && a.at === a.desk.spot;
  })).toBe(true);
});

test('HUD shows the received waiting status before the next animation frame @pr13', async ({ page, office }) => {
  office.server.publish(snapshot([agent('status', { task: 'files', tool: 'Read' })]));
  await office.open();
  await row(page, 'status').click();
  await page.clock.runFor(100);
  const before = await page.evaluate(() => window.tokenTally.actors.get('status').targetKey);
  await office.publish([agent('status', { status: 'waiting', tool: 'Bash', task: 'copier' })]);
  await expect(row(page, 'status').locator('.st')).toHaveText('Needs you');
  await expect(page.locator('#file-body')).toContainText('Needs your sign-off: Bash');
  await expect(page.locator('#stats')).toContainText('1 need you');
  expect(await page.evaluate(() => window.tokenTally.actors.get('status').targetKey)).toBe(before);
});

test('focus and selection survive reordering, ten HUD updates and cast changes @pr13', async ({ page, office }) => {
  // Ten HUD ticks also render hundreds of software frames on shared CI runners.
  test.setTimeout(60_000);
  const child = agent('b', { parent: 'c', temp: true });
  office.server.publish(snapshot([agent('a'), child]));
  await office.open();
  await row(page, 'b').press('Enter');
  await expect(row(page, 'b')).toBeFocused();
  await office.publish([agent('a'), child, agent('c')]);
  await expect(row(page, 'b')).toBeFocused();
  await expect(row(page, 'b')).toHaveAttribute('aria-pressed', 'true');
  await page.clock.runFor(10_100);
  await expect(row(page, 'b')).toBeFocused();
  office.server.setCast({ names: ['One', 'Two', 'Three'], manager: 'Manager' });
  await page.evaluate(() => window.tokenTally.loadNames());
  await expect(row(page, 'b')).toBeFocused();
  await expect(row(page, 'b')).toHaveAttribute('aria-pressed', 'true');
  await page.locator('#start').focus();
  await office.publish([agent('a'), agent('b'), agent('c')]);
  await expect(page.locator('#start')).toBeFocused();
});

test('removal closes selection immediately and transfers only roster focus @pr13', async ({ page, office }) => {
  office.server.publish(snapshot([agent('a'), agent('b'), agent('c')]));
  await office.open();
  await row(page, 'b').press('Enter');
  await office.publish([agent('a'), agent('c')]);
  await expect(row(page, 'c')).toBeFocused();
  await expect(page.locator('#file')).toBeHidden();
  await office.publish([agent('a')]);
  await expect(row(page, 'a')).toBeFocused();
  await office.publish([]);
  await expect(page.locator('#roster-min')).toBeFocused();
});

for (const removal of [false, true]) {
  test(`closing a file with a minimised roster keeps usable focus removal=${removal} @pr13`, async ({ page, office }) => {
    office.server.publish(snapshot([agent('selected')]));
    await office.open();
    await row(page, 'selected').click();
    await page.locator('#roster-min').click();
    await page.locator('#file-close').focus();
    if (removal) await office.publish([]);
    else await page.locator('#file-close').click();
    await expect(page.locator('#file')).toBeHidden();
    await expect(page.locator('#roster-min')).toBeFocused();
  });
}

test('returning ID gets one active actor without reviving old selection @pr13', async ({ page, office }) => {
  office.server.publish(snapshot([agent('returning')]));
  await office.open();
  await row(page, 'returning').click();
  await office.publish([]);
  await expect(page.locator('#file')).toBeHidden();
  await office.publish([agent('returning', { assignment: 'A new shift' })]);
  await expect(rows(page)).toHaveCount(1);
  await expect(row(page, 'returning')).toHaveAttribute('aria-pressed', 'false');
  expect((await deskOwners(page)).filter((id) => id === 'returning')).toHaveLength(1);
  await row(page, 'returning').press('Space');
  await expect(page.locator('#file-body')).toContainText('A new shift');
});

for (const query of ['', '?lite']) {
  test(`departures before the first frame dispose actors and returning staff still walk out ${query || 'normal'}`, async ({ page, office }) => {
    test.setTimeout(60_000);
    await office.open(query);
    const staff = Array.from({ length: 3 }, (_, i) => agent(`quick-departure-${i}`));
    await office.publish(staff);
    await expect(rows(page)).toHaveCount(3);
    await row(page, staff[0].id).press('Enter');
    // The fixture clock is paused: both snapshots arrive before the first RAF.
    const initial = await page.evaluate((ids) => {
      window.departureProbe = ids.map((id) => {
        const actor = window.tokenTally.actors.get(id);
        const probe = { actor, geometries: 0, disposed: 0 };
        actor.root.traverse((object) => {
          if (!object.geometry) return;
          probe.geometries++;
          object.geometry.addEventListener('dispose', () => probe.disposed++);
        });
        return probe;
      });
      return window.departureProbe.map(({ actor, geometries }) => ({ walking: actor.walking, target: actor.targetKey, geometries }));
    }, staff.map((person) => person.id));
    for (const actor of initial) {
      expect(actor).toMatchObject({ walking: false, target: 'door' });
      expect(actor.geometries).toBeGreaterThan(0);
    }

    await office.publish([]);
    await expect(rows(page)).toHaveCount(0);
    await expect(page.locator('#file')).toBeHidden();
    expect(await page.evaluate(() => window.departureProbe.map(({ actor, geometries, disposed }) => ({
      absent: !window.tokenTally.actors.has(actor.id), detached: actor.root.parent === null,
      disposed: disposed === geometries,
    })))).toEqual(staff.map(() => ({ absent: true, detached: true, disposed: true })));
    expect((await deskOwners(page)).filter(Boolean)).toEqual([]);

    // A returned ID is a fresh, selectable actor. Once it starts walking, its
    // next departure must retain the normal walk-to-door animation and cleanup.
    await office.publish([agent(staff[0].id, { assignment: 'Returned shift' })]);
    await expect(row(page, staff[0].id)).toHaveAttribute('aria-pressed', 'false');
    await row(page, staff[0].id).press('Enter');
    await expect(page.locator('#file-body')).toContainText('Returned shift');
    expect(await page.evaluate((id) => window.tokenTally.actors.get(id) !== window.departureProbe[0].actor, staff[0].id)).toBe(true);
    await page.clock.runFor(100);
    await office.publish([]);
    expect(await page.evaluate((id) => {
      const actor = window.tokenTally.actors.get(id);
      return { leaving: actor?.leaving, walking: actor?.walking, attached: !!actor?.root.parent };
    }, staff[0].id)).toEqual({ leaving: true, walking: true, attached: true });
    // Initial RAFs above exercise the real frame wiring. Advance the actual
    // actor movement here without rendering 180 shadowed software-WebGL frames.
    await page.evaluate((id) => {
      const actor = window.tokenTally.actors.get(id);
      const start = performance.now() / 1000;
      for (let frame = 1; frame <= 180 && actor.walking; frame++) {
        actor.update(1 / 60, start + frame / 60);
      }
    }, staff[0].id);
    expect(await page.evaluate(() => [...window.tokenTally.actors.values()].map((actor) => actor.id))).toEqual(['manager']);
    await expect(page.locator('#file')).toBeHidden();
  });
}
