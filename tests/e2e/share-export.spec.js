import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';

const headersSource = readFileSync('_headers', 'utf8');
const csp = headersSource.match(/Content-Security-Policy:\s*(.+)/)[1].trim();
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');

test('share export loads the integrity-pinned renderer on demand under production CSP', async ({page}) => {
  test.setTimeout(60000);
  const rendererRequests = [];
  const browserErrors = [];
  page.on('request', request => {
    if (request.url().includes('html2canvas')) rendererRequests.push(request.url());
  });
  page.on('pageerror', error => browserErrors.push(error.message));
  await page.addInitScript(() => {
    localStorage.clear();
    Object.defineProperty(navigator, 'clipboard', {configurable: true, value: undefined});
  });
  await page.route('http://127.0.0.1:4173/', async route => {
    const response = await route.fetch();
    await route.fulfill({response, headers: {...response.headers(), 'content-security-policy': csp}});
  });
  await page.route('**/data/*.json*', route => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      generated_at: '2026-09-30T00:00:00+08:00',
      anime_list: [{
        bangumi_id: 'anime-9999', anime_name: '分享回歸測試', story: '測試簡介',
        premiere_date: '一', premiere_time: '20:00',
        anime_image_url: `https://res.cloudinary.com/demo/image/upload/f_auto,q_auto:best/v1/anime_covers/${'a'.repeat(64)}`,
      }],
    }),
  }));
  await page.route('https://res.cloudinary.com/**', route => route.fulfill({
    contentType: 'image/png', headers: {'access-control-allow-origin': '*'}, body: png,
  }));
  await page.goto('/');
  await expect(page.locator('.anime-title').first()).toHaveText('分享回歸測試');
  expect(rendererRequests).toEqual([]);
  await page.getByRole('button', {name: /加入清單/}).first().click();
  const downloadPromise = page.waitForEvent('download');
  await page.locator('#copyButton').click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^anime_list_\d+\.png$/);
  const bytes = readFileSync(await download.path());
  expect(bytes.subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a');
  await expect(page.locator('#copyButton')).toBeEnabled();
  expect(rendererRequests).toHaveLength(1);
  expect(browserErrors).toEqual([]);
});

for (const failure of [
  {target: 'renderer', code: 'SHARE_RENDERER_NETWORK', advice: '檢查網路'},
  {target: 'image', code: 'SHARE_IMAGE_LOAD', advice: '圖片服務'},
]) {
  test(`share ${failure.target} failure explains recovery and restores the button under production CSP`, async ({page}) => {
    const browserErrors = [];
    page.on('pageerror', error => browserErrors.push(error.message));
    await page.addInitScript(() => {
      localStorage.clear();
      Object.defineProperty(navigator, 'clipboard', {configurable: true, value: undefined});
    });
    await page.route('http://127.0.0.1:4173/', async route => {
      const response = await route.fetch();
      await route.fulfill({response, headers: {...response.headers(), 'content-security-policy': csp}});
    });
    await page.route('**/data/*.json*', route => route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({anime_list: [{
        anime_name: '分享故障回歸',
        anime_image_url: `https://res.cloudinary.com/demo/image/upload/v1/anime_covers/${'a'.repeat(64)}`,
      }]}),
    }));
    let failedImageRequests = 0;
    await page.route('https://res.cloudinary.com/**', route => {
      const broken = failure.target === 'image' && decodeURIComponent(route.request().url()).includes('q_auto:best');
      if (broken) failedImageRequests += 1;
      return route.fulfill({
        status: broken ? 404 : 200,
        contentType: broken ? 'text/plain' : 'image/png',
        headers: {'access-control-allow-origin': '*'}, body: broken ? 'Image not found' : png,
      });
    });
    if (failure.target === 'renderer') {
      await page.route('**/html2canvas.min.js', route => route.abort('failed'));
    }
    await page.goto('/');
    await expect(page.locator('.anime-title').first()).toHaveText('分享故障回歸');
    await page.getByRole('button', {name: /加入清單/}).first().click();
    await page.locator('#copyButton').click();
    await expect(page.locator('.swal2-html-container')).toContainText(failure.code);
    await expect(page.locator('.swal2-html-container')).toContainText(failure.advice);
    await expect(page.locator('#copyButton')).toBeEnabled();
    await expect(page.locator('.share-item')).toHaveCount(1);
    if (failure.target === 'image') expect(failedImageRequests).toBeGreaterThan(0);
    expect(browserErrors).toEqual([]);
  });
}
