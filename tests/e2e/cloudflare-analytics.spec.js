import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';

const csp = readFileSync('_headers', 'utf8')
  .match(/Content-Security-Policy:\s*(.+)/)[1].trim();

test('automatically injected Cloudflare analytics sends a same-origin beacon under production CSP', async ({ page }) => {
  const browserErrors = [];
  const beacons = [];
  page.on('pageerror', error => browserErrors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error') browserErrors.push(message.text());
  });
  await page.route('http://127.0.0.1:4173/', route => route.fulfill({
    contentType: 'text/html',
    headers: { 'content-security-policy': csp },
    body: '<!doctype html><title>Analytics CSP regression</title>'
      + '<script defer src="https://static.cloudflareinsights.com/beacon.min.js"></script>',
  }));
  await page.route('https://static.cloudflareinsights.com/beacon.min.js', route => route.fulfill({
    contentType: 'application/javascript',
    body: 'fetch("/cdn-cgi/rum", {method: "POST", body: "csp-regression"})'
      + '.then(response => { window.analyticsBeaconDelivered = response.status === 204; });',
  }));
  await page.route('http://127.0.0.1:4173/cdn-cgi/rum', route => {
    beacons.push({ method: route.request().method(), body: route.request().postData() });
    return route.fulfill({ status: 204 });
  });
  await page.goto('/');
  await expect.poll(() => page.evaluate(() => window.analyticsBeaconDelivered)).toBe(true);
  expect(beacons).toEqual([{ method: 'POST', body: 'csp-regression' }]);
  expect(browserErrors).toEqual([]);
});
