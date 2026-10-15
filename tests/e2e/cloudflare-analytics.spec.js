import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';

const csp = readFileSync('_headers', 'utf8')
  .match(/Content-Security-Policy:\s*(.+)/)[1].trim();

for (const endpoint of [
  { name: 'same-origin', url: '/cdn-cgi/rum' },
  { name: 'Pages cross-origin', url: 'https://cloudflareinsights.com/cdn-cgi/rum' },
]) {
  test(`Cloudflare analytics sends a ${endpoint.name} beacon under production CSP`, async ({ page }, testInfo) => {
    const origin = new URL(testInfo.project.use.baseURL).origin;
    const browserErrors = [];
    const beacons = [];
    page.on('pageerror', error => browserErrors.push(error.message));
    page.on('console', message => {
      if (message.type() === 'error') browserErrors.push(message.text());
    });
    await page.route(`${origin}/`, route => route.fulfill({
      contentType: 'text/html',
      headers: { 'content-security-policy': csp },
      body: '<!doctype html><title>Analytics CSP regression</title>'
        + '<script defer src="https://static.cloudflareinsights.com/beacon.min.js"></script>',
    }));
    await page.route('https://static.cloudflareinsights.com/beacon.min.js', route => route.fulfill({
      contentType: 'application/javascript',
      body: `fetch(${JSON.stringify(endpoint.url)}, {method: "POST", body: "csp-regression"})`
        + '.then(response => { window.analyticsBeaconDelivered = response.status === 204; });',
    }));
    await page.route(new URL(endpoint.url, origin).href, route => {
      beacons.push({ method: route.request().method(), body: route.request().postData() });
      return route.fulfill({ status: 204, headers: { 'access-control-allow-origin': origin } });
    });
    await page.goto('/');
    await expect.poll(() => page.evaluate(() => window.analyticsBeaconDelivered)).toBe(true);
    expect(beacons).toEqual([{ method: 'POST', body: 'csp-regression' }]);
    expect(browserErrors).toEqual([]);
  });
}
