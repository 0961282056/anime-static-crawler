// Requires: settings, data, search, presentation, sharing
function animeApp() {
    const appConfig = readAppConfig();
    const app = {
        // --- 1. 資料狀態 ---
        availableData: appConfig.availableData,
        defaultYear: appConfig.defaultYear,
        defaultSeason: appConfig.defaultSeason,
        buildVersion: appConfig.buildVersion,
        dataVersion: appConfig.dataVersion,
        quarterVersions: appConfig.quarterVersions,
        reviewedBroadcastSources: appConfig.reviewedBroadcastSources,
        years: [],
        seasons: [],

        // --- 2. 選擇狀態 (雙向綁定) ---
        year: '',
        season: '',
        filterDay: '全部',
        filterStatus: '全部',
        searchKeyword: '',
        visibleCount: SEARCH_PAGE_SIZE,

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
        _shareExportRunning: false,
        _shareItemSequence: 0,
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
        SHARE_MAX_ITEMS,

        // --- 5. 初始化 ---
        initApp() {
            // A. 初始化年份選單
            this.years = Object.keys(this.availableData).sort((a, b) => b - a);
            const savedDay = this.readStorage(this.STORAGE_KEYS.FILTER_DAY);
            this.filterDay = Object.hasOwn(BROADCAST_DAY_LABELS, savedDay) ? savedDay : '全部';

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
            this.resetVisibleAnime();
            this.writeStorage(this.STORAGE_KEYS.FILTER_DAY, this.filterDay);
        },

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
        }
    };
    return Object.defineProperties(app, {
        ...Object.getOwnPropertyDescriptors(dataMethods),
        ...Object.getOwnPropertyDescriptors(searchMethods),
        ...Object.getOwnPropertyDescriptors(presentationMethods),
        ...Object.getOwnPropertyDescriptors(sharingMethods),
    });
}

document.addEventListener('alpine:init', () => {
    Alpine.data('animeApp', animeApp);
});
