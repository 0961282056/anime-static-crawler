import {installDatasetFixture} from '../helpers/versioned-data.cjs';
import {readFileSync} from 'node:fs';
import {expect, test} from '@playwright/test';

const csp = readFileSync('_headers', 'utf8').match(/Content-Security-Policy:\s*(.+)/)[1].trim();
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');
const titles = ['鍵盤作品甲', '鍵盤作品乙', '鍵盤作品丙'];

test.beforeEach(async ({page}) => {
  await page.addInitScript(() => {
    localStorage.clear();
    window.copiedTitles = [];
    Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
      writeText: async text => { window.copiedTitles.push(text); },
    }});
  });
  await page.route('http://127.0.0.1:4173/', async route => {
    const response = await route.fetch();
    await route.fulfill({response, headers: {...response.headers(), 'content-security-policy': csp}});
  });
  await installDatasetFixture(page, () => ({
      generated_at: '2026-10-20T00:00:00+08:00',
      anime_list: titles.map((title, i) => ({
        bangumi_id: `anime-${9900 + i}`, anime_name: title,
        story: `${title}的完整簡介。`.repeat(20), premiere_date: '一', premiere_time: '20:00',
        anime_image_url: `https://res.cloudinary.com/demo/image/upload/v1/anime_covers/${'a'.repeat(64)}`,
      })),
    }));
  await page.route('https://res.cloudinary.com/**', route => route.fulfill({contentType: 'image/png', body: png}));
});

// Reach the real control through sequential keyboard navigation, without locator.focus().
async function tabTo(page, target) {
  for (let n = 0; n < 45; n += 1) {
    await page.keyboard.press('Tab');
    if (await target.evaluate(element => element === document.activeElement)) return;
  }
  throw new Error('Control is not reachable with Tab');
}

for (const viewport of [{width: 1280, height: 720}, {width: 360, height: 800}]) {
  test(`title and summary support Tab, Enter and Space at ${viewport.width}px under production CSP`, async ({page}) => {
    await page.setViewportSize(viewport);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('/');
    const copy = page.getByRole('button', {name: `複製《${titles[0]}》標題`, exact: true});
    await expect(copy).toBeVisible();
    await expect(page.getByRole('heading', {name: `複製《${titles[0]}》標題`})).toBeVisible();
    await tabTo(page, copy);
    await expect(copy).toHaveCSS('outline-style', 'solid');
    await page.keyboard.press('Enter');
    await page.keyboard.press('Space');
    await expect.poll(() => page.evaluate(() => window.copiedTitles)).toEqual([titles[0], titles[0]]);
    await expect(copy).toBeFocused();
    const story = page.getByRole('button', {name: `展開《${titles[0]}》簡介`, exact: true});
    await tabTo(page, story);
    await expect(story).toHaveCSS('outline-style', 'solid');
    for (const key of ['Enter', 'Space']) {
      await page.keyboard.press(key);
      await expect(page.getByRole('dialog')).toBeVisible();
      await expect(page.locator('.swal2-html-container')).toHaveText(`${titles[0]}的完整簡介。`.repeat(20));
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog')).toHaveCount(0);
      await expect(story).toBeFocused();
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(errors).toEqual([]);
  });
}

test('keyboard removal keeps focus on the next, previous, or empty list heading', async ({page}) => {
  await page.goto('/');
  for (let i = 0; i < 3; i += 1) {
    const add = page.getByRole('button', {name: /加入清單/}).nth(i);
    await expect(add).toBeVisible();
    await tabTo(page, add);
    await page.keyboard.press('Enter');
  }
  const remove = name => page.getByRole('button', {name: `從分享清單移除《${name}》`, exact: true});
  await tabTo(page, remove(titles[1]));
  await page.keyboard.press('Enter');
  await expect(page.locator('.share-item')).toHaveCount(2);
  await expect(remove(titles[2])).toBeFocused();
  await page.keyboard.press('Space');
  await expect(page.locator('.share-item')).toHaveCount(1);
  await expect(remove(titles[0])).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('.share-item')).toHaveCount(0);
  await expect(page.getByRole('heading', {name: '分享清單', exact: true})).toBeFocused();
  await expect(page.locator('#copyButton')).toBeDisabled();
});

test('named back-to-top control returns keyboard focus to the page heading', async ({page}) => {
  await page.setViewportSize({width: 360, height: 600});
  await page.goto('/');
  await expect(page.locator('.anime-card')).toHaveCount(3);
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  const top = page.getByRole('button', {name: '回到頁面頂端', exact: true});
  await expect(top).toBeVisible();
  // It is the final control in the document's tab order.
  await page.locator('.anime-card').last().getByRole('link').last().focus();
  await page.keyboard.press('Tab');
  await expect(top).toBeFocused();
  await expect(top).toHaveCSS('outline-style', 'solid');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', {level: 1})).toBeFocused();
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
  await expect(top).toBeHidden();
});
