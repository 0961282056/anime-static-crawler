import { expect, test } from '@playwright/test';

function observeBrowserErrors(page) {
  const errors = [];
  page.on('console', (message) => {
    if (message.type() === 'error') {
      errors.push(`console: ${message.text()}`);
    }
  });
  page.on('pageerror', (error) => {
    errors.push(`pageerror: ${error.message}`);
  });
  return errors;
}

function isQuarterDataResponse(response) {
  const url = response.url();
  return url.includes('/data/') && url.includes('.json');
}

test('homepage loads data, filters records, and renders healthy images', async ({
  page,
}) => {
  const browserErrors = observeBrowserErrors(page);
  await page.addInitScript(() => localStorage.clear());

  const initialDataResponse = page.waitForResponse(isQuarterDataResponse);
  await page.goto('/');
  await expect(page).toHaveTitle('動畫新番資訊站');
  await expect(page.getByRole('heading', { name: '📺 動畫新番資訊站' })).toBeVisible();

  const response = await initialDataResponse;
  expect(response.ok()).toBe(true);
  expect(response.headers()['content-type']).toContain('application/json');

  const resultCount = page.locator('#resultCount');
  await expect.poll(async () => Number(await resultCount.textContent())).toBeGreaterThan(0);
  const initialCount = Number(await resultCount.textContent());

  const firstTitle = await page.locator('.anime-title').first().textContent();
  expect(firstTitle?.trim()).toBeTruthy();
  await page.getByLabel('關鍵字搜尋').fill(firstTitle.trim());
  await expect(resultCount).toHaveText('1');
  await page.getByLabel('關鍵字搜尋').fill('');
  await expect(resultCount).toHaveText(String(initialCount));

  const yearSelect = page.getByLabel('年份');
  const yearValues = await yearSelect.locator('option').evaluateAll((options) =>
    options.map((option) => option.value),
  );
  expect(yearValues.length).toBeGreaterThan(1);
  const currentYear = await yearSelect.inputValue();
  const alternateYear = yearValues.find((value) => value !== currentYear);
  expect(alternateYear).toBeTruthy();

  const alternateDataResponse = page.waitForResponse(isQuarterDataResponse);
  await yearSelect.selectOption(alternateYear);
  expect((await alternateDataResponse).ok()).toBe(true);
  await expect(yearSelect).toHaveValue(alternateYear);
  await expect.poll(async () => Number(await resultCount.textContent())).toBeGreaterThan(0);

  const images = page.locator('.card-img');
  const imageCount = await images.count();
  expect(imageCount).toBeGreaterThan(0);
  for (let index = 0; index < Math.min(imageCount, 6); index += 1) {
    const image = images.nth(index);
    await image.scrollIntoViewIfNeeded();
    await expect
      .poll(() => image.evaluate((element) => element.complete && element.naturalWidth > 0))
      .toBe(true);
  }

  expect(browserErrors).toEqual([]);
});
