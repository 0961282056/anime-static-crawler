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
    source_urls: ['https://chiramune.com/bd/06.html'],
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
  await expect(page.locator('.source-links a').first()).toHaveAttribute('href', 'https://chiramune.com/bd/06.html');
  await page.getByLabel('播出狀態').selectOption('unknown');
  await expect(page.locator('.anime-title')).toHaveText('舊資料範例');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
  expect(errors).toEqual([]);
});

test('reviewed historical scopes render real data and official sources on mobile', async ({page}) => {
  await page.setViewportSize({width: 360, height: 800});
  await page.addInitScript(() => localStorage.clear());
  await page.route('https://res.cloudinary.com/**', route => route.fulfill({contentType: 'image/png', body: png}));
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.locator('.anime-card').first()).toBeVisible();
  const input = page.getByLabel('關鍵字搜尋');
  const cases = [
    ['上班族魚先生', '全 12 集', 'www.videx.jp'],
    ['1日外出錄班長', '集數待確認', 'www.vap.co.jp'],
    ['末班電車後、膠囊旅館、給上司微熱的夜晚。', '全 12 集', 'shucap.cf-anime.com'],
    ['正中間的理久', '全 12 集', 'www.tv-tokyo.co.jp'],
    ['奇喵的畫家', '全 25 集', 'www.tv-tokyo.co.jp'],
    ['Kumarba 第2季', '全 12 集', 'bgm.tv'],
    ['神奇柑仔店 錢天堂', '已播 148 集／總集數未定', 'www.web.nhk'],
    ['異世界迷宮裡的後宮生活', '全 12 集', 'bgm.tv'],
    ['後宮露營！', '全 8 集', 'harecam.af-original.com'],
    ['大正偽新娘～替身新娘與軍人的猛烈熱愛', '全 8 集', 'gunpuku-mouai.af-original.com'],
  ];
  for (const [name, label, host] of cases) {
    await input.fill(name);
    const card = page.locator('.anime-card').filter({has: page.locator('.anime-title', {hasText: name})});
    await expect(card).toHaveCount(1);
    await expect(card.locator('.episode-count')).toHaveText(label);
    await card.getByText('集數來源與確認時間', {exact: true}).click();
    const href = await card.locator('.source-links a').first().getAttribute('href');
    expect(new URL(href).hostname).toBe(host);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
  expect(errors).toEqual([]);
});

test('official summary excerpts render real season data with attributed links', async ({page}) => {
  await page.setViewportSize({width: 360, height: 800});
  await page.addInitScript(() => localStorage.clear());
  await page.route('https://res.cloudinary.com/**', route => route.fulfill({contentType: 'image/png', body: png}));
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.locator('.anime-card').first()).toBeVisible();
  for (const [name, host] of [
    ['佐佐木與文鳥小嗶 第二季', 'kadokawa-animation.jp'],
    ['重生者的魔法一定要特別 第二季', 'www.fujitv.co.jp'],
    ['冰之城牆 第二季', 'korinojoheki-pr.com'],
    ['Welsh & Shedar', 'welshandshedar-anime.com'],
  ]) {
    await page.getByLabel('關鍵字搜尋').fill(name);
    const card = page.locator('.anime-card');
    await expect(card).toHaveCount(1);
    await expect(card.locator('.story-source')).toHaveText('簡介來源：官方作品介紹原文（節錄）');
    expect(new URL(await card.locator('.story-source').getAttribute('href')).hostname).toBe(host);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
  expect(errors).toEqual([]);
});

test('source details distinguish failed refreshes and dated summary evidence', async ({page}) => {
  await page.setViewportSize({width: 360, height: 800});
  await page.addInitScript(() => localStorage.clear());
  await page.route('**/data/*.json*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({
    generated_at: '2026-10-16T09:00:00+08:00',
    anime_list: [{
      bangumi_id: 'anime-999', anime_name: '來源查證範例', original_name: 'Source Example',
      anime_image_url: `https://res.cloudinary.com/demo/image/upload/v1/anime_covers/${'a'.repeat(64)}`,
      premiere_date: '一', premiere_time: '12:00', story: '這是有明確作品身分與快照日期的來源簡介。',
      external_ids: {bangumi: 580133, anilist: 197754},
      story_source: {provider: 'bangumi', subject_id: 580133, method: 'bangumi_archive', checked_at: '2026-10-16T09:00:00+08:00', source_snapshot_date: '2026-10-14'},
      episodes: {total_episodes: 12, aired_episodes: 8, airing_status: 'airing', episode_count_status: 'confirmed', fetch_state: 'stale', source_kind: 'bangumi_archive', source_snapshot_date: '2026-10-14', checked_at: '2026-10-15T08:00:00+08:00', attempted_at: '2026-10-16T09:00:00+08:00', source_urls: ['https://bgm.tv/subject/580133/ep'], note: '僅計電視正篇，不含特別篇。'},
    }],
  })}));
  await page.route('https://res.cloudinary.com/**', route => route.fulfill({contentType: 'image/png', body: png}));
  await page.goto('/');
  const card = page.locator('.anime-card');
  await expect(card).toHaveCount(1);
  await card.getByText('集數來源與確認時間', {exact: true}).click();
  await expect(card.locator('.source-facts')).toContainText('最近更新失敗，保留上次成功資料');
  await expect(card.locator('.source-facts')).toContainText('2026-10-14');
  await expect(card.locator('.source-facts')).toContainText('2026/10/15');
  await expect(card.locator('.source-facts')).toContainText('2026/10/16');
  await expect(card.locator('.episode-details')).toContainText('不含特別篇');
  await expect(card.getByRole('link', {name: /AniList 條目/})).toHaveAttribute('href', 'https://anilist.co/anime/197754');
  await expect(card.locator('.story-source')).toHaveText('簡介來源：Bangumi 原文快照（截至 2026-10-14）');
  await expect(card.locator('.story-source')).toHaveAttribute('href', 'https://bgm.tv/subject/580133');
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
});
