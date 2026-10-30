import {expect, test} from '@playwright/test';
import {readFileSync} from 'node:fs';
const rows = JSON.parse(readFileSync('services/reviewed_wikipedia_stories.json', 'utf8'));
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');

test('Wikipedia excerpts show fixed revisions and complete attribution on mobile and in dialogs', async ({page}) => {
  await page.setViewportSize({width:360,height:800});
  await page.addInitScript(() => localStorage.clear());
  await page.route('https://res.cloudinary.com/**', route => route.fulfill({contentType:'image/png',body:png}));
  const errors=[];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  const footer = page.locator('footer');
  await expect(footer.getByRole('link', {name:'bangumi-data 社群資料'})).toHaveAttribute('href', 'https://github.com/bangumi-data/bangumi-data/tree/94ea82be75d9e617c7970bb0fd0670ae5c60e53d');
  await expect(footer.getByRole('link', {name:'CC BY 4.0', exact:true})).toHaveAttribute('href', 'https://creativecommons.org/licenses/by/4.0/');
  await expect(footer).toContainText('依本站作品與季度範圍篩選');
  for (const row of rows) {
    await page.getByLabel('關鍵字搜尋').fill(row.anime_name);
    const card=page.locator('.anime-card').filter({has:page.locator('.anime-title',{hasText:row.anime_name})});
    await expect(card).toHaveCount(1);
    await expect(card.locator('.story-box')).toHaveText(row.story);
    await expect(card.locator('.story-source')).toHaveAttribute('href',row.source_url);
    await expect(card.locator('.story-source')).toContainText('維基百科');
    const credit=card.locator('.wikipedia-credit');
    await expect(credit).toBeVisible();
    await expect(credit).toContainText(String(row.attribution.revision_id));
    await expect(credit.getByRole('link',{name:'維基百科貢獻者'})).toHaveAttribute('href',row.attribution.contributors_url);
    await expect(credit.getByRole('link',{name:'CC BY-SA 4.0'})).toHaveAttribute('href',row.attribution.license_url);
    await expect(credit).toContainText('原文連續片段節錄');
    await card.getByText('集數來源與確認時間', {exact:true}).click();
    await expect(card.getByRole('link', {name:/AniList 條目/})).toHaveAttribute('href', `https://anilist.co/anime/${row.anilist_id}`);
    await card.locator('.story-box').click();
    await expect(page.locator('.swal2-html-container')).toContainText(row.story);
    await expect(page.locator('.swal2-html-container')).toContainText('維基百科貢獻者');
    await expect(page.locator('.swal2-html-container')).toContainText('CC BY-SA 4.0');
    expect(await page.evaluate(() => document.documentElement.scrollWidth>innerWidth)).toBe(false);
    await page.getByRole('button',{name:'關閉',exact:true}).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth>innerWidth)).toBe(false);
  }
  expect(errors).toEqual([]);
});
