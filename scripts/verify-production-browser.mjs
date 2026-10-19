import {chromium, expect} from '@playwright/test';
import {mkdirSync, writeFileSync} from 'node:fs';
const site = 'https://anime-static-crawler.pages.dev/';
const expected = process.env.EXPECTED_COMMIT;
if (!/^[a-f0-9]{40}$/.test(expected || '')) throw new Error('EXPECTED_COMMIT must be a full SHA');
const browser = await chromium.launch(process.env.CHROMIUM_EXECUTABLE ? {executablePath: process.env.CHROMIUM_EXECUTABLE} : {});
try {
  const page = await browser.newPage();
  // Exercise the same-origin browser network path, including managed proxy/TLS
  // settings, rather than using Node's separate APIRequestContext connection.
  await page.goto(`${site}?verify=${expected}`, {waitUntil: 'domcontentloaded'});
  const readJson = async path => page.evaluate(async url => {
    const response = await fetch(url, {cache: 'no-store'});
    if (!response.ok) throw new Error(`Production JSON HTTP ${response.status}`);
    return response.json();
  }, site + path);
  const marker = () => readJson(`release.json?verify=${expected}`);
  expect((await marker()).commit).toBe(expected);
  await expect(page.locator('.anime-card').first()).toBeVisible({timeout: 30000});
  const quarter = `${await page.locator('#yearSelect').inputValue()}_${await page.locator('#seasonSelect').inputValue()}`;
  const dataset = await readJson(`data/${encodeURIComponent(quarter)}.json?verify=${expected}`);
  await expect(page.locator('.anime-card')).toHaveCount(dataset.anime_list.length);
  const checked = [];
  for (const row of dataset.anime_list.slice(0, 3)) {
    const card = page.locator('.anime-card').filter({has: page.getByText(row.anime_name, {exact: true})});
    await expect(card).toHaveCount(1);
    await expect(card.locator('.story-box')).toHaveText(row.story);
    if (row.story_source) {
      const link = row.story_source.provider === 'official' ? row.story_source.source_url : `https://bgm.tv/subject/${row.story_source.subject_id}`;
      await expect(card.locator('.story-source')).toHaveAttribute('href', link);
    }
    checked.push(row.bangumi_id);
  }
  const release = await marker();
  const index = await readJson(`search-index.json?verify=${expected}`);
  expect(index.data_version).toBe(release.data_version);
  const historical = index.entries.find(row => row.identity[0] === 'anime-646') || index.entries.find(row => row.quarters[0] !== quarter);
  expect(historical).toBeTruthy();
  const normalize = value => value.normalize('NFKC').toLowerCase();
  const keyword = normalize(historical.identity[1]).trim();
  const matches = index.entries.filter(row => [row.identity[1], row.identity[2], row.story].some(value => normalize(value).includes(keyword)));
  const allowed = new Set(matches.map(row => row.quarters[0]));
  const requested = [];
  page.on('request', request => {
    const path = decodeURIComponent(new URL(request.url()).pathname);
    if (path.startsWith('/data/')) requested.push(path.slice(6, -5));
  });
  await page.getByLabel('關鍵字搜尋').fill(historical.identity[1]);
  await expect(page.locator('.anime-card')).toHaveCount(matches.length, {timeout: 30000});
  await expect(page.locator('.anime-title').filter({hasText: historical.identity[1]})).toBeVisible();
  expect(requested.every(key => allowed.has(key))).toBeTruthy();
  expect((await marker()).commit).toBe(expected);
  mkdirSync('audit-output', {recursive: true});
  writeFileSync('audit-output/production-browser.json', JSON.stringify({status: 'verified', commit: expected, quarter, checked, historical_search: historical.identity[0], matched_quarter_requests: requested}, null, 2) + '\n');
} finally {
  await browser.close();
}
