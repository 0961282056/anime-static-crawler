import {expect, test} from '@playwright/test';
import {installDatasetFixture} from '../helpers/versioned-data.cjs';

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');
const longTitle = '很長的動畫名稱與WithoutSpacesForWrapping測試'.repeat(3);
const story = '這是需要上下捲動才能完整閱讀的作品簡介。'.repeat(160);

test.beforeEach(async ({page}) => {
  await page.addInitScript(() => { try { localStorage.clear(); } catch { /* Storage denial is an explicit test case. */ } });
  await installDatasetFixture(page, () => ({anime_list: Array.from({length: 20}, (_, i) => ({
    bangumi_id: `anime-${9900 + i}`, anime_name: `${i + 1} ${longTitle}`,
    original_name: `Interaction ${i + 1}`, story,
    premiere_date: '一', premiere_time: '20:00',
    anime_image_url: `https://res.cloudinary.com/demo/image/upload/v1/anime_covers/${'a'.repeat(64)}`,
  }))}));
  await page.route('https://res.cloudinary.com/**', route => route.fulfill({contentType: 'image/png', body: png}));
});

async function fillShareList(page, count = 20) {
  await page.goto('/');
  await expect(page.locator('.anime-card')).toHaveCount(20);
  const add = page.getByRole('button', {name: /加入清單/});
  for (let i = 0; i < count; i++) await add.nth(i).click();
  await expect(page.locator('.share-item')).toHaveCount(count);
}

for (const viewport of [{width: 1280, height: 720}, {width: 1280, height: 480}, {width: 960, height: 540}]) {
  test(`desktop sidebar scrolls to its export controls with 20 long titles at ${viewport.width}x${viewport.height}`, async ({page}) => {
    await page.setViewportSize(viewport);
    await fillShareList(page);
    const panel = page.getByRole('complementary', {name: '篩選與分享清單'});
    await panel.scrollIntoViewIfNeeded();
    const box = await panel.boundingBox();
    expect(box).not.toBeNull();
    await page.mouse.move(box.x + 6, box.y + 30);
    await page.mouse.wheel(0, 1200);
    await expect.poll(() => panel.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
    await expect(page.locator('#copyButton')).toBeInViewport({ratio: 1});
    await expect(page.locator('#copyButton')).toBeEnabled();
    await expect(page.locator('.share-count')).toHaveText('20 / 20');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test('keyboard can scroll both regions and remove the last item without losing focus', async ({page}) => {
  await page.setViewportSize({width: 1280, height: 480});
  await fillShareList(page);
  const panel = page.getByRole('complementary', {name: '篩選與分享清單'});
  await panel.focus();
  await page.keyboard.press('End');
  await expect.poll(() => panel.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
  const list = page.getByRole('region', {name: '分享清單', exact: true});
  await list.focus();
  await page.keyboard.press('Home');
  await expect.poll(() => list.evaluate(element => element.scrollTop)).toBe(0);
  await page.keyboard.press('End');
  await expect.poll(() => list.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
  await page.locator('#copyButton').focus();
  await page.keyboard.press('Shift+Tab');
  await expect(page.locator('.share-remove').last()).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('.share-item')).toHaveCount(19);
  await expect(page.locator('.share-remove').last()).toBeFocused();
  await expect(page.locator('.share-remove').last()).toBeInViewport({ratio: 1});
});

for (const viewport of [{width: 360, height: 640}, {width: 390, height: 844}, {width: 844, height: 390}]) {
  test(`mobile page and bounded share list remain usable at ${viewport.width}x${viewport.height}`, async ({page}) => {
    await page.setViewportSize(viewport);
    await fillShareList(page);
    const list = page.getByRole('region', {name: '分享清單', exact: true});
    const listBox = await list.boundingBox();
    expect(listBox.height).toBeLessThanOrEqual(Math.min(300, viewport.height * 0.4) + 2);
    expect(await list.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
    expect(await page.locator('.share-item-title').last().evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    const copy = page.locator('#copyButton');
    await copy.scrollIntoViewIfNeeded();
    await expect(copy).toBeInViewport({ratio: 1});
    expect(await page.evaluate(() => scrollY)).toBeGreaterThan(0);
    const remove = page.locator('.share-remove').last();
    await remove.scrollIntoViewIfNeeded();
    const removeBox = await remove.boundingBox();
    expect(removeBox.width).toBeGreaterThanOrEqual(44);
    expect(removeBox.height).toBeGreaterThanOrEqual(44);
    await remove.click();
    await expect(page.locator('.share-item')).toHaveCount(19);
    await expect(copy).toBeEnabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test('a storage warning cannot hide sidebar recovery or export controls', async ({page}) => {
  await page.setViewportSize({width: 1280, height: 480});
  await page.addInitScript(() => Object.defineProperty(window, 'localStorage', {
    configurable: true, get() { throw new Error('storage unavailable'); },
  }));
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await fillShareList(page, 3);
  const dismiss = page.getByRole('button', {name: '知道了', exact: true});
  await dismiss.scrollIntoViewIfNeeded();
  await expect(dismiss).toBeInViewport({ratio: 1});
  await dismiss.click();
  await expect(dismiss).toBeHidden();
  await page.locator('#copyButton').scrollIntoViewIfNeeded();
  await expect(page.locator('#copyButton')).toBeInViewport({ratio: 1});
  expect(pageErrors).toEqual([]);
});

test('story modal covers the back-to-top control instead of allowing background clicks', async ({page}) => {
  await page.setViewportSize({width: 360, height: 640});
  await page.goto('/');
  await page.locator('.story-box').last().click();
  await expect(page.getByRole('dialog')).toBeVisible();
  const top = page.locator('#backToTopBtn');
  await expect(top).toHaveClass(/show/);
  const box = await top.boundingBox();
  expect(await page.evaluate(({x, y}) => Boolean(
    document.elementFromPoint(x, y)?.closest('.swal2-container')
  ), {x: box.x + box.width / 2, y: box.y + box.height / 2})).toBe(true);
  await page.keyboard.press('Escape');
  await expect(page.locator('.story-box').last()).toBeFocused();
});

for (const viewport of [{width: 1280, height: 480}, {width: 360, height: 640}, {width: 844, height: 390}]) {
  test(`long story scrolls inside a closable dialog at ${viewport.width}x${viewport.height}`, async ({page}) => {
    await page.setViewportSize(viewport);
    await page.goto('/');
    const trigger = page.locator('.story-box').first();
    await trigger.click();
    const content = page.getByRole('region', {name: '作品簡介內容', exact: true});
    await expect(content).toHaveText(story);
    await expect(page.getByRole('button', {name: '關閉簡介', exact: true})).toBeInViewport({ratio: 1});
    await expect(page.getByRole('button', {name: '關閉', exact: true})).toBeInViewport({ratio: 1});
    await content.focus();
    await page.keyboard.press('End');
    await expect.poll(() => content.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
    const heading = page.getByRole('heading', {name: `1 ${longTitle}`, exact: true});
    await heading.focus();
    await page.keyboard.press('End');
    if (await heading.evaluate(element => element.scrollHeight > element.clientHeight)) {
      await expect.poll(() => heading.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
    }
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(trigger).toBeFocused();
  });
}
