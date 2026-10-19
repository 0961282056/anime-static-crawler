// Requires: settings, errors, schema, transport
const dataMethods = {
    isCurrentLoad(requestId) {
        return this._loadRequestId === requestId;
    },

    async fetchDatasetFromNetwork(cacheKey, signal) {
        const version = this.quarterVersions[cacheKey] || this.dataVersion;
        const url = cacheKey === '__search_index__' ? `search-index.json?v=${encodeURIComponent(version)}` : `data/${cacheKey}.json?v=${encodeURIComponent(version)}`;
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
                    return cacheKey === '__search_index__' ? validateSearchIndex(data, this) : validateDataset(data);
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
        const version = this.quarterVersions[cacheKey] || this.dataVersion;
        const cached = this.dataCache[cacheKey];
        if (
            cached &&
            cached.version === (this.quarterVersions[cacheKey] || this.dataVersion) &&
            Number.isFinite(cached.cachedAt) &&
            now - cached.cachedAt < this.DATA_CACHE_TTL_MS
        ) {
            return raceWithAbort(Promise.resolve(cached), signal);
        }

        const pendingState = getPendingRequestState(this);
        let pendingToken = this._pendingDataRequests.get(cacheKey);
        let pending = pendingToken ? pendingState.get(pendingToken) : null;
        if (pending && (pending.isAborted() || pending.version !== version)) {
            pending.abort();
            this._pendingDataRequests.delete(cacheKey);
            pendingState.delete(pendingToken);
            pending = null;
        }
        if (!pending) {
            const requestController = new AbortController();
            const requestToken = Symbol(`data:${cacheKey}`);
            const entry = {
                token: requestToken,
                version,
                promise: null,
                isAborted: () => requestController.signal.aborted,
                abort: () => requestController.abort()
            };
            entry.promise = this.fetchDatasetFromNetwork(cacheKey, requestController.signal)
                .then(data => {
                    const dataset = {...data, version: entry.version, cachedAt: Date.now()};
                    if (entry.version === (this.quarterVersions[cacheKey] || this.dataVersion)) {
                        this.dataCache[cacheKey] = dataset;
                    }
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

        const searchingAll = this.searchKeyword.trim().length > 0;
        const cacheKey = searchingAll ? '__all_quarters__' : `${this.year}_${this.season}`;
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
            const dataset = searchingAll
                ? await this.getAllQuarterData(controller.signal)
                : await this.getDataset(cacheKey, controller.signal);
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

    async retryLoad() {
        if (['SEARCH_SCHEMA', 'SEARCH_VERSION'].includes(this.loadError?.code)) {
            try {
                const {response, data} = await fetchJsonWithDeadline('release.json?refresh=' + Date.now(), null, this.FETCH_TIMEOUT_MS);
                const quarters = Object.entries(this.availableData).flatMap(([year, seasons]) => seasons.map(season => `${year}_${season}`));
                if (!response.ok || data?.schema_version !== 1 || !/^[a-f0-9]{16}$/.test(data.data_version) || !data.quarter_versions || Object.keys(data.quarter_versions).length !== quarters.length || quarters.some(key => !/^[a-f0-9]{64}$/.test(data.quarter_versions[key]))) {
                    throw createDataError('SEARCH_SCHEMA', '無法取得完整的新版搜尋資料，請稍後重試。', {retryable: false});
                }
                if (data.build_version !== this.buildVersion) {
                    throw createDataError('APP_UPDATED', '網站已有新版，請重新整理頁面後再試。', {retryable: false});
                }
                this.dataVersion = data.data_version;
                this.quarterVersions = data.quarter_versions;
                this.dataCache = {};
            } catch (error) {
                this.loadError = describeDataError(error, this.rawAnimeList.length > 0);
                return;
            }
        }
        return this.loadData(false);
    }
};
