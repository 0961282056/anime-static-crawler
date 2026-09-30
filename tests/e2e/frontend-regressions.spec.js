import { expect, test } from '@playwright/test';

const transparentPng = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
  'base64',
);

function fixtureDataset(key) {
  return {
    schema_version: 1,
    generated_at: '2026-09-30T00:00:00+08:00',
    anime_list: [{
      bangumi_id: 'anime-9999',
      anime_name: `fixture-${key}`,
      anime_image_url: `https://res.cloudinary.com/demo/image/upload/f_auto,q_auto:best/v1/anime_covers/${'a'.repeat(64)}`,
      premiere_date: '一',
      premiere_time: '20:00',
      story: 'fixture story',
    }],
  };
}

async function installFixtureRoutes(page, {delayFirst = false} = {}) {
  let requestCount = 0;
  await page.route('**/data/*.json*', async route => {
    requestCount += 1;
    const key = decodeURIComponent(route.request().url().split('/data/')[1].split('?')[0].replace('.json', ''));
    if (delayFirst && requestCount === 1) await new Promise(resolve => setTimeout(resolve, 350));
    try {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(fixtureDataset(key)),
      });
    } catch (error) {
      // The stale request can be cancelled before the delayed fixture responds.
    }
  });
  await page.route('https://res.cloudinary.com/**', async route => {
    await route.fulfill({status: 200, contentType: 'image/png', body: transparentPng});
  });
  return () => requestCount;
}

test('real Alpine handles rapid quarter switching and clears pending state', async ({page}) => {
  await page.addInitScript(() => localStorage.clear());
  const requestCount = await installFixtureRoutes(page, {delayFirst: true});
  await page.goto('/');

  const yearSelect = page.getByLabel('年份');
  await expect(yearSelect).toBeVisible();
  const values = await yearSelect.locator('option').evaluateAll(options => options.map(option => option.value));
  const currentYear = await yearSelect.inputValue();
  const alternateYear = values.find(value => value !== currentYear);
  expect(alternateYear).toBeTruthy();

  await yearSelect.selectOption(alternateYear);
  await expect(page.locator('.anime-title').first()).toHaveText(new RegExp(`^fixture-${alternateYear}_`));
  await page.waitForTimeout(450);
  await expect(page.locator('.anime-title').first()).toHaveText(new RegExp(`^fixture-${alternateYear}_`));

  const state = await page.evaluate(() => {
    const root = document.querySelector('[x-data]');
    const data = window.Alpine.$data(root);
    return {
      hasAlpine: Boolean(window.Alpine),
      pendingSize: data._pendingDataRequests.size,
      cacheKeys: Object.keys(data.dataCache),
    };
  });
  expect(state.hasAlpine).toBe(true);
  expect(state.pendingSize).toBe(0);
  expect(state.cacheKeys.length).toBeGreaterThanOrEqual(1);
  expect(requestCount()).toBeGreaterThanOrEqual(2);
});

test('real Alpine tolerates throwing storage and refetches after TTL expiry', async ({page}) => {
  await page.addInitScript(() => {
    const throwingStorage = {
      getItem() { throw new Error('storage blocked'); },
      setItem() { throw new Error('storage blocked'); },
      removeItem() { throw new Error('storage blocked'); },
      clear() { throw new Error('storage blocked'); },
    };
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() { return throwingStorage; },
    });
  });
  const requestCount = await installFixtureRoutes(page);
  await page.goto('/');
  await expect(page.locator('.anime-title').first()).toBeVisible();

  const before = requestCount();
  await page.evaluate(() => {
    const data = window.Alpine.$data(document.querySelector('[x-data]'));
    const key = `${data.year}_${data.season}`;
    data.dataCache[key].cachedAt = Date.now() - data.DATA_CACHE_TTL_MS - 1;
    return data.loadData();
  });
  await expect.poll(() => requestCount()).toBeGreaterThan(before);
  await expect.poll(async () => page.evaluate(() => {
    const data = window.Alpine.$data(document.querySelector('[x-data]'));
    return data._pendingDataRequests.size;
  })).toBe(0);
  await expect(page.locator('.anime-title').first()).toBeVisible();
});

test('real Alpine explains transient HTTP failures and allows a safe retry', async ({page}) => {
  await page.addInitScript(() => localStorage.clear());
  let requestCount = 0;
  await page.route('**/data/*.json*', async route => {
    requestCount += 1;
    if (requestCount <= 3) {
      await route.fulfill({status: 429, contentType: 'application/json', body: JSON.stringify({error: 'rate limit detail'})});
      return;
    }
    const key = decodeURIComponent(route.request().url().split('/data/')[1].split('?')[0].replace('.json', ''));
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(fixtureDataset(key)),
    });
  });
  await page.route('https://res.cloudinary.com/**', async route => {
    await route.fulfill({status: 200, contentType: 'image/png', body: transparentPng});
  });

  await page.goto('/');
  const alert = page.getByRole('alert');
  await expect(alert).toContainText('資料服務忙碌');
  await expect(alert).toContainText('HTTP 429');
  await expect(alert).toContainText('錯誤代碼：HTTP_429');
  await expect(alert).not.toContainText('rate limit detail');
  await expect(page.getByRole('button', {name: '重新載入'})).toBeVisible();

  await page.getByRole('button', {name: '重新載入'}).click();
  await expect(page.locator('.anime-title').first()).toHaveText(/fixture-/);
  await expect(alert).toBeHidden();
  expect(requestCount).toBe(4);
});

test('real Alpine rejects malformed JSON shape and retries without leaving a pending request', async ({page}) => {
  await page.addInitScript(() => localStorage.clear());
  let requestCount = 0;
  await page.route('**/data/*.json*', async route => {
    requestCount += 1;
    const key = decodeURIComponent(route.request().url().split('/data/')[1].split('?')[0].replace('.json', ''));
    const body = requestCount === 1
      ? JSON.stringify({schema_version: 1, anime_list: [{story: 'payload must stay private'}]})
      : JSON.stringify(fixtureDataset(key));
    await route.fulfill({status: 200, contentType: 'application/json', body});
  });
  await page.route('https://res.cloudinary.com/**', async route => {
    await route.fulfill({status: 200, contentType: 'image/png', body: transparentPng});
  });

  await page.goto('/');
  const alert = page.getByRole('alert');
  await expect(alert).toContainText('資料格式錯誤');
  await expect(alert).toContainText('DATA_SCHEMA');
  await expect(alert).not.toContainText('payload must stay private');

  await page.getByRole('button', {name: '重新載入'}).click();
  await expect(page.locator('.anime-title').first()).toHaveText(/fixture-/);
  await expect.poll(() => page.evaluate(() => {
    const data = window.Alpine.$data(document.querySelector('[x-data]'));
    return data._pendingDataRequests.size;
  })).toBe(0);
  expect(requestCount).toBe(2);
});
