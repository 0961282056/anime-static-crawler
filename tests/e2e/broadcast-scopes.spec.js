import {expect, test} from '@playwright/test';

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');

test('historical broadcast scopes filter across weekdays without mixing daily shorts and compilations', async ({page}) => {
  await page.setViewportSize({width: 360, height: 800});
  await page.addInitScript(() => localStorage.clear());
  await page.route('https://res.cloudinary.com/**', route => route.fulfill({contentType: 'image/png', body: png}));
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.locator('.anime-card').first()).toBeVisible();
  const search = page.getByLabel('關鍵字搜尋');
  const weekday = page.getByLabel('每週放送');
  const named = name => page.locator('.anime-card').filter({has: page.getByText(name, {exact: true})});

  await search.fill('耐子的日常 新系列');
  const taeko = named('耐子的日常 新系列');
  await expect(taeko.locator('.info-row')).toContainText('週一至五');
  await expect(taeko.locator('.broadcast-note')).toContainText('週一至週五各更新一話');
  for (const day of ['一', '二', '三', '四', '五', '一至五']) {
    await weekday.selectOption(day);
    await expect(taeko).toBeVisible();
  }
  for (const day of ['六', '日', '不定期公開', '無首播日期']) {
    await weekday.selectOption(day);
    await expect(taeko).toHaveCount(0);
  }
  await weekday.selectOption('全部');

  await search.fill('OniPan!');
  const onipan = named('OniPan!');
  await expect(onipan.locator('.episode-count')).toHaveText('全 12 集');
  await expect(onipan.locator('.broadcast-note')).toContainText('12集週合集計數，週五串流更新');
  await expect(onipan.locator('.broadcast-note')).toContainText('電視短篇於週一至五播出');
  await expect(onipan.locator('.broadcast-note a')).toHaveAttribute('href', 'https://acgsecrets.hk/bangumi/202204/');
  await weekday.selectOption('五');
  await expect(onipan).toBeVisible();
  await weekday.selectOption('一');
  await expect(onipan).toHaveCount(0);
  await weekday.selectOption('全部');

  await search.fill('巨神與冰華之城');
  const giant = named('巨神與冰華之城');
  await expect(giant.locator('.info-row')).toContainText('不定期公開');
  await expect(giant.locator('.broadcast-note')).toContainText('不是每週固定更新');
  await weekday.selectOption('不定期公開');
  await expect(giant).toBeVisible();
  for (const day of ['六', '無首播日期']) {
    await weekday.selectOption(day);
    await expect(giant).toHaveCount(0);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  expect(errors).toEqual([]);
});
