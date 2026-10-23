// Keep mocked response bytes and public version metadata consistent. Real data
// tests use the actual build unchanged; fault tests can override individual routes.
const {createHash} = require('node:crypto');
const {readFileSync} = require('node:fs');

function fixtureRelease(payloadForQuarter) {
  const release = JSON.parse(readFileSync('dist/release.json', 'utf8'));
  const index = JSON.parse(readFileSync('dist/search-index.json', 'utf8'));
  const bodies = {};
  for (const quarter of Object.keys(release.quarter_versions)) {
    const payload = typeof payloadForQuarter === 'function' ? payloadForQuarter(quarter) : payloadForQuarter[quarter];
    bodies[quarter] = payload === undefined ? readFileSync(`dist/data/${quarter}.json`, 'utf8') : JSON.stringify(payload);
  }
  release.quarter_versions = Object.fromEntries(Object.entries(bodies).map(([quarter, body]) => [quarter, createHash('sha256').update(body).digest('hex')]));
  const canonical = JSON.stringify(Object.fromEntries(Object.entries(release.quarter_versions).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)))
    .replace(/[^\x00-\x7f]/g, character => '\\u' + character.charCodeAt(0).toString(16).padStart(4, '0'));
  release.data_version = createHash('sha256').update(canonical).digest('hex').slice(0, 16);
  Object.assign(index, {data_version: release.data_version, quarter_versions: release.quarter_versions});
  return {release, index, bodies};
}

async function installDatasetFixture(page, payloadForQuarter) {
  const fixture = fixtureRelease(payloadForQuarter);
  const csp = readFileSync('_headers', 'utf8').match(/Content-Security-Policy:\s*(.+)/)[1].trim();
  const escape = value => value.replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;');
  await page.route(/^http:\/\/127\.0\.0\.1:4173\/(?:\?.*)?$/, async route => {
    const response = await route.fetch();
    const body = (await response.text())
      .replace(/data-data-version="[^"]*"/, `data-data-version="${fixture.release.data_version}"`)
      .replace(/data-quarter-versions="[^"]*"/, `data-quarter-versions="${escape(JSON.stringify(fixture.release.quarter_versions))}"`);
    await route.fulfill({response, body, headers: {...response.headers(), 'content-security-policy': csp}});
  });
  await page.route('**/data/*.json*', route => {
    const quarter = decodeURIComponent(new URL(route.request().url()).pathname).split('/').at(-1).replace(/\.json$/, '');
    return route.fulfill({contentType: 'application/json', body: fixture.bodies[quarter]});
  });
  await page.route('**/release.json*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify(fixture.release)}));
  return fixture;
}

exports.fixtureRelease = fixtureRelease;
exports.installDatasetFixture = installDatasetFixture;
