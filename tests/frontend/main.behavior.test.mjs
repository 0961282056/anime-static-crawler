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
