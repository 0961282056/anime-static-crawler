import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../static/js/main.js', import.meta.url), 'utf8');

function createContext({storage = null, fetchImpl = null, html2canvas = null, navigatorImpl = null, swal = null} = {}) {
  const scripts = [];
  const button = { innerHTML: '生成圖片', disabled: false };
  const configElement = {
    dataset: {
      defaultYear: '2026',
      defaultSeason: '秋',
      buildVersion: 'test-build',
      officialStorySources: JSON.stringify(Object.fromEntries(JSON.parse(readFileSync(new URL('../../services/reviewed_story_sources.json', import.meta.url), 'utf8')).map(row => [row.card_id, [row.subject_id, row.source_url]]))),
      availableData: JSON.stringify({ 2025: ['夏'], 2026: ['秋'] }),
    },
  };
  const body = {
    appendChild(element) {
      element.parentNode = body;
    },
    removeChild(element) {
      element.parentNode = null;
    },
    contains(element) {
      return element.parentNode === body;
    },
  };
  const document = {
    body,
    head: {
      appendChild(script) {
        script.parentNode = document.head;
        scripts.push(script);
      },
      removeChild(script) {
        script.parentNode = null;
      },
    },
    fonts: { ready: Promise.resolve() },
    getElementById(id) {
      if (id === 'app-config') return configElement;
      if (id === 'copyButton') return button;
      return null;
    },
    querySelector(selector) {
      if (selector === 'script[data-anime-html2canvas="true"]') {
        return scripts.find(script => script.parentNode === document.head) || null;
      }
      return null;
    },
    createElement(tagName) {
      if (tagName === 'script') {
        return { dataset: {}, parentNode: null };
      }
      return {
        style: {},
        dataset: {},
        parentNode: null,
        appendChild() {},
        append() {},
        querySelectorAll() { return []; },
      };
    },
    addEventListener() {},
  };
  const window = {
    location: { origin: 'https://example.test' },
    URL,
    html2canvas,
    scrollTo() {},
    addEventListener() {},
  };
  const context = {
    AbortController,
    URL,
    console: { log() {}, warn() {}, error() {} },
    Date,
    Promise,
    Math,
    Number,
    JSON,
    Error,
    encodeURIComponent,
    setTimeout,
    clearTimeout,
    document,
    window,
    navigator: navigatorImpl,
    fetch: (...args) => fetchImpl(...args),
    Swal: swal || undefined,
  };
  if (storage !== null) context.localStorage = storage;
  context.globalThis = context;
  vm.runInNewContext(
    `${source}\n globalThis.__animeAppFactory = animeApp; globalThis.__frontendHooks = {loadHtml2Canvas, buildCloudinaryTransformationUrl};`,
    context,
    { filename: 'main.js' },
  );
  const app = context.__animeAppFactory();
  app.$nextTick = callback => callback();
  return { context, app, button, scripts };
}

function dataset(name, generatedAt = '2026-09-30T00:00:00+08:00') {
  return {
    schema_version: 1,
    generated_at: generatedAt,
    anime_list: [{
      bangumi_id: `anime-${name}`,
      anime_name: name,
      anime_image_url: `https://res.cloudinary.com/demo/image/upload/f_auto,q_auto:best/v1/anime_covers/${'a'.repeat(64)}`,
      premiere_date: '一',
      premiere_time: '20:00',
      story: `${name} story`,
    }],
  };
}

function responseFor(payload) {
  return { ok: true, status: 200, json: async () => payload };
}

function errorResponse(status) {
  return { ok: false, status, json: async () => ({ ignored: true }) };
}

function nextTick() {
  return new Promise(resolve => setTimeout(resolve, 0));
}

test('episode labels distinguish planned, confirmed, finished and missing progress', () => {
  const {app} = createContext();
  const base = {total_episodes: 12, aired_episodes: 8, airing_status: 'airing', episode_count_status: 'confirmed', fetch_state: 'ok'};
  assert.equal(app.episodeCountLabel({episodes: base}), '已播 8／12 集');
  assert.equal(app.episodeCountLabel({episodes: {...base, episode_count_status: 'planned'}}), '已播 8 集／預定 12 集');
  assert.equal(app.episodeCountLabel({episodes: {...base, total_episodes: null}}), '已播 8 集／總集數未定');
  assert.equal(app.episodeCountLabel({episodes: {...base, aired_episodes: null}}), '共 12 集／進度待確認');
  assert.equal(app.episodeCountLabel({episodes: {...base, aired_episodes: 12, airing_status: 'finished'}}), '全 12 集');
  assert.equal(app.airingStatusLabel({episodes: {...base, airing_status: 'finished'}}), '本季已播畢');
  assert.equal(app.episodeCountLabel({}), '集數待確認');
  assert.equal(app.episodeCountLabel({episodes: {...base, fetch_state: 'conflict'}}), '已播 8 集／總集數待確認');
  assert.match(app.episodeUpdateWarning({episodes: {...base, checked_at: '2026-10-13T18:00:00+08:00', fetch_state: 'stale'}}), /資料可能落後/);
  assert.equal(app.episodeSourceUrl({external_ids: {bangumi: 580133}}), 'https://bgm.tv/subject/580133/ep');
  assert.equal(app.episodeSourceUrl({external_ids: {bangumi: 'javascript:alert(1)'}}), null);
});

test('official evidence links take priority and unsafe schemes cannot become links', () => {
  const {app} = createContext();
  const anime = {external_ids: {bangumi: 580133}, episodes: {source_urls: ['https://chiramune.com/bd/06.html']}};
  assert.equal(app.episodeSourceUrl(anime), 'https://chiramune.com/bd/06.html');
  for (const source of ['javascript:alert(1)', 'http://evil.example/', 'https://user:pass@evil.example/', 'invalid']) {
    assert.equal(app.episodeSourceUrl({...anime, episodes: {source_urls: [source]}}), 'https://bgm.tv/subject/580133/ep');
  }
});

test('source details distinguish failed attempts, fixed snapshots and reference links', () => {
  const {app} = createContext();
  const anime = {external_ids: {bangumi: 100, anilist: 200}, episodes: {source_kind: 'bangumi_archive', checked_at: '2026-10-14T10:00:00+08:00', attempted_at: '2026-10-16T10:00:00+08:00', fetch_state: 'stale', note: '', source_urls: ['javascript:alert(1)', 'https://bgm.tv/subject/100', 'https://bgm.tv/subject/100']}};
  assert.equal(app.episodeSourceKind(anime), 'Bangumi 固定資料快照');
  assert.match(app.episodeVerificationState(anime), /保留上次成功資料/);
  const links = app.episodeSourceLinks(anime);
  assert.equal(links.length, 2);
  assert.match(links[1].label, /參考；不代表計數範圍已一致/);
  assert.equal(app.formatSourceTimestamp('invalid'), '尚未記錄');
  assert.equal(app.episodeVerificationState({episodes: {checked_at: 'invalid'}}), '尚未成功查證');
  const firstConflict = {episodes: {fetch_state: 'conflict', checked_at: null, attempted_at: '2026-10-18T10:00:00+08:00'}};
  assert.equal(app.episodeVerificationState(firstConflict), '尚未成功查證');
  assert.equal(app.formatSourceTimestamp(firstConflict.episodes.checked_at), '尚未記錄');
  assert.notEqual(app.formatSourceTimestamp(firstConflict.episodes.attempted_at), '尚未記錄');
});

test('summary attribution never links a different subject or claims a fresh archive', () => {
  const {app} = createContext();
  const anime = {external_ids: {bangumi: 100}, story_source: {subject_id: 100, method: 'bangumi_archive', source_snapshot_date: '2026-10-14'}};
  assert.equal(app.storySourceUrl(anime), 'https://bgm.tv/subject/100');
  assert.match(app.storySourceLabel(anime), /截至 2026-10-14/);
  assert.equal(app.storySourceUrl({...anime, external_ids: {bangumi: 200}}), null);
});

test('official summary excerpts link the reviewed season and reject arbitrary URLs', () => {
  const {app} = createContext();
  const anime = {bangumi_id: 'anime-2289', external_ids: {bangumi: 471844}, story_source: {
    provider: 'official', method: 'manual_official', subject_id: 471844,
    source_url: 'https://www.fujitv.co.jp/b_hp/returners-magic2/index.html'
  }};
  assert.equal(app.storySourceUrl(anime), anime.story_source.source_url);
  assert.match(app.storySourceLabel(anime), /官方作品介紹原文（節錄）/);
  assert.equal(app.storySourceUrl({...anime, bangumi_id: 'anime-999'}), null);
  assert.equal(app.storySourceUrl({...anime, story_source: {...anime.story_source, source_url: 'javascript:alert(1)'}}), null);
  assert.equal(app.storySourceUrl({...anime, external_ids: {bangumi: 1}}), null);
});

test('Classica Loid second-series excerpt links only the reviewed official identity', () => {
  const {app} = createContext();
  const anime = {bangumi_id: 'anime-624', external_ids: {bangumi: 212315}, story_source: {
    provider: 'official', method: 'manual_official', subject_id: 212315,
    source_url: 'https://www.classicaloid.net/introduction/'
  }};
  assert.equal(app.storySourceUrl(anime), anime.story_source.source_url);
  assert.equal(app.storySourceUrl({...anime, bangumi_id: 'anime-623'}), null);
  assert.equal(app.storySourceUrl({...anime, external_ids: {bangumi: 180749}}), null);
});

test('every reviewed official excerpt has a working link bound to its own version', () => {
  const {app} = createContext();
  const reviews = JSON.parse(readFileSync(new URL('../../services/reviewed_story_sources.json', import.meta.url), 'utf8'));
  for (const row of reviews) {
    const anime = {bangumi_id: row.card_id, external_ids: {bangumi: row.subject_id}, story_source: {
      provider: 'official', method: 'manual_official', subject_id: row.subject_id,
      source_url: row.source_url
    }};
    assert.equal(app.storySourceUrl(anime), row.source_url, row.card_id);
    for (const other of reviews.filter(item => item.card_id !== row.card_id)) {
      assert.equal(app.storySourceUrl({...anime, bangumi_id: other.card_id}), null);
    }
  }
});

test('airing filter combines with weekday and keyword while supporting legacy records', () => {
  const {app} = createContext();
  app.rawAnimeList = [
    {anime_name: 'first', premiere_date: '一', episodes: {airing_status: 'airing'}},
    {anime_name: 'second', premiere_date: '一', episodes: {airing_status: 'finished'}},
    {anime_name: 'legacy', premiere_date: '二'},
  ];
  app.filterStatus = 'airing';
  app.filterDay = '一';
  app.searchKeyword = 'first';
  assert.equal(app.filteredAnime.length, 1);
  assert.equal(app.filteredAnime[0].anime_name, 'first');
  app.filterStatus = 'unknown';
  app.filterDay = '全部';
  app.searchKeyword = '';
  assert.equal(app.filteredAnime[0].anime_name, 'legacy');
});

test('bad episode counts and impossible finished status fail validation', async () => {
  for (const episodes of [
    {total_episodes: 12, aired_episodes: 13, airing_status: 'airing', episode_count_status: 'confirmed', fetch_state: 'ok'},
    {total_episodes: 12, aired_episodes: 8, airing_status: 'finished', episode_count_status: 'confirmed', fetch_state: 'ok'},
    {total_episodes: '12', aired_episodes: 8, airing_status: 'airing', episode_count_status: 'planned', fetch_state: 'ok'},
  ]) {
    const payload = dataset('bad-count');
    payload.anime_list[0].episodes = episodes;
    const {app} = createContext({fetchImpl: async () => responseFor(payload)});
    app.year = '2026'; app.season = '秋';
    await app.loadData();
    assert.equal(app.loadError.code, 'DATA_SCHEMA');
    assert.equal(app.rawAnimeList.length, 0);
  }
});

test('summary dialog treats source titles and summaries as literal text', () => {
  const calls = [];
  const {app} = createContext({swal: {fire: options => calls.push(options)}});
  const title = '<a href="https://example.test/">作品 & 特別篇</a>';
  const story = '<b>原文簡介</b>';
  app.showStory(title, story);
  assert.equal(calls[0].titleText, title);
  assert.equal(calls[0].title, undefined);
  assert.equal(calls[0].text, story);
});

for (const episodes of [
  {total_episodes: null, aired_episodes: 0, airing_status: 'not_yet_aired', episode_count_status: 'planned', fetch_state: 'ok'},
  {total_episodes: null, aired_episodes: 1, airing_status: 'airing', episode_count_status: 'confirmed', fetch_state: 'ok'},
  {total_episodes: 12, aired_episodes: 1, airing_status: 'not_yet_aired', episode_count_status: 'planned', fetch_state: 'ok'},
]) {
  test(`contradictory episode metadata is rejected: ${JSON.stringify(episodes)}`, async () => {
    const payload = dataset('contradictory');
    payload.anime_list[0].episodes = episodes;
    const {app} = createContext({fetchImpl: async () => responseFor(payload)});
    app.year = '2026'; app.season = '秋';
    await app.loadData();
    assert.equal(app.loadError?.code, 'DATA_SCHEMA');
    assert.equal(app.rawAnimeList.length, 0);
    assert.equal(Object.keys(app.dataCache).length, 0);
  });
}

test('unknown totals and zero unaired progress remain valid', async () => {
  for (const episodes of [
    {total_episodes: null, aired_episodes: null, airing_status: 'unknown', episode_count_status: 'unknown', fetch_state: 'unmatched'},
    {total_episodes: null, aired_episodes: 0, airing_status: 'not_yet_aired', episode_count_status: 'unknown', fetch_state: 'ok'},
    {total_episodes: 12, aired_episodes: 0, airing_status: 'not_yet_aired', episode_count_status: 'planned', fetch_state: 'ok'},
  ]) {
    const payload = dataset('valid-counts');
    payload.anime_list[0].episodes = episodes;
    const {app} = createContext({fetchImpl: async () => responseFor(payload)});
    app.year = '2026'; app.season = '秋';
    await app.loadData();
    assert.equal(app.loadError, null);
    assert.equal(app.rawAnimeList.length, 1);
  }
});

test('latest quarter request wins when an older response arrives later', async () => {
  const requests = [];
  const { app } = createContext({
    fetchImpl: (url, options) => new Promise(resolve => {
      requests.push({ url, signal: options.signal, resolve });
    }),
  });

  app.year = '2025';
  app.season = '夏';
  const oldRequest = app.loadData();
  while (requests.length < 1) await nextTick();

  app.year = '2026';
  app.season = '秋';
  const latestRequest = app.loadData();
  while (requests.length < 2) await nextTick();

  requests[1].resolve(responseFor(dataset('latest')));
  await latestRequest;
  requests[0].resolve(responseFor(dataset('stale')));
  await oldRequest;

  assert.equal(app.rawAnimeList[0].anime_name, 'latest');
  assert.equal(app.loading, false);
  assert.equal(app.dataCache['2026_秋'].generated_at, '2026-09-30T00:00:00+08:00');
});

test('starting a different quarter clears the previous error before the new request completes', async () => {
  let resolveRequest;
  const { app } = createContext({
    fetchImpl: () => new Promise(resolve => { resolveRequest = resolve; }),
  });
  app.loadError = { code: 'HTTP_500', title: '舊錯誤', message: '舊訊息', hasStaleData: false };
  app.year = '2026';
  app.season = '秋';
  const request = app.loadData();

  assert.equal(app.loadError, null);
  assert.equal(app.loading, true);
  resolveRequest(responseFor(dataset('new quarter')));
  await request;
  assert.equal(app.loadError, null);
  assert.equal(app.rawAnimeList[0].anime_name, 'new quarter');
});

test('dataset cache stores the envelope and expires after the five minute TTL', async () => {
  let fetchCount = 0;
  const { app } = createContext({
    fetchImpl: async () => {
      fetchCount += 1;
      return responseFor(dataset(`load-${fetchCount}`));
    },
  });
  app.year = '2026';
  app.season = '秋';

  await app.loadData();
  await app.loadData();
  assert.equal(fetchCount, 1);
  assert.equal(app.dataCache['2026_秋'].anime_list[0].anime_name, 'load-1');
  assert.equal(app.dataCache['2026_秋'].generated_at, '2026-09-30T00:00:00+08:00');

  app.dataCache['2026_秋'].cachedAt = Date.now() - app.DATA_CACHE_TTL_MS - 1;
  await app.loadData();
  assert.equal(fetchCount, 2);
  assert.equal(app.rawAnimeList[0].anime_name, 'load-2');
});

test('HTTP failures expose readable categories, retry only transient statuses, and clear pending state', async () => {
  for (const [status, code, expectedAttempts] of [[404, 'HTTP_404', 1], [429, 'HTTP_429', 3], [503, 'HTTP_5XX', 3]]) {
    let fetchCount = 0;
    const { app } = createContext({
      fetchImpl: async () => {
        fetchCount += 1;
        return errorResponse(status);
      },
    });
    app.year = '2026';
    app.season = '秋';
    app.MAX_FETCH_RETRIES = 2;
    app.RETRY_DELAYS_MS = [0, 0];

    await app.loadData();

    assert.equal(fetchCount, expectedAttempts);
    assert.equal(app.loadError.code, code);
    assert.match(app.loadError.message, new RegExp(`HTTP ${status}`));
    assert.doesNotMatch(app.loadError.message, /系統會自動重試/);
    if (expectedAttempts > 1) {
      assert.match(app.loadError.message, new RegExp(`已自動嘗試 ${expectedAttempts} 次`));
      assert.match(app.loadError.message, /重新載入/);
    }
    assert.equal(app.loading, false);
    assert.equal(app._pendingDataRequests.size, 0);
    assert.equal(app.dataCache['2026_秋'], undefined);
  }
});

test('network and timeout failures are classified without exposing raw error details', async () => {
  let networkCount = 0;
  const networkContext = createContext({
    fetchImpl: async () => {
      networkCount += 1;
      throw new TypeError('network detail should not reach the UI');
    },
  });
  networkContext.app.year = '2026';
  networkContext.app.season = '秋';
  networkContext.app.RETRY_DELAYS_MS = [0, 0];
  await networkContext.app.loadData();

  assert.equal(networkCount, 3);
  assert.equal(networkContext.app.loadError.code, 'NETWORK');
  assert.match(networkContext.app.loadError.message, /無法連線到資料服務/);
  assert.doesNotMatch(networkContext.app.loadError.message, /network detail/);

  let timeoutCount = 0;
  const timeoutContext = createContext({
    fetchImpl: (url, options) => new Promise((resolve, reject) => {
      timeoutCount += 1;
      options.signal.addEventListener('abort', () => {
        const error = new Error('timed out');
        error.name = 'AbortError';
        reject(error);
      }, { once: true });
    }),
  });
  timeoutContext.app.year = '2026';
  timeoutContext.app.season = '秋';
  timeoutContext.app.FETCH_TIMEOUT_MS = 5;
  timeoutContext.app.RETRY_DELAYS_MS = [0, 0];
  await timeoutContext.app.loadData();

  assert.equal(timeoutCount, 3);
  assert.equal(timeoutContext.app.loadError.code, 'FETCH_TIMEOUT');
  assert.match(timeoutContext.app.loadError.message, /逾時/);
  assert.equal(timeoutContext.app._pendingDataRequests.size, 0);
});

test('invalid JSON and malformed rows fail closed without caching the payload', async () => {
  const invalidJson = createContext({
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => { throw new SyntaxError('payload detail should not reach the UI'); },
    }),
  });
  invalidJson.app.year = '2026';
  invalidJson.app.season = '秋';
  await invalidJson.app.loadData();
  assert.equal(invalidJson.app.loadError.code, 'JSON_PARSE');
  assert.doesNotMatch(invalidJson.app.loadError.message, /payload detail/);
  assert.equal(invalidJson.app.dataCache['2026_秋'], undefined);
  assert.equal(invalidJson.app._pendingDataRequests.size, 0);

  const malformedRow = createContext({
    fetchImpl: async () => responseFor({ schema_version: 1, anime_list: [{ story: 'no name' }] }),
  });
  malformedRow.app.year = '2026';
  malformedRow.app.season = '秋';
  await malformedRow.app.loadData();
  assert.equal(malformedRow.app.loadError.code, 'DATA_SCHEMA');
  assert.match(malformedRow.app.loadError.message, /第 1 筆動畫資料缺少名稱/);
  assert.equal(malformedRow.app.dataCache['2026_秋'], undefined);
  assert.equal(malformedRow.app._pendingDataRequests.size, 0);
});

test('a valid sparse historical row remains usable while retry preserves and then replaces stale data', async () => {
  let fetchCount = 0;
  const { app } = createContext({
    fetchImpl: async () => {
      fetchCount += 1;
      if (fetchCount === 1) return responseFor({ schema_version: 1, anime_list: [{ anime_name: 'old data' }] });
      if (fetchCount === 2) throw new TypeError('temporary network issue');
      if (fetchCount === 3) return responseFor(dataset('fresh data'));
      throw new TypeError('different quarter unavailable');
    },
  });
  app.year = '2026';
  app.season = '秋';
  app.RETRY_DELAYS_MS = [0, 0];
  app.MAX_FETCH_RETRIES = 0;

  await app.loadData();
  assert.equal(app.rawAnimeList[0].anime_name, 'old data');

  app.dataCache['2026_秋'].cachedAt = Date.now() - app.DATA_CACHE_TTL_MS - 1;
  await app.loadData();
  assert.equal(app.loadError.code, 'NETWORK');
  assert.equal(app.loadError.hasStaleData, true);
  assert.equal(app.rawAnimeList[0].anime_name, 'old data');
  assert.equal(app._pendingDataRequests.size, 0);

  await app.retryLoad();
  assert.equal(app.loadError, null);
  assert.equal(app.rawAnimeList[0].anime_name, 'fresh data');
  assert.equal(app._pendingDataRequests.size, 0);

  app.year = '2025';
  app.season = '夏';
  await app.loadData();
  assert.equal(app.loadError.code, 'NETWORK');
  assert.equal(app.loadError.hasStaleData, false);
  assert.equal(app.rawAnimeList.length, 0);
  assert.equal(app._pendingDataRequests.size, 0);
});

test('storage failures are contained and filtering remains a pure calculation', () => {
  let writes = 0;
  const throwingStorage = {
    getItem() { throw new Error('blocked'); },
    setItem() { writes += 1; throw new Error('blocked'); },
  };
  const { app } = createContext({ storage: throwingStorage });
  app.rawAnimeList = [{ anime_name: 'A', story: 'story', premiere_date: '一' }];
  app.filterDay = '一';

  assert.deepEqual(app.filteredAnime.map(item => item.anime_name), ['A']);
  assert.equal(app.readStorage('missing', 'fallback'), 'fallback');
  assert.equal(app.writeStorage('key', 'value'), false);
  app.saveFilterDay();
  assert.equal(writes, 2);
  assert.match(app.storageWarning, /無法儲存偏好設定/);
  app.dismissStorageWarning();
  assert.equal(app.storageWarning, '');
  app.writeStorage('after-dismiss', 'value');
  assert.equal(app.storageWarning, '');
});

test('aborting a stale request does not retry or show an error', async () => {
  const requests = [];
  const warnings = [];
  const errors = [];
  const { app, context } = createContext({
    fetchImpl: (url, options) => new Promise((resolve, reject) => {
      requests.push({ url, signal: options.signal, resolve, reject });
      options.signal.addEventListener('abort', () => {
        const error = new Error('aborted');
        error.name = 'AbortError';
        reject(error);
      }, { once: true });
    }),
  });
  context.console.warn = message => warnings.push(message);
  context.console.error = message => errors.push(message);

  app.year = '2025';
  app.season = '夏';
  const staleRequest = app.loadData();
  while (requests.length < 1) await nextTick();
  app.year = '2026';
  app.season = '秋';
  const latestRequest = app.loadData();
  while (requests.length < 2) await nextTick();
  requests[1].resolve(responseFor(dataset('latest')));
  await latestRequest;
  await staleRequest;

  assert.equal(warnings.length, 0);
  assert.equal(errors.length, 0);
  assert.equal(requests.length, 2);
});

test('aborting while a response body is pending clears the shared request promptly', async () => {
  let fetchCount = 0;
  const { app } = createContext({
    fetchImpl: async (url, options) => {
      fetchCount += 1;
      if (fetchCount === 1) {
        return {
          ok: true,
          status: 200,
          json: () => new Promise((resolve, reject) => {
            options.signal.addEventListener('abort', () => {
              const error = new Error('body aborted');
              error.name = 'AbortError';
              reject(error);
            }, { once: true });
          }),
        };
      }
      return responseFor(dataset('latest-body'));
    },
  });

  app.year = '2025';
  app.season = '夏';
  const staleRequest = app.loadData();
  await nextTick();
  app.year = '2026';
  app.season = '秋';
  await app.loadData();
  await staleRequest;

  assert.equal(fetchCount, 2);
  assert.equal(app._pendingDataRequests.size, 0);
});

test('A to B to A replaces an aborted pending entry while same-key calls still deduplicate', async () => {
  let fetchCount = 0;
  const abortableResponse = (options, payload) => new Promise((resolve, reject) => {
    const abort = () => {
      const error = new Error('request aborted');
      error.name = 'AbortError';
      reject(error);
    };
    options.signal.addEventListener('abort', abort, { once: true });
    setTimeout(() => resolve(responseFor(payload)), 50);
  });
  const { app } = createContext({
    fetchImpl: (url, options) => {
      fetchCount += 1;
      if (fetchCount === 1) return abortableResponse(options, dataset('old-A'));
      if (fetchCount === 2) return abortableResponse(options, dataset('B'));
      return Promise.resolve(responseFor(dataset('new-A')));
    },
  });

  app.year = '2025';
  app.season = '夏';
  const oldA = app.loadData();
  await nextTick();
  const duplicateA = app.loadData();
  await nextTick();
  assert.equal(fetchCount, 1);
  app.year = '2026';
  app.season = '秋';
  const b = app.loadData();
  await nextTick();
  app.year = '2025';
  app.season = '夏';
  const newA = app.loadData();
  await Promise.all([oldA, duplicateA, b, newA]);

  assert.equal(fetchCount, 3);
  assert.equal(app.rawAnimeList[0].anime_name, 'new-A');
  assert.equal(app._pendingDataRequests.size, 0);
});

test('card transformations use fixed responsive widths without mutating source URLs', () => {
  const { app } = createContext({ fetchImpl: async () => responseFor(dataset('unused')) });
  const sourceUrl = `https://res.cloudinary.com/demo/image/upload/f_auto,q_auto:best/v1/anime_covers/${'b'.repeat(32)}`;
  const anime = { anime_image_url: sourceUrl, anime_name: 'cover' };
  const srcset = app.cardImageSrcset(anime);

  assert.equal(anime.anime_image_url, sourceUrl);
  assert.match(app.cardImageUrl(anime, 600), /image\/upload\/c_limit,w_600,f_auto,q_auto\/v1\/anime_covers\/[0-9a-f]{32}$/);
  assert.match(srcset, /c_limit,w_300,f_auto,q_auto\/v1\/anime_covers\/.* 300w/);
  assert.match(srcset, /c_limit,w_600,f_auto,q_auto\/v1\/anime_covers\/.* 600w/);
  assert.match(srcset, /c_limit,w_900,f_auto,q_auto\/v1\/anime_covers\/.* 900w/);
});

test('html2canvas loader is lazy, singleton while loading, and retryable after failure', async () => {
  const { context, scripts } = createContext();
  const first = context.__frontendHooks.loadHtml2Canvas();
  const second = context.__frontendHooks.loadHtml2Canvas();
  assert.equal(first, second);
  assert.equal(scripts.length, 1);
  assert.equal(scripts[0].src, 'https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js');
  assert.equal(scripts[0].integrity, 'sha384-ZZ1pncU3bQe8y31yfZdMFdSpttDoPmOZg2wguVK9almUodir1PghgT0eY7Mrty8H');
  assert.equal(scripts[0].crossOrigin, 'anonymous');
  const renderer = () => {};
  context.window.html2canvas = renderer;
  scripts[0].onload();
  assert.equal(await first, renderer);

  const retryContext = createContext();
  const failed = retryContext.context.__frontendHooks.loadHtml2Canvas();
  retryContext.scripts[0].onerror();
  await assert.rejects(failed, /載入失敗/);
  const retry = retryContext.context.__frontendHooks.loadHtml2Canvas();
  assert.equal(retryContext.scripts.length, 2);
  retryContext.context.window.html2canvas = renderer;
  retryContext.scripts[1].onload();
  assert.equal(await retry, renderer);
});

test('share preparation errors always restore the button state', async () => {
  const { app, button } = createContext({ html2canvas: () => {} });
  app.shareList = [{ name: 'bad', img: 'https://evil.example/image.png' }];
  await app.generateShareImage();
  assert.equal(button.disabled, false);
  assert.equal(button.innerHTML, '生成圖片');
});

test('copy failures show a safe permission message without exposing the browser error', async () => {
  const alerts = [];
  const { app } = createContext({
    navigatorImpl: {
      clipboard: {
        writeText: async () => {
          const error = new Error('clipboard secret detail');
          error.name = 'NotAllowedError';
          throw error;
        },
      },
    },
    swal: { fire: options => alerts.push(options) },
  });

  assert.equal(await app.copyText('動畫名稱'), false);
  assert.equal(alerts[0].title, '複製失敗');
  assert.match(alerts[0].text, /剪貼簿權限/);
  assert.doesNotMatch(alerts[0].text, /clipboard secret detail/);
});

test('share URL and renderer failures show safe advice and always restore the button', async () => {
  const urlAlerts = [];
  const invalidUrl = createContext({
    html2canvas: () => {},
    swal: { fire: options => urlAlerts.push(options) },
  });
  invalidUrl.app.shareList = [{ name: 'bad', img: 'https://evil.example/image.png' }];
  await invalidUrl.app.generateShareImage();
  assert.equal(urlAlerts[0].title, '圖片網址無法使用');
  assert.match(urlAlerts[0].text, /安全驗證/);
  assert.doesNotMatch(urlAlerts[0].text, /evil\.example/);
  assert.equal(invalidUrl.button.disabled, false);

  const rendererAlerts = [];
  const rendererFailure = createContext({
    html2canvas: () => { throw new Error('renderer internal payload'); },
    swal: { fire: options => rendererAlerts.push(options) },
  });
  rendererFailure.app.shareList = [{
    name: 'valid',
    img: `https://res.cloudinary.com/demo/image/upload/f_auto,q_auto:best/v1/anime_covers/${'a'.repeat(64)}`,
  }];
  await rendererFailure.app.generateShareImage();
  assert.equal(rendererAlerts[0].title, '生成圖片失敗');
  assert.match(rendererAlerts[0].text, /渲染/);
  assert.match(rendererAlerts[0].text, /SHARE_RENDERER/);
  assert.doesNotMatch(rendererAlerts[0].text, /renderer internal payload/);
  assert.equal(rendererFailure.button.disabled, false);

  const networkAlerts = [];
  const rendererNetwork = createContext({
    swal: { fire: options => networkAlerts.push(options) },
  });
  rendererNetwork.app.shareList = [{
    name: 'valid',
    img: `https://res.cloudinary.com/demo/image/upload/f_auto,q_auto:best/v1/anime_covers/${'b'.repeat(64)}`,
  }];
  const networkRender = rendererNetwork.app.generateShareImage();
  while (rendererNetwork.scripts.length < 1) await nextTick();
  rendererNetwork.scripts[0].onerror();
  await networkRender;
  assert.match(networkAlerts[0].text, /網路/);
  assert.match(networkAlerts[0].text, /SHARE_RENDERER_NETWORK/);
  assert.equal(rendererNetwork.button.disabled, false);
});

test('keyword search covers every quarter, merges shared cards and clearing restores selection', async () => {
  const recent = dataset('shared');
  const old = dataset('old');
  old.anime_list.push({...recent.anime_list[0]});
  old.anime_list[0].original_name = 'ＡＮＣＩＥＮＴ';
  const calls = [];
  const {app} = createContext({fetchImpl: async url => {
    calls.push(url);
    return responseFor(url.includes('2026_') ? recent : old);
  }});
  app.year = '2026'; app.season = '秋';
  await app.loadData();
  app.searchKeyword = 'ancient';
  await app.loadData();
  assert.equal(app.filteredAnime.length, 1);
  assert.equal(app.filteredAnime[0].anime_name, 'old');
  assert.equal(app.sourceQuarterLabel(app.filteredAnime[0]), '2025年夏季');
  app.searchKeyword = 'shared';
  await app.loadData();
  assert.equal(app.filteredAnime.length, 1);
  assert.equal(app.sourceQuarterLabel(app.filteredAnime[0]), '2026年秋季、2025年夏季');
  assert.equal(recent.anime_list[0].source_quarters, undefined);
  app.searchKeyword = '';
  await app.loadData();
  assert.equal(app.rawAnimeList.length, 1);
  assert.equal(app.rawAnimeList[0].anime_name, 'shared');
  assert.equal(app.sourceQuarterLabel(app.rawAnimeList[0]), '');
  assert.equal(calls.length, 2);
});

test('a failed quarter cannot appear as complete global search and retry recovers', async () => {
  let failing = true;
  const {app} = createContext({fetchImpl: async url => {
    if (url.includes('2025_') && failing) return errorResponse(503);
    return responseFor(dataset(url.includes('2025_') ? 'old' : 'recent'));
  }});
  app.MAX_FETCH_RETRIES = 0;
  app.year = '2026'; app.season = '秋';
  await app.loadData();
  app.searchKeyword = 'story';
  await app.loadData();
  assert.ok(app.loadError);
  assert.equal(app.rawAnimeList.length, 0);
  assert.equal(app.loadError.hasStaleData, false);
  failing = false;
  await app.retryLoad();
  assert.equal(app.loadError, null);
  assert.equal(app.filteredAnime.length, 2);
});

test('clearing a keyword wins over an older pending global search', async () => {
  let resolveOld;
  const {app} = createContext({fetchImpl: async url => {
    if (url.includes('2025_')) return new Promise(resolve => { resolveOld = resolve; });
    return responseFor(dataset('recent'));
  }});
  app.year = '2026'; app.season = '秋';
  await app.loadData();
  app.searchKeyword = 'old';
  const search = app.loadData();
  while (!resolveOld) await nextTick();
  app.searchKeyword = '';
  await app.loadData();
  resolveOld(responseFor(dataset('old')));
  await search;
  assert.equal(app.rawAnimeList[0].anime_name, 'recent');
  assert.equal(app.loading, false);
  assert.equal(app.loadError, null);
});

test('global search bounds simultaneous quarter loads', async () => {
  let active = 0; let maximum = 0; let calls = 0;
  const {app} = createContext({fetchImpl: async () => {
    active++; calls++; maximum = Math.max(maximum, active);
    await nextTick(); active--;
    return responseFor(dataset('shared'));
  }});
  app.availableData = {2024: ['冬', '春', '夏', '秋'], 2025: ['冬', '春', '夏', '秋'], 2026: ['秋']};
  app.year = '2026'; app.season = '秋'; app.searchKeyword = 'shared';
  await app.loadData();
  assert.equal(calls, 9);
  assert.ok(maximum <= 4);
  assert.equal(app.filteredAnime.length, 1);
  assert.equal(app.filteredAnime[0].source_quarters.length, 9);
});

test('a changed quarter hash invalidates a fresh dataset cache and changes its request URL', async () => {
  const urls = [];
  const {app} = createContext({fetchImpl: async url => {
    urls.push(url);
    return responseFor(dataset(`version-${urls.length}`));
  }});
  app.year = '2026'; app.season = '秋';
  app.quarterVersions['2026_秋'] = 'a'.repeat(64);
  await app.loadData();
  await app.loadData();
  assert.equal(urls.length, 1);
  app.quarterVersions['2026_秋'] = 'b'.repeat(64);
  await app.loadData();
  assert.equal(urls.length, 2);
  assert.match(urls[0], new RegExp('v=' + 'a'.repeat(64)));
  assert.match(urls[1], new RegExp('v=' + 'b'.repeat(64)));
  assert.equal(app.rawAnimeList[0].anime_name, 'version-2');
});
