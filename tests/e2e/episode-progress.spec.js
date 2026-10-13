import {expect, test} from '@playwright/test';

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');

test('episode progress, status filter and source details work with real Alpine on mobile', async ({page}) => {
  await page.setViewportSize({width: 360, height: 800});
  await page.addInitScript(() => localStorage.clear());
  const record = (id, name, episodes) => ({
    bangumi_id: `anime-${id}`, anime_name: name,
    anime_image_url: `https://res.cloudinary.com/demo/image/upload/v1/anime_covers/${'a'.repeat(64)}`,
    premiere_date: '一', premiere_time: '24:00', story: '測試簡介',
    external_ids: {bangumi: 580133}, episodes,
  });
  const info = {
    total_episodes: 12, aired_episodes: 8, airing_status: 'airing',
    episode_count_status: 'confirmed', fetch_state: 'ok',
    checked_at: '2026-10-13T18:00:00+08:00', note: '依首播日期核對正篇。',
  };
  await page.route('**/data/*.json*', route => route.fulfill({
    contentType: 'application/json', body: JSON.stringify({
      generated_at: '2026-10-13T18:00:00+08:00',
      anime_list: [record(1, '播出範例', info), record(2, '播畢範例', {...info, aired_episodes: 12, airing_status: 'finished'}), record(3, '舊資料範例', null)],
    }),
  }));
  await page.route('https://res.cloudinary.com/**', route => route.fulfill({contentType: 'image/png', body: png}));
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.locator('.anime-card')).toHaveCount(3);
  await expect(page.locator('.episode-count').first()).toHaveText('已播 8／12 集');
  await page.getByLabel('播出狀態').selectOption('finished');
  await expect(page.locator('.anime-card')).toHaveCount(1);
  await expect(page.locator('.episode-count')).toHaveText('全 12 集');
  await expect(page.locator('.airing-status')).toHaveText('本季已播畢');
  await page.getByText('集數來源與確認時間', {exact: true}).click();
  await expect(page.locator('.episode-details')).toContainText('最後確認：');
  await expect(page.getByRole('link', {name: '查看正篇集目'})).toHaveAttribute('href', 'https://bgm.tv/subject/580133/ep');
  await page.getByLabel('播出狀態').selectOption('unknown');
  await expect(page.locator('.anime-title')).toHaveText('舊資料範例');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
  expect(errors).toEqual([]);
});
