import {expect, test} from '@playwright/test';
import {fixtureRelease, installDatasetFixture} from '../helpers/versioned-data.cjs';

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');
const record = (id, name, story = name) => ({
  bangumi_id: `anime-${id}`, anime_name: name, original_name: name, story,
  premiere_date: '一', premiere_time: '20:00',
  anime_image_url: `https://res.cloudinary.com/demo/image/upload/v1/anime_covers/${'a'.repeat(64)}`,
});
const entries = (records, quarter) => records.map(row => ({
  identity: [row.bangumi_id, row.anime_name, row.original_name, null],
  story: row.story, quarters: [quarter],
}));

test.beforeEach(async ({page}) => {
  await page.clock.setFixedTime(new Date('2026-10-23T12:00:00Z'));
  await page.addInitScript(() => localStorage.clear());
  await page.route('https://res.cloudinary.com/**', route => route.fulfill({contentType: 'image/png', body: png}));
});

test('old cached index refuses new same-identity bytes and retry recovers a data-only release under CSP', async ({page}) => {
  const current = record(9900, '目前季度');
  const old = record(9901, '同一歷史作品', 'old-only-needle');
  const fresh = {...old, story: 'new-only-needle'};
  const oldPayloads = {'2026_秋': {anime_list: [current]}, '2018_冬': {anime_list: [old]}};
  const before = await installDatasetFixture(page, oldPayloads);
  const after = fixtureRelease({...oldPayloads, '2018_冬': {anime_list: [fresh]}});
  before.index.entries = entries([old], '2018_冬');
  after.index.entries = entries([fresh], '2018_冬');
  let deployedNew = false;
  const requestedVersions = [];
  await page.route('**/search-index.json*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify(deployedNew ? after.index : before.index)}));
  await page.route('**/release.json*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify(deployedNew ? after.release : before.release)}));
  await page.route('**/data/*.json*', route => {
    const url = new URL(route.request().url());
    if (!decodeURIComponent(url.pathname).endsWith('/2018_冬.json')) return route.fallback();
    requestedVersions.push(url.searchParams.get('v'));
    return route.fulfill({contentType: 'application/json', body: (deployedNew ? after : before).bodies['2018_冬']});
  });
  await page.goto('/');
  await expect(page.locator('.anime-title')).toHaveText('目前季度');
  const search = page.getByLabel('關鍵字搜尋');
  await search.fill('prime-zero-results');
  await expect(page.getByText('沒有符合條件的動畫', {exact: true})).toBeVisible();
  deployedNew = true;
  await search.fill('old-only-needle');
  await expect(page.getByRole('alert')).toContainText('SEARCH_VERSION');
  await expect(page.locator('.anime-card')).toHaveCount(0);
  expect(await page.evaluate(() => Alpine.$data(document.querySelector('[x-data]')).dataCache['2018_冬'])).toBeUndefined();
  expect(requestedVersions).toEqual([before.release.quarter_versions['2018_冬']]);
  await page.getByRole('button', {name: '重新載入'}).click();
  await expect(page.getByRole('alert')).toBeHidden();
  await expect(page.getByText('沒有符合條件的動畫', {exact: true})).toBeVisible();
  await search.fill('new-only-needle');
  await expect(page.locator('.anime-title')).toHaveText('同一歷史作品');
  await expect(page.locator('.story-box')).toHaveText('new-only-needle');
  expect(requestedVersions).toEqual([before.release.quarter_versions['2018_冬'], after.release.quarter_versions['2018_冬']]);
  expect(await page.evaluate(() => Alpine.$data(document.querySelector('[x-data]')).dataCache['2018_冬'].version)).toBe(after.release.quarter_versions['2018_冬']);
});

test('offline retained search results remain pageable and filterable with the keyboard under CSP', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  const records = Array.from({length: 125}, (_, index) => ({
    ...record(9000 + index, `common-${index}`), premiere_date: index >= 120 ? '二' : '一',
  }));
  const fixture = await installDatasetFixture(page, {
    '2026_秋': {anime_list: [record(8000, '目前季度')]},
    '2025_夏': {anime_list: records},
  });
  fixture.index.entries = entries(records, '2025_夏');
  let offline = false;
  await page.route('**/search-index.json*', route => offline ? route.abort('connectionfailed') : route.fulfill({contentType: 'application/json', body: JSON.stringify(fixture.index)}));
  await page.goto('/');
  await expect(page.locator('.anime-title')).toHaveText('目前季度');
  const search = page.getByLabel('關鍵字搜尋');
  await search.fill('common');
  await expect(page.locator('#resultCount')).toHaveText('125');
  const more = page.getByRole('button', {name: '顯示更多', exact: true});
  await more.click();
  await expect(page.locator('.anime-card')).toHaveCount(120);
  await page.evaluate(() => {
    const app = Alpine.$data(document.querySelector('[x-data]'));
    app.dataCache.__search_index__.cachedAt = Date.now() - app.DATA_CACHE_TTL_MS - 1;
  });
  offline = true;
  await search.fill('common ');
  await expect(page.getByRole('alert')).toContainText('NETWORK');
  await expect(page.getByRole('alert')).toContainText('目前保留上次成功的資料');
  await expect(page.locator('.anime-card')).toHaveCount(60);
  await expect(more).toBeVisible();
  await more.focus(); await page.keyboard.press('Enter');
  await expect(page.locator('.anime-card')).toHaveCount(120);
  await expect(page.locator('.title-copy').nth(60)).toBeFocused();
  await more.focus(); await page.keyboard.press('Space');
  await expect(page.locator('.anime-card')).toHaveCount(125);
  await expect(more).toBeHidden();
  await page.getByLabel('每週放送').selectOption('二');
  await expect(page.locator('.anime-card')).toHaveCount(5);
  await expect(page.locator('.anime-title').first()).toHaveText('common-120');
  await expect(page.getByRole('alert')).toContainText('NETWORK');
});
