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

        // --- 5. 初始化 ---
        initApp() {
            // A. 初始化年份選單
            this.years = Object.keys(this.availableData).sort((a, b) => b - a);
            const savedDay = localStorage.getItem(this.STORAGE_KEYS.FILTER_DAY);
            this.filterDay = savedDay || '全部';

            // --- 🟢 第一層 $nextTick: 等待年份選單就緒 ---
            this.$nextTick(() => {
                // 1. 嘗試讀取使用者上次的選擇
                const savedYear = localStorage.getItem(this.STORAGE_KEYS.YEAR);
                const savedSeason = localStorage.getItem(this.STORAGE_KEYS.SEASON);
                
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
                let targetYear = window.SERVER_DATA.defaultYear;
                let targetSeason = window.SERVER_DATA.defaultSeason;
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
                    localStorage.setItem(this.STORAGE_KEYS.SCROLL, window.scrollY);
                }, 200);
            });
            
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState === 'hidden') {
                    localStorage.setItem(this.STORAGE_KEYS.SCROLL, window.scrollY);
                }
            });
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

        // 🟢 修改：增加 shouldRestoreScroll 參數
        async loadData(shouldRestoreScroll = false) {
            if (!this.year || !this.season) return;

            // 每次載入都記住當前選擇 (為了下次開啟使用)
            localStorage.setItem(this.STORAGE_KEYS.YEAR, this.year);
            localStorage.setItem(this.STORAGE_KEYS.SEASON, this.season);

            const cacheKey = `${this.year}_${this.season}`;
            this.loading = true;
            this.rawAnimeList = []; 

            try {
                if (this.dataCache[cacheKey]) {
                    this.rawAnimeList = this.dataCache[cacheKey];
                    console.log(`[Cache Hit] ${cacheKey}`);
                } else {
                    console.log(`[Fetch] ${cacheKey}`);
                    const res = await fetch(`data/${cacheKey}.json?t=${new Date().getTime()}`);
                    if (!res.ok) throw new Error('Data load failed');
                    const data = await res.json();
                    
                    this.rawAnimeList = data.anime_list || [];
                    this.dataCache[cacheKey] = this.rawAnimeList;

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
                
                // 🟢 關鍵修改：只有當 "shouldRestoreScroll" 為 true 時才恢復位置
                // 否則強制滾回頂部 (體驗更好)
                this.$nextTick(() => {
                    if (shouldRestoreScroll) {
                        const savedPos = localStorage.getItem(this.STORAGE_KEYS.SCROLL);
                        if (savedPos && parseInt(savedPos) > 0) {
                            setTimeout(() => window.scrollTo({top: parseInt(savedPos), behavior: 'auto'}), 100);
                            console.log("📜 恢復上次瀏覽位置");
                        }
                    } else {
                        window.scrollTo({top: 0, behavior: 'auto'});
                        console.log("🆕 新的開始，回到頂部");
                    }
                });
            }
        },

        // --- 7. 資料篩選 (不變) ---
        get filteredAnime() {
            let list = this.rawAnimeList;
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

        // --- 8. 互動功能 (不變) ---
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
            this.$nextTick(() => {
                const container = document.getElementById('shareListContainer');
                if(container) container.scrollTop = container.scrollHeight;
            });
            Swal.fire({toast: true, position: 'top', icon: 'success', title: '已加入', timer: 1000, showConfirmButton: false, background: '#2b2b2b', color: '#fff'});
        },

        removeFromShare(index) {
            this.shareList.splice(index, 1);
        },

       // --- 修改後的 generateShareImage (自動切換最高畫質片源) ---
        async generateShareImage() {
            const btn = document.getElementById('copyButton');
            const originalText = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 載入最高畫質圖...'; 

            // 1. 建立隱藏容器
            const exportContainer = document.createElement('div');
            exportContainer.style.cssText = `
                position: fixed; top: 0; left: -9999px; z-index: -1;
                width: 600px;
                background-color: #1a1a1a;
                padding: 40px;
                display: flex; flex-direction: column; gap: 40px;
                font-family: 'Noto Sans TC', sans-serif;
            `;

            // 2. 填入資料
            this.shareList.forEach(item => {
                
                // 🔥【關鍵修改】處理圖片網址，請求最高畫質版本
                let highResImgUrl = item.img;
                
                // 檢查是否為 Cloudinary 的圖片，並且 URL 結構包含轉換參數
                if (highResImgUrl.includes('cloudinary.com') && highResImgUrl.includes('/upload/')) {
                    // 正則表達式: 尋找 /upload/ 和 /v1/ 之間的轉換參數
                    // 並將其替換為 q_auto:best (最高品質) 和 f_auto (最佳格式)
                    // 這樣 Cloudinary 會根據原圖輸出清晰度最佳的版本
                    highResImgUrl = highResImgUrl.replace(
                        // 尋找 /upload/ 後面一串非 / 的字符 (即轉換參數) 直到 /v1/
                        /\/upload\/[^/]+\/v1\//, 
                        // 替換為要求最高品質的轉換參數
                        '/upload/q_auto:best,f_auto/v1/'
                    );
                    
                    console.log(`[HD URL] ${highResImgUrl}`);
                }

                const cardHtml = `
                    <div style="
                        width: 100%;
                        display: flex;
                        flex-direction: column;
                        background-color: #2b2b2b;
                        border-radius: 24px;
                        overflow: hidden;
                        box-shadow: 0 20px 50px rgba(0,0,0,0.5);
                    ">
                        <div style="width: 100%; line-height: 0;">
                            <img src="${highResImgUrl}" style="
                                width: 100%;
                                height: auto;
                                display: block;
                            " crossorigin="anonymous">
                        </div>

                        <div style="
                            padding: 30px 35px;
                            background-color: #252525;
                            border-top: 1px solid #333;
                            display: flex;
                            flex-direction: column;
                            justify-content: center; 
                        ">
                            <h2 style="
                                margin: 0;
                                font-size: 42px;
                                font-weight: 700;
                                color: #ffffff;
                                line-height: 1.4;
                                min-height: 1.4em; 
                            ">${item.name}</h2>
                        </div>
                    </div>
                `;
                exportContainer.insertAdjacentHTML('beforeend', cardHtml);
            });

            document.body.appendChild(exportContainer);

            try {
                // 3. 等待圖片載入
                const images = Array.from(exportContainer.querySelectorAll('img'));
                await Promise.all(images.map(img => {
                    if (img.complete) return Promise.resolve();
                    return new Promise(resolve => {
                        img.onload = resolve;
                        img.onerror = resolve; 
                    });
                }));

                // 4. 截圖 (維持 scale: 3 以獲得最佳字體清晰度)
                const canvas = await html2canvas(exportContainer, {
                    scale: 3, 
                    useCORS: true, 
                    allowTaint: true,
                    backgroundColor: '#1a1a1a', 
                    logging: false,
                    letterRendering: 1, 
                });

                // 5. 輸出與清空
                canvas.toBlob(blob => {
                    if (!blob) throw new Error('Canvas is empty');
                    navigator.clipboard.write([new ClipboardItem({'image/png': blob})])
                        .then(() => {
                            Swal.fire({
                                icon: 'success', 
                                title: '圖片已複製！', 
                                text: '清單已自動清空', 
                                background: '#1e1e1e', color: '#fff',
                                timer: 2000, showConfirmButton: false
                            });
                            this.shareList = []; 
                        })
                        .catch(err => {
                            console.error('Clipboard Error:', err);
                            Swal.fire({icon: 'error', title: '複製失敗', text: '請手動下載', background: '#1e1e1e', color: '#fff'});
                        });
                });

            } catch (e) {
                console.error(e);
                Swal.fire({icon: 'error', title: '生成失敗', background: '#1e1e1e', color: '#fff'});
            } finally {
                if (document.body.contains(exportContainer)) {
                    document.body.removeChild(exportContainer);
                }
                btn.disabled = false;
                btn.innerHTML = originalText;
            }
        },

        scrollToTop() {
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }
    };
}