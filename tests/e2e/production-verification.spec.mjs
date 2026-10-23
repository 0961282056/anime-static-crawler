import {expect, test} from '@playwright/test';
import {readFileSync, readdirSync} from 'node:fs';
import {verifyPublishedPage} from '../../scripts/verify-production-browser.mjs';

const expected = 'a'.repeat(40);
const site = 'http://127.0.0.1:4173/';
const csp = readFileSync('_headers', 'utf8').match(/Content-Security-Policy:\s*(.+)/)[1].trim();
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');
const record = (id, name) => ({
  bangumi_id: `anime-${id}`, anime_name: name, story: '本地驗收測試',
  anime_image_url: '無圖片', premiere_date: '一', premiere_time: '20:00',
});

async function installFixture(page, {current, historical, changeRelease = false}) {
  await page.addInitScript(() => localStorage.clear());
  await page.route(`${site}?verify=*`, async route => {
    const response = await route.fetch();
    await route.fulfill({response, headers: {...response.headers(), 'content-security-policy': csp}});
  });
  await page.route('https://res.cloudinary.com/**', route => route.fulfill({contentType: 'image/png', body: png}));
  let releaseReads = 0;
  await page.route('**/release.json*', route => {
    releaseReads += 1;
    return route.fulfill({contentType: 'application/json', body: JSON.stringify({
      ...JSON.parse(readFileSync('dist/release.json', 'utf8')),
      commit: changeRelease && releaseReads > 2 ? 'b'.repeat(40) : expected,
    })});
  });
  const index = JSON.parse(readFileSync('dist/search-index.json', 'utf8'));
  index.entries = historical.map(row => ({
    identity: [row.bangumi_id, row.anime_name, row.original_name || '', row.external_ids?.bangumi || null],
    story: row.story, quarters: ['2018_冬'],
  }));
  await page.route('**/search-index.json*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify(index)}));
  await page.route('**/data/*.json*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({
    anime_list: decodeURIComponent(new URL(route.request().url()).pathname).endsWith('/2018_冬.json') ? historical : current,
  })}));
  return () => releaseReads;
}

test('production inspection accepts all three synopsis sources and checks Wikipedia attribution', async ({page}) => {
  const records = readdirSync('dist/data').filter(file => /^\d{4}_[冬春夏秋]\.json$/.test(file))
    .flatMap(file => JSON.parse(readFileSync(`dist/data/${file}`, 'utf8')).anime_list);
  const current = ['wikipedia', 'official', 'bangumi'].map(provider => {
    const row = records.find(row => row.story_source?.provider === provider);
    expect(row, `A published ${provider} source is required`).toBeTruthy();
    return row;
  });
  await installFixture(page, {current, historical: [record(646, '驗收歷史作品')]});
  const result = await verifyPublishedPage(page, {site, expected});
  expect(result.checked).toEqual(current.map(row => row.bangumi_id));
  expect(result.matched_quarter_requests).toEqual(['2018_冬']);
});

test('production inspection expands large search results to find an exact title beyond the first batch', async ({page}) => {
  const historical = Array.from({length: 125}, (_, i) => record(i === 124 ? 646 : 9000 + i, i === 124 ? 'needle' : `needle-${i}`));
  await installFixture(page, {current: [record(8000, '當季作品')], historical});
  const result = await verifyPublishedPage(page, {site, expected});
  expect(result.historical_search).toBe('anime-646');
  await expect(page.locator('#resultCount')).toHaveText('125');
  await expect(page.locator('.anime-card')).toHaveCount(125);
  await expect(page.getByRole('button', {name: '顯示更多', exact: true})).toBeHidden();
});

test('production inspection rejects a release that changes during browser verification', async ({page}) => {
  const reads = await installFixture(page, {
    current: [record(8000, '當季作品')], historical: [record(646, '驗收歷史作品')], changeRelease: true,
  });
  await expect(verifyPublishedPage(page, {site, expected})).rejects.toThrow();
  expect(reads()).toBe(3);
  await expect(page.locator('.anime-title')).toHaveText('驗收歷史作品');
});
