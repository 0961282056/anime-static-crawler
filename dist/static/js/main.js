function animeApp() {
    return {
        // --- 1. 資料狀態 ---
        availableData: window.SERVER_DATA.availableData,
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
        lastUpdateTime: '',
        showBackToTop: false,
        
        // --- 4. 快取與設定 ---
        dataCache: {},
        STORAGE_KEYS: {
            YEAR: 'anime_user_year',
            SEASON: 'anime_user_season',
            FILTER_DAY: 'anime_user_filter_day',
            SCROLL: 'anime_user_scroll_pos'
        },

        // --- 5. 初始化 (相當於 jQuery ready) ---
        initApp() {
            // A. 初始化年份選單 (倒序)
            this.years = Object.keys(this.availableData).sort((a, b) => b - a);
            
            // B. 恢復狀態 (從 LocalStorage 或後端預設)
            const savedYear = localStorage.getItem(this.STORAGE_KEYS.YEAR);
            const savedSeason = localStorage.getItem(this.STORAGE_KEYS.SEASON);
            const savedDay = localStorage.getItem(this.STORAGE_KEYS.FILTER_DAY);

            // 防呆：如果存的年份已不存在，回退到預設
            this.year = (savedYear && this.availableData[savedYear]) ? savedYear : window.SERVER_DATA.defaultYear;
            this.filterDay = savedDay || '全部';
            
            // C. 建構季節選單
            this.updateSeasonOptions(savedSeason || window.SERVER_DATA.defaultSeason);

            // D. 監聽捲動 (回到頂部 & 記憶位置)
            window.addEventListener('scroll', () => {
                this.showBackToTop = window.scrollY > 300;
                // Debounce 儲存位置
                clearTimeout(this._scrollTimeout);
                this._scrollTimeout = setTimeout(() => {
                    localStorage.setItem(this.STORAGE_KEYS.SCROLL, window.scrollY);
                }, 200);
            });
            
            // 監聽頁面隱藏 (切換 App 時強制存檔)
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState === 'hidden') {
                    localStorage.setItem(this.STORAGE_KEYS.SCROLL, window.scrollY);
                }
            });

            // E. 首次載入資料
            this.loadData(true); 
        },

        // --- 6. 核心邏輯 ---
        updateSeasonOptions(targetSeason = null) {
            this.seasons = this.availableData[this.year] || [];
            
            // 智慧選擇季節
            if (targetSeason && this.seasons.includes(targetSeason)) {
                this.season = targetSeason;
            } else if (this.seasons.length > 0) {
                this.season = this.seasons[0];
            } else {
                this.season = '';
            }
        },

        async loadData(isFirstLoad = false) {
            if (!this.year || !this.season) return;

            // 記憶當前選擇
            localStorage.setItem(this.STORAGE_KEYS.YEAR, this.year);
            localStorage.setItem(this.STORAGE_KEYS.SEASON, this.season);

            const cacheKey = `${this.year}_${this.season}`;
            this.loading = true;
            this.rawAnimeList = []; // 切換時先清空，避免顯示舊資料

            try {
                // 優先讀取快取
                if (this.dataCache[cacheKey]) {
                    this.rawAnimeList = this.dataCache[cacheKey];
                    console.log(`[Cache Hit] ${cacheKey}`);
                } else {
                    console.log(`[Fetch] ${cacheKey}`);
                    const res = await fetch(`data/${cacheKey}.json?t=${new Date().getTime()}`);
                    if (!res.ok) throw new Error('Data load failed');
                    const data = await res.json();
                    
                    this.rawAnimeList = data.anime_list || [];
                    this.dataCache[cacheKey] = this.rawAnimeList; // 寫入快取

                    if (data.generated_at) {
                        const d = new Date(data.generated_at);
                        this.lastUpdateTime = `更新於 ${d.getFullYear()}/${d.getMonth()+1}/${d.getDate()} ${d.getHours()}:${d.getMinutes()}`;
                    }
                }
            } catch (err) {
                console.error(err);
                Swal.fire({icon: 'error', title: '載入失敗', text: '無法取得該季度資料', background: '#1e1e1e', color: '#fff'});
            } finally {
                this.loading = false;
                
                // 首次載入時恢復捲動位置
                if (isFirstLoad) {
                    // $nextTick 確保 DOM 渲染完畢後才捲動
                    this.$nextTick(() => {
                        const savedPos = localStorage.getItem(this.STORAGE_KEYS.SCROLL);
                        if (savedPos && parseInt(savedPos) > 0) {
                            setTimeout(() => window.scrollTo({top: parseInt(savedPos), behavior: 'auto'}), 100);
                        }
                    });
                }
            }
        },

        // --- 7. 資料篩選 (Computed Logic) ---
        // Alpine 的 x-for 會自動監聽這個 getter 的變化
        get filteredAnime() {
            let list = this.rawAnimeList;
            
            // 每次篩選變動時，順便存檔
            localStorage.setItem(this.STORAGE_KEYS.FILTER_DAY, this.filterDay);

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

        // --- 8. 互動功能 ---
        copyText(text) {
            navigator.clipboard.writeText(text).then(() => {
                Swal.fire({
                    toast: true, position: 'top-end', icon: 'success', 
                    title: '已複製', showConfirmButton: false, timer: 1000, 
                    background: '#2b2b2b', color: '#fff'
                });
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
            
            // 自動捲動分享清單到底部
            this.$nextTick(() => {
                const container = document.getElementById('shareListContainer');
                if(container) container.scrollTop = container.scrollHeight;
            });
            
            Swal.fire({toast: true, position: 'top', icon: 'success', title: '已加入', timer: 1000, showConfirmButton: false, background: '#2b2b2b', color: '#fff'});
        },

        removeFromShare(index) {
            this.shareList.splice(index, 1);
        },

        async generateShareImage() {
            const btn = document.getElementById('copyButton');
            const originalText = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 處理中...';

            try {
                // 預載所有圖片，避免截圖空白
                await Promise.all(this.shareList.map(item => {
                    return new Promise(resolve => {
                        const img = new Image();
                        img.crossOrigin = "anonymous";
                        img.src = item.img;
                        img.onload = resolve;
                        img.onerror = resolve;
                    });
                }));

                const canvas = await html2canvas(document.getElementById('shareListContainer'), {
                    scale: 2, useCORS: true, backgroundColor: '#ffffff'
                });

                canvas.toBlob(blob => {
                    navigator.clipboard.write([new ClipboardItem({'image/png': blob})])
                        .then(() => {
                            Swal.fire({icon: 'success', title: '圖片已複製！', background: '#1e1e1e', color: '#fff'});
                            this.shareList = []; // 清空
                        })
                        .catch(() => Swal.fire({icon: 'error', title: '複製失敗', background: '#1e1e1e', color: '#fff'}));
                });
            } catch (e) {
                console.error(e);
                Swal.fire({icon: 'error', title: '生成失敗', background: '#1e1e1e', color: '#fff'});
            } finally {
                btn.disabled = false;
                btn.innerHTML = originalText;
            }
        },

        scrollToTop() {
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }
    };
}