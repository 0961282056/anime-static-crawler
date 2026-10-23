// Local lab measurement only: synthetic covers, no crawler or Cloudinary writes.
import {chromium} from '@playwright/test';
import {readFileSync, mkdirSync, writeFileSync} from 'node:fs';

const baseURL = 'http://127.0.0.1:4173';
const keyword = '的';
const csp = readFileSync('_headers', 'utf8').match(/Content-Security-Policy:\s*(.+)/)[1].trim();
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');
const browser = await chromium.launch({
  ...(process.env.CHROMIUM_EXECUTABLE ? {executablePath: process.env.CHROMIUM_EXECUTABLE} : {}),
});
try {
  const page = await browser.newPage({viewport: {width: 360, height: 800}});
  const session = await page.context().newCDPSession(page);
  await session.send('Emulation.setCPUThrottlingRate', {rate: 4});
  await session.send('Performance.enable');
  await page.route(/https:\/\/(res\.cloudinary\.com|placehold\.co)\//, route => route.fulfill({contentType: 'image/png', body: png}));
  await page.route(`${baseURL}/`, async route => {
    const response = await route.fetch();
    await route.fulfill({response, headers: {...response.headers(), 'content-security-policy': csp}});
  });
  await page.addInitScript(() => {
    localStorage.clear();
    window.__profileLongTasks = [];
    new PerformanceObserver(list => window.__profileLongTasks.push(...list.getEntries().map(entry => entry.duration))).observe({entryTypes: ['longtask']});
  });
  await page.goto(baseURL);
  await page.locator('.anime-card').first().waitFor({timeout: 30000});
  const started = Date.now();
  await page.getByLabel('關鍵字搜尋').fill(keyword);
  await page.waitForFunction(() => {
    const data = Alpine.$data(document.querySelector('[x-data]'));
    if (data.loading || data.loadError) return false;
    const total = Number(document.getElementById('resultCount').textContent);
    return total > 0 && document.querySelectorAll('.anime-card').length === Math.min(total, data.visibleCount || total);
  }, {}, {timeout: 60000});
  const elapsed = Date.now() - started;
  const metrics = (await session.send('Performance.getMetrics')).metrics;
  const report = {
    scenario: 'Local 360px viewport, 4x CPU slowdown, real data, synthetic cover images, production CSP; not field telemetry or a power measurement',
    browser: browser.version(),
    keyword,
    search_wall_ms: elapsed,
    js_heap_used_bytes: metrics.find(metric => metric.name === 'JSHeapUsedSize')?.value,
    ...await page.evaluate(() => ({
      matched_results: Number(document.getElementById('resultCount').textContent),
      rendered_cards: document.querySelectorAll('.anime-card').length,
      dom_nodes: document.querySelectorAll('*').length,
      max_long_task_ms: Math.max(0, ...window.__profileLongTasks),
      long_task_ms: window.__profileLongTasks.reduce((a, b) => a + b, 0),
    })),
  };
  mkdirSync('audit-output', {recursive: true});
  writeFileSync('audit-output/frontend-profile.json', JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));
} finally {
  await browser.close();
}
