import {chromium, expect} from '@playwright/test';
import {mkdirSync, writeFileSync} from 'node:fs';
const site = 'https://anime-static-crawler.pages.dev/';
const expected = process.env.EXPECTED_COMMIT;
if (!/^[a-f0-9]{40}$/.test(expected || '')) throw new Error('EXPECTED_COMMIT must be a full SHA');
const browser = await chromium.launch(process.env.CHROMIUM_EXECUTABLE ? {executablePath: process.env.CHROMIUM_EXECUTABLE} : {});
try {
  const page = await browser.newPage();
  const marker = async () => {
    const response = await page.request.get(`${site}release.json?verify=${expected}`);
    expect(response.ok()).toBeTruthy();
    return response.json();
  };
  expect((await marker()).commit).toBe(expected);
  await page.goto(`${site}?verify=${expected}`, {waitUntil: 'domcontentloaded'});
  await expect(page.locator('.anime-card').first()).toBeVisible({timeout: 30000});
  const quarter = `${await page.locator('#yearSelect').inputValue()}_${await page.locator('#seasonSelect').inputValue()}`;
  const response = await page.request.get(`${site}data/${encodeURIComponent(quarter)}.json?verify=${expected}`);
  expect(response.ok()).toBeTruthy();
  const dataset = await response.json();
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
  expect((await marker()).commit).toBe(expected);
  mkdirSync('audit-output', {recursive: true});
  writeFileSync('audit-output/production-browser.json', JSON.stringify({status: 'verified', commit: expected, quarter, checked}, null, 2) + '\n');
} finally {
  await browser.close();
}
