const DATA_CACHE_TTL_MS = 5 * 60 * 1000;
const FETCH_TIMEOUT_MS = 15 * 1000;
const MAX_FETCH_RETRIES = 2;
const RETRY_DELAYS_MS = [300, 700];
const HTML2CANVAS_TIMEOUT_MS = 15 * 1000;
const SHARE_IMAGE_TIMEOUT_MS = 15 * 1000;
const HTML2CANVAS_URL = 'https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js';
const HTML2CANVAS_INTEGRITY = 'sha384-ZZ1pncU3bQe8y31yfZdMFdSpttDoPmOZg2wguVK9almUodir1PghgT0eY7Mrty8H';
let html2canvasLoadPromise = null;
const pendingRequestStates = new WeakMap();

function getPendingRequestState(owner) {
    let state = pendingRequestStates.get(owner);
    if (!state) {
        state = new Map();
        pendingRequestStates.set(owner, state);
    }
    return state;
}

function createAbortError() {
    const error = new Error('Request aborted');
    error.name = 'AbortError';
    return error;
}

function isAbortError(error, signal = null) {
    return Boolean(signal && signal.aborted) || Boolean(error && error.name === 'AbortError');
}

function createDataError(code, userMessage, {retryable = false, status = null} = {}) {
    const error = new Error(userMessage);
    error.name = 'DataLoadError';
    error.code = code;
    error.status = Number.isInteger(status) ? status : null;
    error.retryable = retryable;
    error.userMessage = userMessage;
    return error;
}

function createShareError(code, message) {
    const error = new Error(message);
    error.name = 'ShareImageError';
    error.code = code;
    return error;
}

function createHttpDataError(status) {
    const safeStatus = Number.isInteger(status) ? status : 0;
    let code = 'HTTP_ERROR';
    let userMessage = `資料服務拒絕請求（HTTP ${safeStatus || '未知'}）。請稍後再試。`;

    if (safeStatus === 404) {
        code = 'HTTP_404';
        userMessage = '找不到這個季度的資料（HTTP 404）。請確認季度是否已發布，或稍後再試。';
    } else if (safeStatus === 429) {
        code = 'HTTP_429';
        userMessage = '資料服務目前忙碌（HTTP 429）。';
    } else if (safeStatus >= 500 && safeStatus <= 599) {
        code = 'HTTP_5XX';
        userMessage = `資料服務暫時異常（HTTP ${safeStatus}）。`;
    } else if (safeStatus === 408 || safeStatus === 425) {
        code = `HTTP_${safeStatus}`;
        userMessage = `資料服務回應逾時或尚未準備完成（HTTP ${safeStatus}）。`;
    }

    return createDataError(code, userMessage, {
        retryable: isRetryableStatus(safeStatus),
        status: safeStatus
    });
}

function normalizeDataError(error) {
    if (error && error.code && error.userMessage) return error;
    return createDataError(
        'NETWORK',
        '無法連線到資料服務。',
        {retryable: true}
    );
}

function validateDataset(payload) {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        throw createDataError('DATA_SCHEMA', '資料檔案格式不完整或已變更，無法安全顯示。', {retryable: false});
    }
    if (!Array.isArray(payload.anime_list)) {
        throw createDataError('DATA_SCHEMA', '資料檔案缺少動畫清單，無法安全顯示。', {retryable: false});
    }

    const optionalStringFields = ['bangumi_id', 'anime_image_url', 'premiere_date', 'premiere_time', 'story'];
    payload.anime_list.forEach((item, index) => {
        if (!item || typeof item !== 'object' || Array.isArray(item)) {
            throw createDataError('DATA_SCHEMA', `第 ${index + 1} 筆動畫資料格式錯誤，無法安全顯示。`, {retryable: false});
        }
        if (typeof item.anime_name !== 'string' || !item.anime_name.trim()) {
            throw createDataError('DATA_SCHEMA', `第 ${index + 1} 筆動畫資料缺少名稱，無法安全顯示。`, {retryable: false});
        }
        optionalStringFields.forEach(field => {
            if (item[field] !== undefined && item[field] !== null && typeof item[field] !== 'string') {
                throw createDataError('DATA_SCHEMA', `第 ${index + 1} 筆動畫資料的欄位格式錯誤，無法安全顯示。`, {retryable: false});
            }
        });
    });

    return payload;
}

function describeDataError(error, hasStaleData = false) {
    const normalized = normalizeDataError(error);
    const attempts = Number.isInteger(normalized.attempts) ? normalized.attempts : 0;
    let title = '資料載入失敗';
    let message = normalized.userMessage;

    if (normalized.code === 'NETWORK') title = '網路連線失敗';
    else if (normalized.code === 'FETCH_TIMEOUT') title = '資料請求逾時';
    else if (normalized.code === 'HTTP_404') title = '找不到季度資料';
    else if (normalized.code === 'HTTP_429') title = '資料服務忙碌';
    else if (normalized.code === 'HTTP_5XX') title = '資料服務暫時異常';
    else if (normalized.code === 'JSON_PARSE') {
        title = '資料內容錯誤';
        message = '資料檔案不是有效的 JSON，請稍後再試；若持續發生，請聯絡維護者。';
    } else if (normalized.code === 'DATA_SCHEMA') {
        title = '資料格式錯誤';
    }

    const finalAttempts = attempts || 1;
    if (normalized.retryable) {
        message += `系統已自動嘗試 ${finalAttempts} 次仍未成功，請稍後按「重新載入」再試。`;
    } else if (attempts > 1) {
        message += `（已嘗試 ${attempts} 次）`;
    }
    return {
        code: normalized.code || 'UNKNOWN',
        title,
        message,
        retryable: Boolean(normalized.retryable),
        hasStaleData,
    };
}

function describeClipboardError(error, supported = true) {
    if (!supported) {
        return '目前瀏覽器不支援自動複製，請手動選取動畫名稱複製。';
    }
    if (error && error.name === 'NotAllowedError') {
        return '瀏覽器拒絕剪貼簿權限，請允許此網站使用剪貼簿，或手動選取動畫名稱複製。';
    }
    return '複製文字失敗，請確認瀏覽器權限後再試，或手動選取動畫名稱複製。';
}

function describeShareError(error) {
    const messages = {
        SHARE_IMAGE_URL: '分享清單中有圖片網址未通過安全驗證，請重新加入該動畫後再試。',
        SHARE_IMAGE_LOAD: '部分圖片無法從圖片服務載入，請檢查網路後再試。',
        SHARE_RENDERER_TIMEOUT: '分享圖片元件載入逾時，請檢查網路後再試。',
        SHARE_RENDERER_NETWORK: '分享圖片元件載入失敗，請檢查網路後再試。',
        SHARE_RENDERER: '瀏覽器無法完成分享圖片渲染，請稍後再試。',
        SHARE_EXPORT: '圖片匯出失敗，請確認瀏覽器下載權限後再試。',
    };
    const requestedCode = error && error.code;
    const code = Object.prototype.hasOwnProperty.call(messages, requestedCode)
        ? requestedCode
        : 'SHARE_EXPORT';
    return {
        code,
        title: code === 'SHARE_IMAGE_URL' ? '圖片網址無法使用' : '生成圖片失敗',
        message: messages[code] || '瀏覽器無法完成圖片匯出，請稍後再試。',
    };
}

function waitWithAbort(delayMs, signal) {
    if (!signal) {
        return new Promise(resolve => setTimeout(resolve, delayMs));
    }
    if (signal.aborted) {
        return Promise.reject(createAbortError());
    }

    return new Promise((resolve, reject) => {
        let settled = false;
        const finish = (callback, value) => {
            if (settled) return;
            settled = true;
            clearTimeout(timerId);
            signal.removeEventListener('abort', onAbort);
            callback(value);
        };
        const onAbort = () => finish(reject, createAbortError());
        const timerId = setTimeout(() => finish(resolve), delayMs);
        signal.addEventListener('abort', onAbort, {once: true});
    });
}

function raceWithAbort(promise, signal) {
    if (!signal) return promise;
    if (signal.aborted) return Promise.reject(createAbortError());

    return new Promise((resolve, reject) => {
        let settled = false;
        const finish = (callback, value) => {
            if (settled) return;
            settled = true;
            signal.removeEventListener('abort', onAbort);
            callback(value);
        };
        const onAbort = () => finish(reject, createAbortError());
        signal.addEventListener('abort', onAbort, {once: true});
        promise.then(
            value => finish(resolve, value),
            error => finish(reject, error)
        );
    });
}

function isRetryableStatus(status) {
    return status === 408 || status === 425 || status === 429 || (status >= 500 && status <= 599);
}

async function fetchWithDeadline(url, signal, readResponse = response => response, timeoutMs = FETCH_TIMEOUT_MS) {
    const requestController = new AbortController();
    let timedOut = false;
    let timeoutId;
    const abortFromCaller = () => requestController.abort();

    if (signal) {
        if (signal.aborted) throw createAbortError();
        signal.addEventListener('abort', abortFromCaller, {once: true});
    }

    timeoutId = setTimeout(() => {
        timedOut = true;
        requestController.abort();
    }, timeoutMs);

    try {
        const response = await fetch(url, {signal: requestController.signal});
        return await readResponse(response);
    } catch (error) {
        if (signal && signal.aborted) throw createAbortError();
        if (timedOut) {
            const timeoutSeconds = Math.max(1, Math.ceil(Number(timeoutMs) / 1000));
            throw createDataError(
                'FETCH_TIMEOUT',
                `資料請求逾時（超過 ${timeoutSeconds} 秒）。`,
                {retryable: true}
            );
        }
        throw error;
    } finally {
        clearTimeout(timeoutId);
        if (signal) signal.removeEventListener('abort', abortFromCaller);
    }
}

async function fetchJsonWithDeadline(url, signal, timeoutMs = FETCH_TIMEOUT_MS) {
    return fetchWithDeadline(url, signal, async response => {
        if (!response.ok) return {response, data: null};
        try {
            return {response, data: await response.json()};
        } catch (error) {
            throw createDataError(
                'JSON_PARSE',
                '資料檔案不是有效的 JSON，請稍後再試；若持續發生，請聯絡維護者。',
                {retryable: false}
            );
        }
    }, timeoutMs);
}

function findCloudinaryDeliveryBoundary(pathSegments, uploadIndex) {
    for (let index = uploadIndex + 1; index < pathSegments.length; index += 1) {
        if (/^v[0-9]+$/.test(pathSegments[index]) || pathSegments[index] === 'anime_covers') {
            return index;
        }
    }
    return -1;
}

function buildCloudinaryTransformationUrl(sourceUrl, transformation) {
    let imageUrl;
    try {
        imageUrl = new URL(sourceUrl);
    } catch (error) {
        return null;
    }

    if (imageUrl.protocol !== 'https:' || imageUrl.hostname !== 'res.cloudinary.com') {
        return null;
    }

    const pathSegments = imageUrl.pathname.split('/');
    const uploadIndex = pathSegments.findIndex((segment, index) => (
        segment === 'upload' && pathSegments[index - 1] === 'image'
    ));
    if (uploadIndex < 0) return null;

    const deliveryBoundary = findCloudinaryDeliveryBoundary(pathSegments, uploadIndex);
    if (deliveryBoundary < 0 || deliveryBoundary >= pathSegments.length - 1) return null;

    imageUrl.pathname = pathSegments
        .slice(0, uploadIndex + 1)
        .concat(transformation, pathSegments.slice(deliveryBoundary))
        .join('/');
    return imageUrl.toString();
}

function loadHtml2Canvas() {
    if (typeof window !== 'undefined' && typeof window.html2canvas === 'function') {
        return Promise.resolve(window.html2canvas);
    }
    if (html2canvasLoadPromise) return html2canvasLoadPromise;

    html2canvasLoadPromise = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        let settled = false;
        const timeoutId = setTimeout(() => {
            if (settled) return;
            settled = true;
            reject(createShareError('SHARE_RENDERER_TIMEOUT', 'html2canvas 載入逾時'));
        }, HTML2CANVAS_TIMEOUT_MS);
        const finish = (callback, value) => {
            if (settled) return;
            settled = true;
            clearTimeout(timeoutId);
            callback(value);
        };
        script.src = HTML2CANVAS_URL;
        script.integrity = HTML2CANVAS_INTEGRITY;
        script.crossOrigin = 'anonymous';
        script.async = true;
        script.dataset.animeHtml2canvas = 'true';
        script.onload = () => {
            if (typeof window.html2canvas === 'function') {
                finish(resolve, window.html2canvas);
            } else {
                finish(reject, createShareError('SHARE_RENDERER', 'html2canvas 載入後未提供函式'));
            }
        };
        script.onerror = () => finish(reject, createShareError('SHARE_RENDERER_NETWORK', 'html2canvas 載入失敗'));
        document.head.appendChild(script);
    }).catch(error => {
        html2canvasLoadPromise = null;
        const failedScript = document.querySelector('script[data-anime-html2canvas="true"]');
        if (failedScript && failedScript.parentNode) failedScript.parentNode.removeChild(failedScript);
        throw error;
    });

    return html2canvasLoadPromise;
}

function readAppConfig() {
    const element = document.getElementById('app-config');
    if (!element) {
        throw new Error('Missing #app-config build metadata');
    }
    return {
        defaultYear: element.dataset.defaultYear,
        defaultSeason: element.dataset.defaultSeason,
        buildVersion: element.dataset.buildVersion,
        availableData: JSON.parse(element.dataset.availableData)
    };
}

function animeApp() {
    const appConfig = readAppConfig();
    return {
        // --- 1. 資料狀態 ---
        availableData: appConfig.availableData,
        defaultYear: appConfig.defaultYear,
        defaultSeason: appConfig.defaultSeason,
        buildVersion: appConfig.buildVersion,
        years: [],
        seasons: [],
        
        // --- 2. 選擇狀態 (雙向綁定) ---
        year: '',
        season: '',
        filterDay: '全部',
        searchKeyword: '',
        
        // --- 3. 應用狀態 ---
        rawAnimeList: [],
        shareList: [],
        loading: false,
        loadError: null,
        storageWarning: '',
        _storageWarningDismissed: false,
        lastUpdateTime: '',
        showBackToTop: false,
        
        // --- 4. 快取與設定 ---
        dataCache: {},
        _pendingDataRequests: new Map(),
        _loadRequestId: 0,
        _activeLoadAbort: null,
        _activeCacheKey: null,
        STORAGE_KEYS: {
            YEAR: 'anime_user_year',
            SEASON: 'anime_user_season',
            FILTER_DAY: 'anime_user_filter_day',
            SCROLL: 'anime_user_scroll_pos'
        },
        DATA_CACHE_TTL_MS,
        FETCH_TIMEOUT_MS,
        MAX_FETCH_RETRIES,
        RETRY_DELAYS_MS,

        // --- 5. 初始化 ---
        initApp() {
            // A. 初始化年份選單
            this.years = Object.keys(this.availableData).sort((a, b) => b - a);
            const savedDay = this.readStorage(this.STORAGE_KEYS.FILTER_DAY);
            this.filterDay = savedDay || '全部';

            // --- 🟢 第一層 $nextTick: 等待年份選單就緒 ---
            this.$nextTick(() => {
                // 1. 嘗試讀取使用者上次的選擇
                const savedYear = this.readStorage(this.STORAGE_KEYS.YEAR);
                const savedSeason = this.readStorage(this.STORAGE_KEYS.SEASON);
                
                // 2. 準備 "現在時間" 作為備案
                const now = new Date();
                const currentYear = now.getFullYear().toString();
                const month = now.getMonth() + 1;
                let currentSeason = '';
                if (month >= 1 && month <= 3) currentSeason = '冬';
                else if (month >= 4 && month <= 6) currentSeason = '春';
                else if (month >= 7 && month <= 9) currentSeason = '夏';
                else currentSeason = '秋';

                // 3. 決策變數
                let targetYear = this.defaultYear;
                let targetSeason = this.defaultSeason;
                let shouldRestoreScroll = false; // 預設不恢復捲動位置

                // --- 決策邏輯 ---
                // 優先權 1: 使用者存檔 (必須有效才算)
                if (savedYear && savedSeason && 
                    this.availableData[savedYear] && 
                    this.availableData[savedYear].includes(savedSeason)) {
                    
                    targetYear = savedYear;
                    targetSeason = savedSeason;
                    shouldRestoreScroll = true; // ✅ 只有這種情況才恢復捲動
                    console.log(`✅ [Init] 還原使用者存檔: ${targetYear} ${targetSeason}`);
                
                // 優先權 2: 現在時間 (智慧跳轉)
                } else if (this.availableData[currentYear] && 
                           this.availableData[currentYear].includes(currentSeason)) {
                    
                    targetYear = currentYear;
                    targetSeason = currentSeason;
                    // shouldRestoreScroll 保持 false
                    console.log(`ℹ️ [Init] 無存檔，跳轉至當前時間: ${targetYear} ${targetSeason}`);
                
                // 優先權 3: 系統預設 (Fallback)
                } else {
                    console.log(`⚠️ [Init] 皆無效，使用系統預設: ${targetYear} ${targetSeason}`);
                }

                // 4. 設定年份
                this.year = targetYear;
                this.seasons = this.availableData[this.year] || [];

                // --- 🟢 第二層 $nextTick: 等待季節選單就緒 ---
                this.$nextTick(() => {
                    // 5. 設定季節
                    if (this.seasons.includes(targetSeason)) {
                        this.season = targetSeason;
                    } else if (this.seasons.length > 0) {
                        this.season = this.seasons[0];
                    }
                    
                    // 6. 載入資料 (傳入是否恢復捲動的旗標)
                    this.loadData(shouldRestoreScroll);
                });
            });

            // 監聽捲動與頁面隱藏 (保持不變)
            window.addEventListener('scroll', () => {
                this.showBackToTop = window.scrollY > 300;
                clearTimeout(this._scrollTimeout);
                this._scrollTimeout = setTimeout(() => {
                    this.writeStorage(this.STORAGE_KEYS.SCROLL, window.scrollY);
                }, 200);
            });
            
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState === 'hidden') {
                    this.writeStorage(this.STORAGE_KEYS.SCROLL, window.scrollY);
                }
            });
        },

        readStorage(key, fallback = null) {
            try {
                if (typeof globalThis.localStorage === 'undefined') {
                    this.noteStorageFailure();
                    return fallback;
                }
                const value = globalThis.localStorage.getItem(key);
                return value === null ? fallback : value;
            } catch (error) {
                this.noteStorageFailure();
                return fallback;
            }
        },

        writeStorage(key, value) {
            try {
                if (typeof globalThis.localStorage === 'undefined') {
                    this.noteStorageFailure();
                    return false;
                }
                globalThis.localStorage.setItem(key, String(value));
                return true;
            } catch (error) {
                this.noteStorageFailure();
                return false;
            }
        },

        noteStorageFailure() {
            if (!this.storageWarning && !this._storageWarningDismissed) {
                this.storageWarning = '瀏覽器目前無法儲存偏好設定；本頁仍可使用，但重新整理後可能不會保留你的選擇。';
            }
        },

        dismissStorageWarning() {
            this.storageWarning = '';
            this._storageWarningDismissed = true;
        },

        saveFilterDay() {
            this.writeStorage(this.STORAGE_KEYS.FILTER_DAY, this.filterDay);
        },

        // --- 6. 核心邏輯 ---
        updateSeasonOptions(targetSeason = null) {
            this.seasons = this.availableData[this.year] || [];
            
            if (targetSeason && this.seasons.includes(targetSeason)) {
                this.season = targetSeason;
            } else if (this.seasons.length > 0) {
                this.season = this.seasons[0];
            } else {
                this.season = '';
            }
        },

        changeYear() {
            this.updateSeasonOptions();
            return this.loadData();
        },

        seasonLabel(season) {
            const monthBySeason = {'冬': 1, '春': 4, '夏': 7, '秋': 10};
            return `${season}(${monthBySeason[season]}月)`;
        },

        animeSearchUrl(name) {
            return `https://ani.gamer.com.tw/search.php?keyword=${encodeURIComponent(name)}`;
        },

        isCurrentLoad(requestId) {
            return this._loadRequestId === requestId;
        },

        async fetchDatasetFromNetwork(cacheKey, signal) {
            const url = `data/${cacheKey}.json?v=${encodeURIComponent(this.buildVersion)}`;
            let lastError = null;
            const maxRetries = Number.isInteger(this.MAX_FETCH_RETRIES)
                ? Math.max(0, this.MAX_FETCH_RETRIES)
                : MAX_FETCH_RETRIES;
            const retryDelays = Array.isArray(this.RETRY_DELAYS_MS) ? this.RETRY_DELAYS_MS : RETRY_DELAYS_MS;

            for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
                if (signal && signal.aborted) throw createAbortError();

                try {
                    const {response, data} = await fetchJsonWithDeadline(url, signal, this.FETCH_TIMEOUT_MS);
                    if (!response.ok) {
                        const statusError = createHttpDataError(response.status);
                        if (!statusError.retryable) throw statusError;
                        lastError = statusError;
                    } else {
                        return validateDataset(data);
                    }
                } catch (error) {
                    if (isAbortError(error, signal)) throw createAbortError();
                    const normalized = normalizeDataError(error);
                    if (normalized.retryable === false) {
                        normalized.attempts = attempt + 1;
                        throw normalized;
                    }
                    lastError = normalized;
                }

                if (attempt < maxRetries) {
                    await waitWithAbort(
                        retryDelays[attempt] ?? retryDelays[retryDelays.length - 1] ?? 0,
                        signal
                    );
                }
            }

            const finalError = lastError || createDataError(
                'UNKNOWN',
                '載入資料時發生未預期錯誤，請重試；若持續發生，請聯絡維護者。',
                {retryable: false}
            );
            finalError.attempts = maxRetries + 1;
            throw finalError;
        },

        getDataset(cacheKey, signal) {
            if (signal && signal.aborted) return Promise.reject(createAbortError());
            const now = Date.now();
            const cached = this.dataCache[cacheKey];
            if (
                cached &&
                Number.isFinite(cached.cachedAt) &&
                now - cached.cachedAt < this.DATA_CACHE_TTL_MS
            ) {
                return raceWithAbort(Promise.resolve(cached), signal);
            }

            const pendingState = getPendingRequestState(this);
            let pendingToken = this._pendingDataRequests.get(cacheKey);
            let pending = pendingToken ? pendingState.get(pendingToken) : null;
            if (pending && pending.isAborted()) {
                this._pendingDataRequests.delete(cacheKey);
                pendingState.delete(pendingToken);
                pending = null;
            }
            if (!pending) {
                const requestController = new AbortController();
                const requestToken = Symbol(`data:${cacheKey}`);
                const entry = {
                    token: requestToken,
                    promise: null,
                    isAborted: () => requestController.signal.aborted,
                    abort: () => requestController.abort()
                };
                entry.promise = this.fetchDatasetFromNetwork(cacheKey, requestController.signal)
                    .then(data => {
                        const dataset = {...data, cachedAt: Date.now()};
                        this.dataCache[cacheKey] = dataset;
                        return dataset;
                    })
                    .finally(() => {
                        const currentToken = this._pendingDataRequests.get(cacheKey);
                        if (currentToken === requestToken) {
                            this._pendingDataRequests.delete(cacheKey);
                        }
                        pendingState.delete(requestToken);
                    });
                pendingState.set(requestToken, entry);
                this._pendingDataRequests.set(cacheKey, requestToken);
                pending = entry;
            }

            // 每個畫面請求都有自己的 abort signal；共用的網路 Promise 不會被舊請求取消。
            return raceWithAbort(pending.promise, signal);
        },

        setLastUpdateTime(generatedAt) {
            if (!generatedAt) {
                this.lastUpdateTime = '';
                return;
            }
            const date = new Date(generatedAt);
            if (Number.isNaN(date.getTime())) {
                this.lastUpdateTime = '';
                return;
            }
            this.lastUpdateTime = `更新於 ${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()} ${date.getHours()}:${date.getMinutes()}`;
        },

        scheduleScroll(requestId, shouldRestoreScroll) {
            if (!this.isCurrentLoad(requestId)) return;

            const scroll = () => {
                if (!this.isCurrentLoad(requestId)) return;
                if (shouldRestoreScroll) {
                    const savedPos = parseInt(this.readStorage(this.STORAGE_KEYS.SCROLL, '0'), 10);
                    if (Number.isFinite(savedPos) && savedPos > 0) {
                        setTimeout(() => {
                            if (!this.isCurrentLoad(requestId)) return;
                            window.scrollTo({top: savedPos, behavior: 'auto'});
                        }, 100);
                    }
                } else {
                    window.scrollTo({top: 0, behavior: 'auto'});
                }
            };

            if (typeof this.$nextTick === 'function') {
                this.$nextTick(scroll);
            } else {
                scroll();
            }
        },

        async loadData(shouldRestoreScroll = false) {
            if (!this.year || !this.season) return;

            const requestId = this._loadRequestId + 1;
            this._loadRequestId = requestId;
            if (this._activeLoadAbort) this._activeLoadAbort();
            const controller = new AbortController();
            const abortLoad = () => controller.abort();
            this._activeLoadAbort = abortLoad;

            const cacheKey = `${this.year}_${this.season}`;
            const previousCacheKey = this._activeCacheKey;
            const keepPreviousData = previousCacheKey === cacheKey && this.rawAnimeList.length > 0;
            if (this._activeCacheKey && this._activeCacheKey !== cacheKey) {
                const pendingState = getPendingRequestState(this);
                const previousToken = this._pendingDataRequests.get(this._activeCacheKey);
                const previousPending = previousToken ? pendingState.get(previousToken) : null;
                if (previousPending && !previousPending.isAborted()) {
                    previousPending.abort();
                }
            }
            this._activeCacheKey = cacheKey;
            this.writeStorage(this.STORAGE_KEYS.YEAR, this.year);
            this.writeStorage(this.STORAGE_KEYS.SEASON, this.season);
            this.loading = true;
            this.loadError = null;
            if (!keepPreviousData) {
                this.rawAnimeList = [];
                this.lastUpdateTime = '';
            }

            try {
                const dataset = await this.getDataset(cacheKey, controller.signal);
                if (!this.isCurrentLoad(requestId)) return;
                this.rawAnimeList = dataset.anime_list;
                this.setLastUpdateTime(dataset.generated_at);
            } catch (error) {
                if (!this.isCurrentLoad(requestId) || isAbortError(error, controller.signal)) return;
                const safeError = describeDataError(error, keepPreviousData);
                this.loadError = safeError;
                console.error('[資料載入錯誤]', {
                    code: safeError.code,
                    status: error && error.status ? error.status : null,
                    attempts: error && error.attempts ? error.attempts : 1,
                });
            } finally {
                if (!this.isCurrentLoad(requestId)) return;
                this.loading = false;
                if (this._activeLoadAbort === abortLoad) this._activeLoadAbort = null;
                this.scheduleScroll(requestId, shouldRestoreScroll);
            }
        },

        retryLoad() {
            return this.loadData(false);
        },

        cardImageUrl(anime, width = 600) {
            const sourceUrl = anime && anime.anime_image_url;
            if (!sourceUrl || sourceUrl === '無圖片') {
                return 'https://placehold.co/300x450/333/999?text=No+Image';
            }
            return buildCloudinaryTransformationUrl(sourceUrl, `c_limit,w_${width},f_auto,q_auto`) || sourceUrl;
        },

        cardImageSrcset(anime) {
            const sourceUrl = anime && anime.anime_image_url;
            if (!sourceUrl || sourceUrl === '無圖片') return '';
            const widths = [300, 600, 900];
            const urls = widths.map(width => {
                const imageUrl = buildCloudinaryTransformationUrl(
                    sourceUrl,
                    `c_limit,w_${width},f_auto,q_auto`
                );
                return imageUrl ? `${imageUrl} ${width}w` : null;
            }).filter(Boolean);
            return urls.join(', ');
        },

        // --- 7. 資料篩選 ---
        get filteredAnime() {
            let list = this.rawAnimeList;

            if (this.filterDay !== '全部') {
                list = list.filter(item => item.premiere_date === this.filterDay);
            }

            if (this.searchKeyword) {
                const k = this.searchKeyword.toLowerCase().trim();
                list = list.filter(item => 
                    (item.anime_name && item.anime_name.toLowerCase().includes(k)) ||
                    (item.story && item.story.toLowerCase().includes(k))
                );
            }
            return list;
        },

        // --- 8. 互動功能 (不變) ---
        copyText(text) {
            const clipboard = typeof navigator !== 'undefined' && navigator ? navigator.clipboard : null;
            const supported = Boolean(clipboard && typeof clipboard.writeText === 'function');
            if (!supported) {
                if (typeof Swal !== 'undefined' && Swal.fire) {
                    Swal.fire({
                        toast: true, position: 'top-end', icon: 'warning',
                        title: '無法自動複製', text: describeClipboardError(null, false),
                        timer: 2500, showConfirmButton: false,
                        background: '#2b2b2b', color: '#fff'
                    });
                }
                return Promise.resolve(false);
            }

            return Promise.resolve()
                .then(() => clipboard.writeText(String(text ?? '')))
                .then(() => {
                    if (typeof Swal !== 'undefined' && Swal.fire) {
                        Swal.fire({
                            toast: true, position: 'top-end', icon: 'success',
                            title: '已複製', showConfirmButton: false, timer: 1000,
                            background: '#2b2b2b', color: '#fff'
                        });
                    }
                    return true;
                })
                .catch(error => {
                    if (typeof Swal !== 'undefined' && Swal.fire) {
                        Swal.fire({
                            toast: true, position: 'top-end', icon: 'error',
                            title: '複製失敗', text: describeClipboardError(error),
                            timer: 3000, showConfirmButton: false,
                            background: '#2b2b2b', color: '#fff'
                        });
                    }
                    return false;
                });
        },

        showStory(title, story) {
            Swal.fire({
                title: title,
                text: story || '暫無簡介',
                background: '#1e1e1e', color: '#e0e0e0', 
                confirmButtonColor: '#bb86fc'
            });
        },

        addToShare(anime) {
            if (this.shareList.some(i => i.name === anime.anime_name)) {
                Swal.fire({toast: true, position: 'top', icon: 'warning', title: '已在清單中', timer: 1000, showConfirmButton: false, background: '#2b2b2b', color: '#fff'});
                return;
            }
            this.shareList.push({
                name: anime.anime_name,
                img: (anime.anime_image_url && anime.anime_image_url !== '無圖片') ? anime.anime_image_url : 'https://placehold.co/50x50',
                date: anime.premiere_date || '?',
                time: anime.premiere_time || '?'
            });
            this.$nextTick(() => {
                const container = document.getElementById('shareListContainer');
                if(container) container.scrollTop = container.scrollHeight;
            });
            Swal.fire({toast: true, position: 'top', icon: 'success', title: '已加入', timer: 1000, showConfirmButton: false, background: '#2b2b2b', color: '#fff'});
        },

        removeFromShare(index) {
            this.shareList.splice(index, 1);
        },

        // --- 分享清單產圖 ---
        async generateShareImage() {
            const btn = document.getElementById('copyButton');
            const originalText = btn ? btn.innerHTML : '';
            let exportContainer = null;
            try {
                if (btn) {
                    btn.disabled = true;
                    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 處理最高畫質圖片...';
                }

                const renderHtml2Canvas = await loadHtml2Canvas();
                if (typeof renderHtml2Canvas !== 'function') {
                    throw createShareError('SHARE_RENDERER', 'html2canvas 載入後無法使用');
                }

                // 建立隱藏容器；URL 驗證也在此 try 內，確保任何例外都會復原按鈕。
                exportContainer = document.createElement('div');
                exportContainer.style.cssText = `
                    position: fixed; top: 0; left: -9999px; z-index: -1;
                    width: 600px;
                    background-color: #1a1a1a;
                    padding: 40px;
                    display: flex; flex-direction: column; gap: 40px;
                    font-family: 'Noto Sans TC', sans-serif;
                `;

                // 使用 DOM API 建卡片，避免把外部資料拼成 HTML。
                this.shareList.forEach(item => {
                    let imageUrl;
                    try {
                        imageUrl = new URL(item.img, window.location.origin);
                    } catch (error) {
                        throw createShareError('SHARE_IMAGE_URL', '分享圖片網址格式錯誤');
                    }
                    if (
                        imageUrl.protocol !== 'https:' ||
                        imageUrl.hostname !== 'res.cloudinary.com' ||
                        !imageUrl.pathname.includes('/image/upload/')
                    ) {
                        throw createShareError('SHARE_IMAGE_URL', '分享圖片來源不是允許的 Cloudinary URL');
                    }

                    const transformedUrl = buildCloudinaryTransformationUrl(
                        imageUrl.toString(),
                        'q_auto:best,f_auto'
                    );
                    if (!transformedUrl) {
                        throw createShareError('SHARE_IMAGE_URL', '分享圖片 URL 格式不受支援');
                    }

                    const card = document.createElement('div');
                    card.style.cssText = `
                        width: 100%; display: flex; flex-direction: column;
                        background-color: #2b2b2b; border-radius: 24px;
                        overflow: hidden; box-shadow: 0 20px 50px rgba(0,0,0,0.5);
                    `;

                    const imageWrapper = document.createElement('div');
                    imageWrapper.style.cssText = 'width: 100%; line-height: 0;';
                    const image = document.createElement('img');
                    image.src = transformedUrl;
                    image.crossOrigin = 'anonymous';
                    image.alt = '';
                    image.decoding = 'async';
                    image.style.cssText = 'width: 100%; height: auto; display: block;';
                    imageWrapper.appendChild(image);

                    const titleWrapper = document.createElement('div');
                    titleWrapper.style.cssText = `
                        padding: 30px 35px; background-color: #252525;
                        border-top: 1px solid #333; display: flex;
                        flex-direction: column; justify-content: center;
                    `;
                    const title = document.createElement('h2');
                    title.textContent = String(item.name || '');
                    title.style.cssText = `
                        margin: 0; font-size: 42px; font-weight: 700;
                        color: #ffffff; line-height: 1.4; min-height: 1.4em;
                    `;
                    titleWrapper.appendChild(title);
                    card.append(imageWrapper, titleWrapper);
                    exportContainer.appendChild(card);
                });

                document.body.appendChild(exportContainer);

                // 架構防禦：剪貼簿不可用時自動下載。
                const triggerFallbackDownload = (blob) => {
                    try {
                        const url = window.URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.style.display = 'none';
                        a.href = url;
                        a.download = `anime_list_${new Date().getTime()}.png`;
                        document.body.appendChild(a);
                        a.click();
                        window.URL.revokeObjectURL(url);
                        document.body.removeChild(a);
                    } catch (error) {
                        throw createShareError('SHARE_EXPORT', '圖片下載備案失敗');
                    }

                    Swal.fire({
                        icon: 'success',
                        title: '圖片已下載！',
                        text: '因瀏覽器限制剪貼簿，已自動為您下載圖片',
                        background: '#1e1e1e', color: '#fff',
                        timer: 3000, showConfirmButton: false
                    });
                };

                // 等待所有圖片與字體載入，避免截圖競態。
                const images = Array.from(exportContainer.querySelectorAll('img'));
                await Promise.all(images.map(img => {
                    if (img.complete) {
                        if (typeof img.naturalWidth === 'number' && img.naturalWidth === 0) {
                            return Promise.reject(createShareError('SHARE_IMAGE_LOAD', '分享圖片無法載入'));
                        }
                        return Promise.resolve();
                    }
                    return new Promise((resolve, reject) => {
                        let settled = false;
                        const finish = (callback, value) => {
                            if (settled) return;
                            settled = true;
                            clearTimeout(timeoutId);
                            callback(value);
                        };
                        const timeoutId = setTimeout(() => {
                            finish(reject, createShareError('SHARE_IMAGE_LOAD', '分享圖片載入逾時'));
                        }, SHARE_IMAGE_TIMEOUT_MS);
                        img.onload = () => finish(resolve);
                        img.onerror = () => finish(reject, createShareError('SHARE_IMAGE_LOAD', '分享圖片無法載入'));
                    });
                }));
                if (document.fonts && document.fonts.ready) await document.fonts.ready;

                let canvas;
                try {
                    canvas = await renderHtml2Canvas(exportContainer, {
                        scale: 3, useCORS: true, allowTaint: true,
                        backgroundColor: '#1a1a1a', logging: false, letterRendering: 1
                    });
                } catch (error) {
                    throw createShareError('SHARE_RENDERER', '瀏覽器無法完成分享圖片渲染');
                }
                if (!canvas || typeof canvas.toBlob !== 'function') {
                    throw createShareError('SHARE_EXPORT', '瀏覽器未提供圖片匯出功能');
                }

                await new Promise((resolve, reject) => {
                    const rejectExport = () => reject(createShareError('SHARE_EXPORT', '圖片匯出失敗'));
                    try {
                        canvas.toBlob(blob => {
                            try {
                                if (!blob) return rejectExport();

                                const clipboard = typeof navigator !== 'undefined' && navigator ? navigator.clipboard : null;
                                if (clipboard && window.ClipboardItem) {
                                    clipboard.write([new window.ClipboardItem({'image/png': blob})])
                                        .then(() => {
                                            Swal.fire({
                                                icon: 'success', title: '圖片已複製！',
                                                text: '可直接貼上至 LINE 或社群，清單已清空',
                                                background: '#1e1e1e', color: '#fff',
                                                timer: 2000, showConfirmButton: false
                                            });
                                            this.shareList = [];
                                            resolve();
                                        })
                                        .catch(() => {
                                            console.warn('[Clipboard] 寫入被拒絕，啟動下載備案');
                                            try {
                                                triggerFallbackDownload(blob);
                                                this.shareList = [];
                                                resolve();
                                            } catch (error) {
                                                reject(error);
                                            }
                                        });
                                } else {
                                    console.warn('[Clipboard] API 不支援，啟動下載備案');
                                    try {
                                        triggerFallbackDownload(blob);
                                        this.shareList = [];
                                        resolve();
                                    } catch (error) {
                                        reject(error);
                                    }
                                }
                            } catch (error) {
                                reject(error instanceof Error ? error : createShareError('SHARE_EXPORT', '圖片匯出失敗'));
                            }
                        }, 'image/png');
                    } catch (error) {
                        rejectExport();
                    }
                });
            } catch (error) {
                const safeError = describeShareError(error);
                console.error('[ShareImage Error]', {code: safeError.code});
                if (typeof Swal !== 'undefined' && Swal.fire) {
                    Swal.fire({
                        icon: 'error',
                        title: safeError.title,
                        text: `${safeError.message}（錯誤代碼：${safeError.code}）`,
                        background: '#1e1e1e', color: '#fff'
                    });
                }
            } finally {
                if (exportContainer && document.body.contains(exportContainer)) {
                    document.body.removeChild(exportContainer);
                }
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = originalText;
                }
            }
        },

        scrollToTop() {
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }
    };
}

document.addEventListener('alpine:init', () => {
    Alpine.data('animeApp', animeApp);
});
