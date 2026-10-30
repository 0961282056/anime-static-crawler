import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import {createHash, webcrypto} from 'node:crypto';

const source = readFileSync(new URL('../../static/js/main.js', import.meta.url), 'utf8');

test('production bundle registers only through Alpine and keeps helpers private', () => {
  const events = new Map();
  const registrations = [];
  const context = {
    document: {
      getElementById: () => ({dataset: {officialStorySources: '{}'}}),
      addEventListener: (name, callback) => events.set(name, callback),
    },
    Alpine: {data: (name, factory) => registrations.push({name, factory})},
  };
  vm.runInNewContext(source, context);
  assert.equal(registrations.length, 0);
  events.get('alpine:init')();
  assert.equal(registrations.length, 1);
  assert.equal(registrations[0].name, 'animeApp');
  for (const name of ['animeApp', 'dataMethods', 'searchMethods', 'loadHtml2Canvas', 'officialStoryUrl', '__frontendHooks']) {
    assert.equal(context[name], undefined, name);
  }
});

function createContext({storage = null, fetchImpl = null, html2canvas = null, navigatorImpl = null, swal = null} = {}) {
  const scripts = [];
  const button = { innerHTML: '生成圖片', disabled: false };
  const configElement = {
    dataset: {
      defaultYear: '2026',
      defaultSeason: '秋',
      buildVersion: 'test-build',
      wikipediaStories: JSON.stringify(Object.fromEntries(JSON.parse(readFileSync(new URL('../../services/reviewed_wikipedia_stories.json', import.meta.url), 'utf8')).map(row => [row.card_id, row]))),
      bangumiStoryExcerpts: JSON.stringify(Object.fromEntries(JSON.parse(readFileSync(new URL('../../services/reviewed_bangumi_story_excerpts.json', import.meta.url), 'utf8')).map(row => [row.card_id, row]))),
      dataVersion: 'aaaaaaaaaaaaaaaa',
      officialStorySources: JSON.stringify(Object.fromEntries(JSON.parse(readFileSync(new URL('../../services/reviewed_story_sources.json', import.meta.url), 'utf8')).map(row => [row.card_id, row.subject_id === null ? [null, row.source_url, row.anime_name, row.original_name] : [row.subject_id, row.source_url]]))),
      reviewedBroadcastSources: JSON.stringify(Object.fromEntries(JSON.parse(readFileSync(new URL('../../services/reviewed_broadcast_sources.json', import.meta.url), 'utf8')).filter(row => row.display_note).map(row => [row.card_id, row]))),
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
        scrollWidth: 600,
        scrollHeight: 1000,
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
    TextDecoder,
    Uint8Array,
    crypto: webcrypto,
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
    source.replace(/\}\)\(\);\n$/, '\n globalThis.__animeAppFactory = animeApp; globalThis.__frontendHooks = {loadHtml2Canvas, buildCloudinaryTransformationUrl};\n})();\n'),
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
      bangumi_id: `anime-${Array.from(name).reduce((n, ch) => (n * 31 + ch.charCodeAt(0)) >>> 0, 0)}`,
      anime_name: name,
      anime_image_url: `https://res.cloudinary.com/demo/image/upload/f_auto,q_auto:best/v1/anime_covers/${'a'.repeat(64)}`,
      premiere_date: '一',
      premiere_time: '20:00',
      story: `${name} story`,
    }],
  };
}

function searchIndexFor(quarters) {
  const entries = new Map();
  for (const [quarter, data] of Object.entries(quarters).sort(([a], [b]) => b.localeCompare(a))) {
    for (const anime of data.anime_list) {
      const identity = [anime.bangumi_id, anime.anime_name, anime.original_name || '', anime.external_ids?.bangumi || null];
      const key = JSON.stringify(identity);
      if (entries.has(key)) entries.get(key).quarters.push(quarter);
      else entries.set(key, {identity, story: anime.story.normalize('NFKC').toLowerCase(), quarters: [quarter]});
    }
  }
  return {schema_version: 1, data_version: 'aaaaaaaaaaaaaaaa', quarter_versions: Object.fromEntries(Object.entries(quarters).map(([key, data]) => [key, datasetHash(data)])), generated_at: '2026-09-30T00:00:00+08:00', entries: [...entries.values()]};
}

function datasetHash(payload) {
  return createHash('sha256').update(JSON.stringify(payload)).digest('hex');
}
function responseFor(payload) {
  return { ok: true, status: 200, json: async () => payload,
    arrayBuffer: async () => new TextEncoder().encode(JSON.stringify(payload)).buffer };
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
    const anime = {bangumi_id: row.card_id, anime_name: row.anime_name, original_name: row.original_name, external_ids: {bangumi: row.subject_id}, story_source: {
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
    return responseFor(url.startsWith('search-index') ? searchIndexFor({'2026_秋': recent, '2025_夏': old}) : url.includes('2026_') ? recent : old);
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
  assert.equal(calls.length, 3);
});

test('a failed quarter cannot appear as complete global search and retry recovers', async () => {
  let failing = true;
  const {app} = createContext({fetchImpl: async url => {
    if (url.startsWith('search-index')) return responseFor(searchIndexFor({'2026_秋': dataset('recent'), '2025_夏': dataset('old')}));
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
    if (url.startsWith('search-index')) return responseFor(searchIndexFor({'2026_秋': dataset('recent'), '2025_夏': dataset('old')}));
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

test('indexed search bounds simultaneous matched-quarter loads', async () => {
  let active = 0; let maximum = 0; const calls = [];
  const quarters = Object.fromEntries(['2024_冬','2024_春','2024_夏','2024_秋','2025_冬','2025_春','2025_夏','2025_秋','2026_秋'].map((q, i) => [q, dataset(`shared-${i}`)]));
  const {app} = createContext({fetchImpl: async url => {
    calls.push(url);
    if (url.startsWith('search-index')) return responseFor(searchIndexFor(quarters));
    active++; maximum = Math.max(maximum, active);
    await nextTick(); active--;
    const key = url.split('/')[1].split('.json')[0];
    return responseFor(quarters[key]);
  }});
  app.availableData = {2024: ['冬', '春', '夏', '秋'], 2025: ['冬', '春', '夏', '秋'], 2026: ['秋']};
  app.year = '2026'; app.season = '秋'; app.searchKeyword = 'shared';
  await app.loadData();
  assert.equal(calls.length, 10);
  assert.ok(maximum <= 4);
  assert.equal(app.filteredAnime.length, 9);
});

test('a changed quarter hash invalidates a fresh dataset cache and changes its request URL', async () => {
  const first = dataset('version-1'); const second = dataset('version-2');
  const urls = [];
  const {app} = createContext({fetchImpl: async url => {
    urls.push(url);
    return responseFor(dataset(`version-${urls.length}`));
  }});
  app.year = '2026'; app.season = '秋';
  app.quarterVersions['2026_秋'] = datasetHash(first);
  await app.loadData();
  await app.loadData();
  assert.equal(urls.length, 1);
  app.quarterVersions['2026_秋'] = datasetHash(second);
  await app.loadData();
  assert.equal(urls.length, 2);
  assert.match(urls[0], new RegExp('v=' + datasetHash(first)));
  assert.match(urls[1], new RegExp('v=' + datasetHash(second)));
  assert.equal(app.rawAnimeList[0].anime_name, 'version-2');
});

test('indexed search fetches no unrelated quarter and no quarter for zero matches', async () => {
  const recent = dataset('recent'); const old = dataset('ancient'); const calls = [];
  const {app} = createContext({fetchImpl: async url => {
    calls.push(url);
    if (url.startsWith('search-index')) return responseFor(searchIndexFor({'2026_秋': recent, '2025_夏': old}));
    if (url.includes('2025_')) return responseFor(old);
    throw new Error('Unrelated quarter must not be fetched');
  }});
  app.year = '2026'; app.season = '秋'; app.searchKeyword = 'ancient';
  await app.loadData();
  assert.equal(app.loadError, null);
  assert.equal(app.filteredAnime[0].anime_name, 'ancient');
  assert.equal(calls.length, 2);
  app.searchKeyword = 'no match';
  await app.loadData();
  assert.equal(app.filteredAnime.length, 0);
  assert.equal(calls.length, 2);
});

test('invalid index version, quarter references or duplicated identities fail closed', async () => {
  for (const mutate of [index => {index.data_version = 'b'.repeat(16);}, index => {index.entries[0].quarters = ['../../private'];}, index => {index.entries.push(index.entries[0]);}]) {
    const index = searchIndexFor({'2026_秋': dataset('recent'), '2025_夏': dataset('old')}); mutate(index);
    let calls = 0;
    const {app} = createContext({fetchImpl: async () => {calls++; return responseFor(index);}});
    app.year = '2026'; app.season = '秋'; app.searchKeyword = 'story';
    await app.loadData();
    assert.equal(app.loadError.code, 'SEARCH_SCHEMA');
    assert.equal(app.rawAnimeList.length, 0);
    assert.equal(calls, 1);
  }
});

test('retry refreshes release metadata after a data-only index version update', async () => {
  const old = dataset('ancient'); const recent = dataset('recent');
  const index = searchIndexFor({'2026_秋': recent, '2025_夏': old});
  index.data_version = 'b'.repeat(16);
  const calls = [];
  const {app} = createContext({fetchImpl: async url => {
    calls.push(url);
    if (url.startsWith('release.json')) return responseFor({schema_version: 1, build_version: 'test-build', data_version: index.data_version, quarter_versions: index.quarter_versions});
    if (url.startsWith('search-index')) return responseFor(index);
    return responseFor(old);
  }});
  app.year = '2026'; app.season = '秋'; app.searchKeyword = 'ancient';
  await app.loadData();
  assert.equal(app.loadError.code, 'SEARCH_SCHEMA');
  await app.retryLoad();
  assert.equal(app.loadError, null);
  assert.equal(app.dataVersion, 'b'.repeat(16));
  assert.equal(app.filteredAnime[0].anime_name, 'ancient');
  assert.equal(calls.filter(url => url.startsWith('search-index')).length, 2);
});

test('a cached old index rejects new same-identity content, then retry loads a consistent release', async () => {
  const current = dataset('current');
  const before = dataset('same-work'); before.anime_list[0].story = 'old-only-word';
  const after = structuredClone(before); after.anime_list[0].story = 'new-only-word';
  const oldIndex = searchIndexFor({'2026_秋': current, '2025_夏': before});
  const newIndex = searchIndexFor({'2026_秋': current, '2025_夏': after});
  newIndex.data_version = 'bbbbbbbbbbbbbbbb';
  let deployedNew = false;
  const {app} = createContext({fetchImpl: async url => {
    if (url.startsWith('release.json')) return responseFor({schema_version: 1, build_version: 'test-build', data_version: newIndex.data_version, quarter_versions: newIndex.quarter_versions});
    if (url.startsWith('search-index')) return responseFor(deployedNew ? newIndex : oldIndex);
    return responseFor(url.includes('2025_') ? after : current);
  }});
  app.quarterVersions = oldIndex.quarter_versions;
  app.year = '2026'; app.season = '秋'; app.searchKeyword = 'prime-no-match';
  await app.loadData();
  deployedNew = true;
  app.searchKeyword = 'old-only-word';
  await app.loadData();
  assert.equal(app.loadError.code, 'SEARCH_VERSION');
  assert.equal(app.rawAnimeList.length, 0);
  assert.equal(app.dataCache['2025_夏'], undefined);
  await app.retryLoad();
  assert.equal(app.loadError, null);
  assert.equal(app.filteredAnime.length, 0); // Removed text no longer matches.
  app.searchKeyword = 'new-only-word';
  await app.loadData();
  assert.equal(app.filteredAnime.length, 1);
  assert.equal(app.dataCache['2025_夏'].version, datasetHash(after));
});

test('quarter integrity uses original UTF-8 bytes, including whitespace and JSON key order', async () => {
  const payload = dataset('日本語／中文');
  const body = JSON.stringify(payload, null, 2) + '\n';
  const digest = createHash('sha256').update(body).digest('hex');
  let receivedBody = body;
  const {app} = createContext({fetchImpl: async () => ({ok: true, status: 200,
    arrayBuffer: async () => new TextEncoder().encode(receivedBody).buffer,
  })});
  app.quarterVersions['2026_秋'] = digest;
  const valid = await app.getDataset('2026_秋');
  assert.equal(valid.anime_list[0].anime_name, '日本語／中文');
  assert.equal(valid.version, digest);
  app.dataCache = {};
  receivedBody = JSON.stringify(payload); // Same JSON values, different bytes.
  await assert.rejects(app.getDataset('2026_秋'), {code: 'SEARCH_VERSION'});
  assert.equal(app.dataCache['2026_秋'], undefined);
});

test('cancelling during SHA-256 verification cannot cache or replace newer data', async () => {
  const before = dataset('before'); const after = dataset('after');
  let finishDigest;
  const {app, context} = createContext({fetchImpl: async () => responseFor(finishDigest ? after : before)});
  context.crypto = {subtle: {digest: () => new Promise(resolve => { finishDigest = resolve; })}};
  app.quarterVersions['2026_秋'] = datasetHash(before);
  const controller = new AbortController();
  const obsolete = app.getDataset('2026_秋', controller.signal);
  const cancelled = assert.rejects(obsolete, {name: 'AbortError'});
  while (!finishDigest) await nextTick();
  controller.abort(); await cancelled;
  context.crypto = webcrypto;
  app.quarterVersions['2026_秋'] = datasetHash(after);
  await app.getDataset('2026_秋');
  finishDigest(Uint8Array.from(Buffer.from(datasetHash(before), 'hex')).buffer);
  await nextTick();
  assert.equal(app.dataCache['2026_秋'].anime_list[0].anime_name, 'after');
  assert.equal(app._pendingDataRequests.size, 0);
});

for (const navigation of ['clear search', 'change quarter']) {
  for (const success of [true, false]) {
    test(`obsolete release refresh ${success ? 'success' : 'failure'} cannot change state after ${navigation}`, async () => {
      let finish;
      let releaseSignal;
      let releaseCalls = 0;
      const healthy = dataset('healthy-quarter');
      const {app} = createContext({fetchImpl: async (url, options) => {
        if (url.startsWith('release.json')) {
          releaseCalls++;
          releaseSignal = options.signal;
          return new Promise(resolve => { finish = resolve; });
        }
        return responseFor(healthy);
      }});
      app.year = '2026'; app.season = '秋'; app.searchKeyword = 'missing';
      app.loadError = {code: 'SEARCH_SCHEMA'};
      const retry = app.retryLoad();
      await nextTick();
      assert.equal(app.loading, true);
      await app.retryLoad();
      assert.equal(releaseCalls, 1);
      app.searchKeyword = '';
      if (navigation === 'change quarter') { app.year = '2025'; app.season = '夏'; }
      await app.loadData();
      const currentKey = `${app.year}_${app.season}`;
      const currentCache = app.dataCache[currentKey];
      assert.equal(releaseSignal.aborted, true);
      finish(success ? responseFor({
        schema_version: 1, build_version: 'test-build', data_version: 'b'.repeat(16),
        quarter_versions: {'2026_秋': 'b'.repeat(64), '2025_夏': 'b'.repeat(64)},
      }) : errorResponse(503));
      await retry;
      assert.equal(app.loadError, null);
      assert.equal(app.loading, false);
      assert.equal(app.dataVersion, 'a'.repeat(16));
      assert.equal(app.dataCache[currentKey], currentCache);
      assert.equal(app.rawAnimeList[0].anime_name, 'healthy-quarter');
    });
  }
}

test('a late response from an older quarter version cannot overwrite the new cache', async () => {
  const resolvers = [];
  const oldData = dataset('old'); const newData = dataset('new');
  const {app} = createContext({fetchImpl: () => new Promise(resolve => resolvers.push(resolve))});
  app.quarterVersions['2026_秋'] = datasetHash(oldData);
  const old = app.getDataset('2026_秋');
  await nextTick();
  app.quarterVersions['2026_秋'] = datasetHash(newData);
  const current = app.getDataset('2026_秋');
  await nextTick();
  assert.equal(resolvers.length, 2);
  resolvers[1](responseFor(newData)); await current;
  resolvers[0](responseFor(oldData)); await old;
  assert.equal(app.dataCache['2026_秋'].anime_list[0].anime_name, 'new');
  assert.equal(app.dataCache['2026_秋'].version, datasetHash(newData));
});

test('module composition preserves reactive getters and independent app state', () => {
  const {context, app} = createContext();
  const second = context.__animeAppFactory();
  assert.equal(typeof Object.getOwnPropertyDescriptor(app, 'filteredAnime').get, 'function');
  app.rawAnimeList = dataset('第一筆').anime_list;
  assert.equal(app.filteredAnime.length, 1);
  app.searchKeyword = '不存在';
  assert.equal(app.filteredAnime.length, 0);
  app.shareList.push({name: '第一筆'});
  app.dataCache.quarter = {cachedAt: 1};
  assert.equal(second.shareList.length, 0);
  assert.equal(Object.keys(second.dataCache).length, 0);
  assert.equal(second.rawAnimeList.length, 0);
});

test('official summary with no database ID binds exact names and rejects invented IDs', async () => {
  const rows = JSON.parse(readFileSync(new URL('../../services/reviewed_story_sources.json', import.meta.url), 'utf8'));
  const row = rows.find(row => row.card_id === 'anime-318');
  const record = {...dataset(row.anime_name).anime_list[0], bangumi_id: row.card_id,
    original_name: row.original_name, external_ids: {bangumi: null}, story: row.story,
    story_source: {provider: 'official', method: 'manual_official', subject_id: null,
      source_url: row.source_url, checked_at: row.checked_at}};
  let current = record;
  const {app} = createContext({fetchImpl: async () => ({ok: true, json: async () => ({anime_list: [current]})})});
  assert.equal(app.storySourceUrl(record), row.source_url);
  assert.equal((await app.fetchDatasetFromNetwork('2019_春')).anime_list[0].story, row.story);
  for (const changes of [{anime_name: '別的作品'}, {original_name: '別的原名'}, {external_ids: {bangumi: 1}},
    {story_source: {...record.story_source, provider: 'bangumi', method: 'bangumi_api'}}]) {
    current = {...record, ...changes};
    await assert.rejects(app.fetchDatasetFromNetwork('2019_春'), error => error.code === 'DATA_SCHEMA');
  }
});

test('weekday filters include weekday runs and keep nonweekly separate from unknown', () => {
  const {app} = createContext();
  app.rawAnimeList = [
    {anime_name:'daily', premiere_date:'一至五'},
    {anime_name:'friday', premiere_date:'五'},
    {anime_name:'nonweekly', premiere_date:'不定期公開'},
    {anime_name:'unknown', premiere_date:'無首播日期'},
  ];
  for (const day of ['一','二','三','四','五']) {
    app.filterDay = day;
    assert.ok(app.filteredAnime.some(row => row.anime_name === 'daily'));
    assert.ok(!app.filteredAnime.some(row => ['nonweekly','unknown'].includes(row.anime_name)));
  }
  for (const day of ['六','日']) {
    app.filterDay = day;
    assert.equal(app.filteredAnime.length, 0);
  }
  for (const [day,name] of [['一至五','daily'],['不定期公開','nonweekly'],['無首播日期','unknown']]) {
    app.filterDay = day;
    assert.deepEqual(Array.from(app.filteredAnime, row => row.anime_name), [name]);
    assert.notEqual(app.broadcastDayLabel(app.filteredAnime[0]), '週六');
  }
  assert.equal(app.broadcastDayLabel({premiere_date:'一至五'}), '週一至五');
  assert.equal(app.broadcastDayLabel({premiere_date:'不定期公開'}), '不定期公開');
  assert.equal(app.broadcastDayLabel({}), '播出日待確認');
});

test('broadcast scope notes bind exact card, names, database and reviewed weekday', () => {
  const {app} = createContext();
  for (const [id,row] of Object.entries(app.reviewedBroadcastSources)) {
    const anime = {bangumi_id:id, anime_name:row.anime_name, original_name:row.original_name, premiere_date:row.weekday, external_ids:{bangumi:row.subject_id}};
    assert.equal(app.broadcastNote(anime), row.display_note);
    assert.equal(app.broadcastSourceUrl(anime), row.source_url);
    for (const changes of [{bangumi_id:'anime-999999'}, {anime_name:'another'}, {original_name:'another'}, {premiere_date:'日'}, {external_ids:{bangumi:1}}]) {
      assert.equal(app.broadcastNote({...anime,...changes}), '');
      assert.equal(app.broadcastSourceUrl({...anime,...changes}), null);
    }
    const original = row.source_url;
    row.source_url = 'https://user:pass@example.test/';
    assert.equal(app.broadcastSourceUrl(anime), null);
    row.source_url = original;
  }
});

test('reviewed Bangumi excerpts retain truthful attribution and exact scope', async () => {
  const row = JSON.parse(readFileSync(new URL('../../services/reviewed_bangumi_story_excerpts.json', import.meta.url), 'utf8'))[0];
  const record = {...dataset(row.anime_name).anime_list[0], bangumi_id: row.card_id,
    original_name: row.original_name, story: row.story, external_ids: {bangumi: row.subject_id},
    story_source: {provider: 'bangumi', method: 'bangumi_excerpt', subject_id: row.subject_id,
      source_url: row.source_url, checked_at: row.checked_at}};
  let current = record;
  const {app} = createContext({fetchImpl: async () => ({ok: true, json: async () => ({anime_list: [current]})})});
  assert.equal((await app.fetchDatasetFromNetwork('2019_夏')).anime_list[0].story, row.story);
  assert.equal(app.storySourceUrl(record), 'https://bgm.tv/subject/283480');
  assert.match(app.storySourceLabel(record), /Bangumi.*人工核對節錄/);
  for (const change of [
    {bangumi_id: 'anime-275'}, {original_name: '闇芝居 六期'}, {story: '錯誤母作品介紹'},
    {story_source: {...record.story_source, provider: 'official'}},
    {story_source: {...record.story_source, source_url: 'https://api.bgm.tv/v0/subjects/250117'}},
    {story_source: {...record.story_source, checked_at: '2026-10-18T00:00:00+08:00'}},
  ]) {
    current = {...record, ...change};
    await assert.rejects(app.fetchDatasetFromNetwork('2019_夏'), error => error.code === 'DATA_SCHEMA');
    assert.equal(app.storySourceUrl(current), null);
  }
});

test('Wikipedia excerpts keep exact revisions, work identity and reuse attribution', async () => {
  const rows = JSON.parse(readFileSync(new URL('../../services/reviewed_wikipedia_stories.json', import.meta.url), 'utf8'));
  const dialogs = [];
  let current;
  const {app} = createContext({swal: {fire: options => dialogs.push(options)}, fetchImpl: async () => ({ok: true, json: async () => ({anime_list: [current]})})});
  for (const row of rows) {
    const record = {...dataset(row.anime_name).anime_list[0], bangumi_id: row.card_id,
      original_name: row.original_name, story: row.story, external_ids: {bangumi: row.subject_id, anilist: row.anilist_id},
      story_source: {provider: 'wikipedia', method: 'wikipedia_excerpt', subject_id: row.subject_id,
        source_url: row.source_url, checked_at: row.checked_at, wikipedia: row.attribution}};
    current = record;
    assert.equal((await app.fetchDatasetFromNetwork('2026_秋')).anime_list[0].story, row.story);
    assert.equal(app.storySourceUrl(record), row.source_url);
    assert.match(app.storySourceLabel(record), /維基百科/);
    assert.match(app.wikipediaCredit(record), new RegExp(String(row.attribution.revision_id)));
    assert.equal(app.wikipediaContributorsUrl(record), row.attribution.contributors_url);
    assert.equal(app.wikipediaLicenseUrl(record), 'https://creativecommons.org/licenses/by-sa/4.0/');
    app.showStory(record.anime_name, record.story, record);
    assert.match(dialogs.at(-1).text, /CC BY-SA 4.0/);
    assert.ok(dialogs.at(-1).text.includes(row.source_url));
    assert.equal(dialogs.at(-1).html, undefined);
    for (const change of [
      {original_name: '第一季'}, {anime_name: '母系列'}, {story: '母系列劇情'},
      {external_ids: {bangumi: row.subject_id, anilist: 1}},
      {story_source: {...record.story_source, provider: 'official'}},
      {story_source: {...record.story_source, provider: 'bangumi'}},
      {story_source: {...record.story_source, wikipedia: null}},
      {story_source: {...record.story_source, source_url: 'https://example.com/'}},
      ...['revision_id', 'contributors_url', 'license', 'license_url', 'changes'].map(key =>
        ({story_source: {...record.story_source, wikipedia: {...row.attribution, [key]: 'invalid'}}})),
    ]) {
      current = {...record, ...change};
      await assert.rejects(app.fetchDatasetFromNetwork('2026_秋'), error => error.code === 'DATA_SCHEMA');
      assert.equal(app.storySourceUrl(current), null);
      assert.equal(app.wikipediaCredit(current), '');
    }
  }
});

test('cancelled global search releases its orphaned downloads without retries or caching', async () => {
  const pending = [];
  const quarters = {'2026_秋': dataset('recent'), '2025_夏': dataset('old')};
  const {app} = createContext({fetchImpl: async (url, options) => {
    if (url.startsWith('search-index')) return responseFor(searchIndexFor(quarters));
    if (url.includes('2026_')) return responseFor(quarters['2026_秋']);
    return new Promise((resolve, reject) => {
      pending.push(options.signal);
      options.signal.addEventListener('abort', () => reject(Object.assign(new Error('cancelled'), {name: 'AbortError'})), {once: true});
    });
  }});
  app.year = '2026'; app.season = '秋';
  await app.loadData();
  app.searchKeyword = 'old';
  const oldSearch = app.loadData();
  while (!pending.length) await nextTick();
  app.searchKeyword = '';
  await app.loadData();
  await oldSearch;
  await nextTick();
  assert.equal(pending[0].aborted, true);
  assert.equal(app._pendingDataRequests.size, 0);
  assert.equal(app.dataCache['2025_夏'], undefined);
  assert.equal(app.rawAnimeList[0].anime_name, 'recent');
});

test('one cancelled subscriber preserves another, then the final cancellation releases the request', async () => {
  const signals = [];
  const {app} = createContext({fetchImpl: (url, options) => new Promise((resolve, reject) => {
    signals.push(options.signal);
    options.signal.addEventListener('abort', () => reject(Object.assign(new Error('cancelled'), {name: 'AbortError'})), {once: true});
  })});
  const first = new AbortController(); const second = new AbortController();
  const a = app.getDataset('2026_秋', first.signal).catch(error => error.name);
  const b = app.getDataset('2026_秋', second.signal).catch(error => error.name);
  first.abort();
  await nextTick();
  assert.equal(signals.length, 1);
  assert.equal(signals[0].aborted, false);
  second.abort();
  await Promise.all([a, b]); await nextTick();
  assert.equal(signals[0].aborted, true);
  assert.equal(app._pendingDataRequests.size, 0);
});

for (const stage of ['fonts', 'renderer', 'blob']) {
  test(`stalled share ${stage} recovers the button and preserves the list`, async () => {
    const alerts = [];
    const renderer = () => stage === 'renderer' ? new Promise(() => {}) : {toBlob() {}};
    const {app, context, button} = createContext({html2canvas: renderer, swal: {fire: options => alerts.push(options)}});
    if (stage === 'fonts') context.document.fonts.ready = new Promise(() => {});
    context.setTimeout = (callback, delay) => setTimeout(callback, delay >= 1000 ? 5 : delay);
    app.shareList = [{name: 'kept', img: `https://res.cloudinary.com/demo/image/upload/v1/anime_covers/${'a'.repeat(64)}`}];
    await Promise.race([app.generateShareImage(), new Promise((resolve, reject) => setTimeout(() => reject(new Error(`${stage} has no deadline`)), 100))]);
    assert.equal(button.disabled, false);
    assert.equal(button.innerHTML, '生成圖片');
    assert.equal(app.shareList.length, 1);
    assert.match(alerts.at(-1).text, /逾時/);
  });
}

test('failed global-search worker cancels sibling downloads immediately', async () => {
  const quarters = Object.fromEntries(['2024_冬','2024_春','2024_夏','2024_秋'].map((q, i) => [q, dataset(`shared-${i}`)]));
  const pending = [];
  let fail;
  const {app} = createContext({fetchImpl: async (url, options) => {
    if (url.startsWith('search-index')) return responseFor(searchIndexFor(quarters));
    if (url.includes('2024_冬')) return new Promise(resolve => { fail = () => resolve(errorResponse(404)); });
    return new Promise((resolve, reject) => {
      pending.push(options.signal);
      options.signal.addEventListener('abort', () => reject(Object.assign(new Error('cancelled'), {name: 'AbortError'})), {once: true});
    });
  }});
  app.availableData = {2024: ['冬','春','夏','秋']};
  app.year = '2024'; app.season = '冬'; app.searchKeyword = 'shared';
  const load = app.loadData();
  while (pending.length < 3) await nextTick();
  fail(); await load; await nextTick();
  assert.equal(app.loadError.code, 'HTTP_404');
  assert.equal(app.rawAnimeList.length, 0);
  assert.ok(pending.every(signal => signal.aborted));
  assert.equal(app._pendingDataRequests.size, 0);
});

test('late response after cancellation cannot repopulate the cache', async () => {
  let finish;
  const {app} = createContext({fetchImpl: () => new Promise(resolve => { finish = resolve; })});
  const controller = new AbortController();
  const load = app.getDataset('2026_秋', controller.signal).catch(error => error.name);
  controller.abort(); await load; await nextTick();
  finish(responseFor(dataset('late'))); await nextTick();
  assert.equal(app.dataCache['2026_秋'], undefined);
  assert.equal(app._pendingDataRequests.size, 0);
});

function exportItem(name = 'original') {
  return {name, token: Symbol(name), img: `https://res.cloudinary.com/demo/image/upload/v1/anime_covers/${'a'.repeat(64)}`};
}

const sameTitleSeries = [
  {bangumi_id: 'anime-1672', anime_name: '科學×冒險生存！', original_name: '科学×冒険サバイバル！', external_ids: {bangumi: 480716}},
  {bangumi_id: 'anime-1936', anime_name: '科學×冒險生存！', original_name: '科学×冒険サバイバル！ 第2シリーズ', external_ids: {bangumi: 536878}},
].map(anime => ({...anime, anime_image_url: exportItem().img}));

test('share list keeps distinct series with the same title and deduplicates quarter/search copies', () => {
  const alerts = [];
  const {app} = createContext({swal: {fire: options => alerts.push(options)}});
  sameTitleSeries.forEach(anime => app.addToShare(anime));
  app.addToShare({...sameTitleSeries[0], result_key: 'search-only-key', source_quarters: ['2024年秋季']});
  assert.equal(app.shareList.length, 2);
  assert.equal(new Set(app.shareList.map(item => item.token)).size, 2);
  assert.equal(alerts.at(-1).title, '已在清單中');
});

for (const replacement of sameTitleSeries) {
  test(`export preserves newly queued ${replacement.bangumi_id} after removing the snapshot item`, async () => {
    let ready;
    const {app, context} = createContext({
      html2canvas: () => ({toBlob(callback) { callback({}); }}),
      navigatorImpl: {clipboard: {write: async () => {}}},
      swal: {fire() {}},
    });
    context.window.ClipboardItem = class {};
    context.document.fonts.ready = new Promise(resolve => { ready = resolve; });
    app.addToShare(sameTitleSeries[0]);
    const firstToken = app.shareList[0].token;
    const exporting = app.generateShareImage();
    await nextTick();
    app.removeFromShare(0);
    app.addToShare(replacement);
    const nextToken = app.shareList[0].token;
    ready();
    await exporting;
    assert.notEqual(firstToken, nextToken);
    assert.equal(app.shareList.length, 1);
    assert.equal(app.shareList[0].token, nextToken);
  });
}

for (const height of [1000, 8000, 20000]) {
  test(`share canvas bounds handle ${height}px content before rendering`, async () => {
    const renders = []; const alerts = [];
    const {app, context, button} = createContext({
      html2canvas: (element, options) => { renders.push(options); throw new Error('stop after measuring'); },
      swal: {fire: options => alerts.push(options)},
    });
    const create = context.document.createElement;
    context.document.createElement = tag => ({...create(tag), scrollHeight: height});
    app.shareList = [exportItem()];
    await app.generateShareImage();
    if (height > 16384) {
      assert.equal(renders.length, 0);
      assert.match(alerts[0].text, /分批匯出/);
    } else {
      assert.equal(renders.length, 1);
      assert.ok(renders[0].scale >= 1 && renders[0].scale <= 3);
      assert.ok(600 * height * renders[0].scale ** 2 <= 16000000 + 0.001);
      assert.ok(height * renders[0].scale <= 16384);
      if (height === 1000) assert.equal(renders[0].scale, 3);
    }
    assert.equal(button.disabled, false);
    assert.equal(app.shareList.length, 1);
  });
}

test('late toBlob callback after timeout cannot download or clear the list', async () => {
  let finish;
  let downloads = 0;
  const {app, context, button} = createContext({html2canvas: () => ({toBlob(callback) { finish = callback; }})});
  context.setTimeout = (callback, delay) => setTimeout(callback, delay >= 1000 ? 5 : delay);
  context.window.URL = {createObjectURL() { downloads++; return 'blob:test'; }};
  app.shareList = [exportItem()];
  await app.generateShareImage();
  finish({}); await nextTick();
  assert.equal(downloads, 0);
  assert.equal(app.shareList.length, 1);
  assert.equal(button.disabled, false);
});

for (const lateResult of ['resolved', 'rejected']) {
  for (const choice of ['download', 'cancel']) {
    test(`stalled clipboard offers the same PNG; ${choice} ignores a late ${lateResult} response`, {timeout: 2000}, async () => {
      let finishClipboard, rejectClipboard, prompt, closePrompt;
      let renders = 0, downloads = 0;
      const blob = {png: true};
      const alerts = [];
      const {app, context, button} = createContext({
        html2canvas: () => { renders++; return {toBlob(callback) { callback(blob); }}; },
        navigatorImpl: {clipboard: {write: () => new Promise((resolve, reject) => {
          finishClipboard = resolve; rejectClipboard = reject;
        })}},
        swal: {fire(options) {
          alerts.push(options);
          if (options.preConfirm) {
            prompt = options;
            return new Promise(resolve => { closePrompt = resolve; });
          }
        }},
      });
      context.window.ClipboardItem = class {};
      context.setTimeout = (callback, delay) => setTimeout(callback, delay >= 1000 ? 5 : delay);
      context.window.URL = {
        createObjectURL(value) { assert.equal(value, blob); return 'blob:prepared-png'; },
        revokeObjectURL() {},
      };
      const create = context.document.createElement;
      context.document.createElement = tag => ({...create(tag), click() { downloads++; }});
      app.shareList = [exportItem()];
      const exporting = app.generateShareImage();
      while (!prompt) await nextTick();
      assert.equal(prompt.confirmButtonText, '下載圖片');
      assert.equal(downloads, 0);
      assert.equal(app.shareList.length, 1);
      const added = exportItem('added while awaiting download');
      app.shareList.push(added);
      if (lateResult === 'resolved') finishClipboard();
      else rejectClipboard(new Error('late clipboard rejection'));
      await nextTick();
      assert.equal(app.shareList.length, 2);
      assert.equal(downloads, 0);
      if (choice === 'download') {
        prompt.preConfirm();
        prompt.preConfirm();
      }
      closePrompt({isConfirmed: choice === 'download'});
      await exporting;
      assert.equal(renders, 1);
      assert.equal(downloads, choice === 'download' ? 1 : 0);
      assert.equal(app.shareList.length, choice === 'download' ? 1 : 2);
      assert.equal(app.shareList.at(-1).token, added.token);
      assert.equal(button.disabled, false);
      assert.equal(button.innerHTML, '生成圖片');
      assert.equal(app._shareExportRunning, false);
      assert.equal(alerts.some(alert => alert.title === '圖片已複製！'), false);
    });
  }
}

test('export snapshots the original list, deduplicates clicks and preserves newly added items', async () => {
  let ready;
  let renders = 0;
  const {app, context} = createContext({
    html2canvas: () => { renders++; return {toBlob(callback) { callback({}); }}; },
    navigatorImpl: {clipboard: {write: async () => {}}},
    swal: {fire() {}},
  });
  context.window.ClipboardItem = class {};
  context.document.fonts.ready = new Promise(resolve => { ready = resolve; });
  app.shareList = [exportItem()];
  const first = app.generateShareImage();
  await nextTick();
  app.shareList.push(exportItem('added while preparing'));
  await app.generateShareImage();
  ready(); await first;
  assert.equal(renders, 1);
  assert.equal(app.shareList.length, 1);
  assert.equal(app.shareList[0].name, 'added while preparing');
  assert.equal(app._shareExportRunning, false);
});

test('scope proxies for one app share the same pending request', async () => {
  const responses = [];
  const {app} = createContext({fetchImpl: () => new Promise(resolve => { responses.push(resolve); })});
  const scope = new Proxy(app, {});
  const first = app.getDataset('2026_秋');
  const second = scope.getDataset('2026_秋');
  responses.forEach(resolve => resolve(responseFor(dataset('shared proxy'))));
  const results = await Promise.all([first, second]);
  assert.equal(responses.length, 1);
  assert.equal(results[0], results[1]);
  assert.equal(app._pendingDataRequests.size, 0);
});

test('rapid keyword replacement stops obsolete downloads and keeps four workers', async () => {
  const old = ['2024_冬','2024_春','2024_夏','2024_秋'];
  const recent = ['2025_冬','2025_春','2025_夏','2025_秋'];
  const quarters = Object.fromEntries([...old, ...recent].map(q => [q, dataset(`${old.includes(q) ? 'obsolete' : 'latest'}-${q}`)]));
  let active = 0; let maximum = 0; const started = [];
  const {app} = createContext({fetchImpl: async (url, options) => {
    if (url.startsWith('search-index')) return responseFor(searchIndexFor(quarters));
    const key = url.split('/')[1].split('.json')[0];
    started.push(key); active++; maximum = Math.max(maximum, active);
    if (recent.includes(key)) { await nextTick(); active--; return responseFor(quarters[key]); }
    return new Promise((resolve, reject) => options.signal.addEventListener('abort', () => {
      active--; reject(Object.assign(new Error('cancelled'), {name: 'AbortError'}));
    }, {once: true}));
  }});
  app.availableData = {2024: ['冬','春','夏','秋'], 2025: ['冬','春','夏','秋']};
  app.year = '2025'; app.season = '秋'; app.searchKeyword = 'obsolete';
  const previous = app.loadData();
  while (started.length < 4) await nextTick();
  app.searchKeyword = 'latest';
  await app.loadData(); await previous; await nextTick();
  assert.equal(maximum, 4);
  assert.equal(active, 0);
  assert.equal(app.filteredAnime.length, 4);
  assert.equal(app._pendingDataRequests.size, 0);
});

test('share limits reject excess items before loading the renderer or covers', async () => {
  const alerts = [];
  const {app, scripts, button} = createContext({swal: {fire: options => alerts.push(options)}});
  for (let i = 0; i < app.SHARE_MAX_ITEMS + 1; i++) app.addToShare({anime_name: `item-${i}`});
  assert.equal(app.shareList.length, 20);
  assert.match(alerts.at(-1).text, /20/);
  app.shareList.push(exportItem('over limit'));
  await app.generateShareImage();
  assert.equal(scripts.length, 0);
  assert.equal(button.disabled, false);
  assert.equal(app._shareExportRunning, false);
  assert.equal(app.shareList.length, 21);
  assert.match(alerts.at(-1).text, /分批匯出/);
});

test('large global results render in batches while filters still see the complete result set', () => {
  const {app} = createContext();
  app.rawAnimeList = Array.from({length: 1560}, (_, i) => ({...dataset(`common-${i}`).anime_list[0], premiere_date: i >= 1500 ? '二' : '一'}));
  app.searchKeyword = 'common';
  assert.equal(app.filteredAnime.length, 1560);
  assert.equal(app.visibleAnime.length, 60);
  assert.equal(app.hasMoreAnime, true);
  app.showMoreAnime();
  assert.equal(app.visibleAnime.length, 120);
  app.filterDay = '二'; app.saveFilterDay();
  assert.equal(app.filteredAnime.length, 60);
  assert.equal(app.visibleAnime.length, 60);
  assert.equal(app.visibleAnime[0].anime_name, 'common-1500');
  assert.equal(app.hasMoreAnime, false);
  app.filterDay = '全部'; app.searchKeyword = '';
  assert.equal(app.visibleAnime.length, 1560);
  assert.equal(app.hasMoreAnime, false);
});
