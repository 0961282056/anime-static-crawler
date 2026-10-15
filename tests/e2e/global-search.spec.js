import {expect, test} from '@playwright/test';

test('keywords search historical quarters, merge repeated works and clear back to the selected quarter', async ({page}) => {
  await page.setViewportSize({width: 360, height: 800});
  await page.addInitScript(() => localStorage.clear());
  const record = (id, name, original_name) => ({
    bangumi_id: `anime-${id}`, anime_name: name, original_name,
    anime_image_url: '無圖片', premiere_date: '一', premiere_time: '20:00', story: '搜尋測試',
  });
  const shared = record(8001, '共用作品', 'Shared work');
  await page.route('**/data/*.json*', route => {
    const path = decodeURIComponent(new URL(route.request().url()).pathname);
    let records = [];
    if (path.endsWith('/2026_秋.json')) records = [shared];
    if (path.endsWith('/2025_夏.json')) records = [shared];
    if (path.endsWith('/2018_冬.json')) records = [record(8002, '遠古作品', 'Ancient work')];
    return route.fulfill({contentType: 'application/json', body: JSON.stringify({
      generated_at: '2026-10-15T08:00:00+08:00', anime_list: records,
    })});
  });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.locator('.anime-title')).toHaveText('共用作品');
  const input = page.getByLabel('關鍵字搜尋');
  await input.fill('ancient');
  await expect(page.locator('.anime-title')).toHaveText('遠古作品');
  await expect(page.locator('.source-quarters')).toContainText('2018年冬季');
  await expect(page.getByLabel('年份')).toBeDisabled();
  await expect(page.getByLabel('季節', {exact: true})).toBeDisabled();
  await page.getByLabel('每週放送').selectOption('二');
  await expect(page.locator('.anime-card')).toHaveCount(0);
  await page.getByLabel('每週放送').selectOption('全部');
  await input.fill('共用');
  await expect(page.locator('.anime-card')).toHaveCount(1);
  await expect(page.locator('.source-quarters')).toHaveText('2026年秋季、2025年夏季');
  await input.fill('');
  await expect(page.locator('.anime-title')).toHaveText('共用作品');
  await expect(page.locator('.source-quarters')).toBeHidden();
  await expect(page.getByLabel('年份')).toBeEnabled();
  await expect(page.getByLabel('季節', {exact: true})).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
  expect(errors).toEqual([]);
});
