import {expect, test} from '@playwright/test';
import {readFileSync} from 'node:fs';

const csp = readFileSync('_headers', 'utf8').match(/Content-Security-Policy:\s*(.+)/)[1].trim();
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');
const record = (id, name) => ({
  bangumi_id: `anime-${id}`, anime_name: name, story: name,
  anime_image_url: `https://res.cloudinary.com/demo/image/upload/v1/anime_covers/${'a'.repeat(64)}`,
  premiere_date: '一', premiere_time: '20:00',
});

async function installPage(page) {
  await page.addInitScript(() => localStorage.clear());
  await page.route('http://127.0.0.1:4173/', async route => {
    const response = await route.fetch();
    await route.fulfill({response, headers: {...response.headers(), 'content-security-policy': csp}});
  });
  await page.route('https://res.cloudinary.com/**', route => route.fulfill({
    contentType: 'image/png', headers: {'access-control-allow-origin': '*'}, body: png,
  }));
}

test('clearing a global search aborts its real fetch and releases pending state under CSP', async ({page}) => {
  await installPage(page);
  const current = record(9998, '目前季度');
  const historical = record(9999, 'historical needle');
  const index = JSON.parse(readFileSync('dist/search-index.json', 'utf8'));
  index.entries = [{identity: [historical.bangumi_id, historical.anime_name, '', null], story: historical.story, quarters: ['2025_夏']}];
  await page.route('**/search-index.json*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify(index)}));
  let release;
  const failed = [];
  page.on('requestfailed', request => failed.push(decodeURIComponent(request.url())));
  await page.route('**/data/*.json*', async route => {
    const isOld = decodeURIComponent(route.request().url()).includes('2025_夏');
    if (isOld) await new Promise(resolve => { release = resolve; });
    try {
      await route.fulfill({contentType: 'application/json', body: JSON.stringify({anime_list: [isOld ? historical : current]})});
    } catch {
      // The test deliberately cancels this intercepted historical request.
    }
  });
  try {
    await page.goto('/');
    await expect(page.locator('.anime-title')).toHaveText('目前季度');
    await page.getByLabel('關鍵字搜尋').fill('needle');
    await expect.poll(() => Boolean(release)).toBe(true);
    await page.getByLabel('關鍵字搜尋').fill('');
    await expect(page.locator('.anime-title')).toHaveText('目前季度');
    await expect.poll(() => failed.some(url => url.includes('2025_夏'))).toBe(true);
    await expect.poll(() => page.evaluate(() => Alpine.$data(document.querySelector('[x-data]'))._pendingDataRequests.size)).toBe(0);
    expect(await page.evaluate(() => Boolean(Alpine.$data(document.querySelector('[x-data]')).dataCache['2025_夏']))).toBe(false);
    await expect(page.getByRole('alert')).toBeHidden();
  } finally {
    release?.();
  }
});

for (const stage of ['fonts', 'renderer']) {
  test(`share ${stage} timeout restores the real UI and removes temporary DOM`, async ({page}) => {
    await installPage(page);
    await page.clock.install();
    await page.addInitScript(stage => {
      window.html2canvas = () => {
        window.__renderStarted = true;
        return new Promise(() => {});
      };
      if (stage === 'fonts') Object.defineProperty(document.fonts, 'ready', {get() {
        window.__fontWaitStarted = true;
        return new Promise(() => {});
      }});
    }, stage);
    await page.route('**/data/*.json*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({anime_list: [record(9999, '分享期限測試')]})}));
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('/');
    await expect(page.locator('.anime-title')).toHaveText('分享期限測試');
    await page.getByRole('button', {name: /加入清單/}).first().click();
    await page.locator('#copyButton').click();
    await expect.poll(() => page.evaluate(stage => stage === 'fonts' ? window.__fontWaitStarted : window.__renderStarted, stage)).toBe(true);
    await expect(page.locator('#copyButton')).toBeDisabled();
    await page.clock.fastForward(stage === 'fonts' ? 16000 : 31000);
    await expect(page.locator('.swal2-html-container')).toContainText(stage === 'fonts' ? 'SHARE_FONT_TIMEOUT' : 'SHARE_RENDER_TIMEOUT');
    await expect(page.locator('#copyButton')).toBeEnabled();
    await expect(page.locator('.share-item')).toHaveCount(1);
    await expect(page.locator('[data-anime-share-export]')).toHaveCount(0);
    expect(errors).toEqual([]);
  });
}

test('large searches show complete totals, expand with the keyboard and filter beyond the first batch', async ({page}) => {
  await installPage(page);
  const current = record(8000, '當季作品');
  const records = Array.from({length: 125}, (_, i) => ({...record(9000 + i, `common-${i}`), premiere_date: i >= 120 ? '二' : '一'}));
  const index = JSON.parse(readFileSync('dist/search-index.json', 'utf8'));
  index.entries = records.map(item => ({identity: [item.bangumi_id, item.anime_name, '', null], story: item.story, quarters: ['2025_夏']}));
  await page.route('**/search-index.json*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify(index)}));
  await page.route('**/data/*.json*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({anime_list: decodeURIComponent(route.request().url()).includes('2025_夏') ? records : [current]})}));
  await page.goto('/');
  await expect(page.locator('.anime-title')).toHaveText('當季作品');
  await page.getByLabel('關鍵字搜尋').fill('common');
  await expect(page.locator('#resultCount')).toHaveText('125');
  await expect(page.locator('.anime-card')).toHaveCount(60);
  const more = page.getByRole('button', {name: '顯示更多', exact: true});
  await more.focus(); await page.keyboard.press('Enter');
  await expect(page.locator('.anime-card')).toHaveCount(120);
  await expect(page.locator('.title-copy').nth(60)).toBeFocused();
  await expect(page.locator('.search-pagination')).toContainText('120／共 125');
  await more.focus(); await page.keyboard.press('Space');
  await expect(page.locator('.anime-card')).toHaveCount(125);
  await expect(page.locator('.title-copy').nth(120)).toBeFocused();
  await expect(more).toBeHidden();
  await page.getByLabel('每週放送').selectOption('二');
  await expect(page.locator('#resultCount')).toHaveText('5');
  await expect(page.locator('.anime-card')).toHaveCount(5);
  await expect(page.locator('.anime-title').first()).toHaveText('common-120');
  await expect(more).toBeHidden();
  await page.getByLabel('每週放送').selectOption('全部');
  await expect(page.locator('.anime-card')).toHaveCount(60);
  await page.getByLabel('關鍵字搜尋').fill('');
  await expect(page.locator('.anime-title')).toHaveText('當季作品');
  await expect(more).toBeHidden();
});
