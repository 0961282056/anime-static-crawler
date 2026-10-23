import {chromium, expect} from '@playwright/test';
import {mkdirSync, writeFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';

function validateCommit(expected) {
  if (!/^[a-f0-9]{40}$/.test(expected || '')) throw new Error('EXPECTED_COMMIT must be a full SHA');
}

// The CLI always verifies production. Tests use this same inspection on local fixtures.
export async function verifyPublishedPage(page, {site, expected}) {
  validateCommit(expected);
  // Exercise the same-origin browser network path, including managed proxy/TLS
  // settings, rather than using Node's separate APIRequestContext connection.
  await page.goto(`${site}?verify=${expected}`, {waitUntil: 'domcontentloaded'});
  const readJson = async path => page.evaluate(async url => {
    const response = await fetch(url, {cache: 'no-store'});
    if (!response.ok) throw new Error(`Production JSON HTTP ${response.status}`);
    return response.json();
  }, site + path);
  const marker = async () => {
    const release = await readJson(`release.json?verify=${expected}`);
    expect(release.commit).toBe(expected);
    return release;
  };
  await marker();
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
      const source = row.story_source;
      expect(['bangumi', 'official', 'wikipedia']).toContain(source.provider);
      const link = source.provider === 'bangumi' ? `https://bgm.tv/subject/${source.subject_id}` : source.source_url;
      await expect(card.locator('.story-source')).toHaveAttribute('href', link);
      if (source.provider === 'wikipedia') {
        const credit = card.locator('.wikipedia-credit');
        await expect(credit).toContainText(String(source.wikipedia.revision_id));
        await expect(credit.getByRole('link', {name: '維基百科貢獻者'})).toHaveAttribute('href', source.wikipedia.contributors_url);
        await expect(credit.getByRole('link', {name: 'CC BY-SA 4.0'})).toHaveAttribute('href', source.wikipedia.license_url);
      }
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
  await expect(page.locator('#resultCount')).toHaveText(String(matches.length), {timeout: 30000});
  const cards = page.locator('.anime-card');
  await expect.poll(async () => {
    const count = await cards.count();
    return count > 0 && count <= matches.length;
  }).toBe(true);
  let visible = await cards.count();
  const more = page.getByRole('button', {name: '顯示更多', exact: true});
  while (visible < matches.length) {
    await expect(more).toBeVisible();
    await more.click();
    await expect.poll(() => cards.count()).toBeGreaterThan(visible);
    visible = await cards.count();
    expect(visible).toBeLessThanOrEqual(matches.length);
  }
  await expect(cards).toHaveCount(matches.length);
  await expect(more).toBeHidden();
  const historicalCard = cards.filter({has: page.getByText(historical.identity[1], {exact: true})});
  await expect(historicalCard).toHaveCount(1);
  await expect(historicalCard.locator('.anime-title')).toBeVisible();
  expect(requested.every(key => allowed.has(key))).toBeTruthy();
  await marker();
  return {status: 'verified', commit: expected, quarter, checked, historical_search: historical.identity[0], matched_quarter_requests: requested};
}

async function main() {
  const expected = process.env.EXPECTED_COMMIT;
  validateCommit(expected);
  const browser = await chromium.launch(process.env.CHROMIUM_EXECUTABLE ? {executablePath: process.env.CHROMIUM_EXECUTABLE} : {});
  try {
    const result = await verifyPublishedPage(await browser.newPage(), {
      site: 'https://anime-static-crawler.pages.dev/', expected,
    });
    mkdirSync('audit-output', {recursive: true});
    writeFileSync('audit-output/production-browser.json', JSON.stringify(result, null, 2) + '\n');
  } finally {
    await browser.close();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
