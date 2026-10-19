import {expect, test} from '@playwright/test';

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');

test('new official excerpts and reviewed weekdays work with real historical data', async ({page}) => {
  await page.setViewportSize({width: 360, height: 800});
  await page.addInitScript(() => localStorage.clear());
  await page.route('https://res.cloudinary.com/**', route => route.fulfill({contentType: 'image/png', body: png}));
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.locator('.anime-card').first()).toBeVisible();
  const input = page.getByLabel('關鍵字搜尋');
  for (const [name, host, text] of [
    ['奇喵的畫家', 'www.tv-tokyo.co.jp', 'パリの片隅'],
    ['歡迎光臨，千歲醬', 'www.vap.co.jp', '京都に住む子ペンギン'],
  ]) {
    await input.fill(name);
    const card = page.locator('.anime-card').filter({has: page.locator('.anime-title', {hasText: name})});
    await expect(card).toHaveCount(1);
    await expect(card.locator('.story-source')).toHaveAttribute('href', new RegExp(`https://${host.replaceAll('.', '\\.')}/`));
    await expect.poll(() => page.evaluate(name => window.Alpine.$data(document.querySelector('[x-data]')).rawAnimeList.find(a => a.anime_name === name)?.story, name)).toContain(text);
  }
  for (const name of ['魔法紀錄 魔法少女小圓外傳 最終季', 'KJ FILE']) {
    await input.fill(name);
    await expect.poll(() => page.evaluate(name => window.Alpine.$data(document.querySelector('[x-data]')).rawAnimeList.find(a => a.anime_name === name)?.premiere_date, name)).toBe('日');
    await page.getByLabel('每週放送').selectOption('日');
    await expect(page.locator('.anime-card').first()).toBeVisible();
    await page.getByLabel('每週放送').selectOption('全部');
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
  expect(errors).toEqual([]);
});
