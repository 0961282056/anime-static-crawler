$(document).ready(function () {
    // --- 變數初始化 ---
    const availableData = window.AVAILABLE_DATA || {};
    const serverDefaultYear = window.DEFAULT_YEAR;
    const serverDefaultSeason = window.DEFAULT_SEASON;
    
    const animeContainer = $('#anime-results-container');
    const resultCountSpan = $('#result-count');
    const statusMessage = $('#status-message');
    const updateTime = $('#updateTime');
    const $yearSelect = $('#year');
    const $seasonSelect = $('#season');
    const $premiereSelect = $('#premiere_date');
    const $searchInput = $('#searchInput');
    
    const dataCache = {};
    let currentAnimeList = [];
    let shareList = [];

    // --- 【狀態記憶 Key】 ---
    const STORAGE_KEYS = {
        YEAR: 'anime_user_year',
        SEASON: 'anime_user_season',
        FILTER_DAY: 'anime_user_filter_day',
        SCROLL: 'anime_user_scroll_pos'
    };

    let isFirstLoad = true; // 用於判斷是否需要恢復捲動

    // --- 1. 初始化 Select2 ---
    $("select").select2({
        width: '100%',
        minimumResultsForSearch: Infinity
    });

    // --- 2. 核心邏輯：初始化與狀態恢復 ---
    function initApp() {
        // A. 讀取 LocalStorage
        let targetYear = localStorage.getItem(STORAGE_KEYS.YEAR) || serverDefaultYear;
        let targetSeason = localStorage.getItem(STORAGE_KEYS.SEASON) || serverDefaultSeason;
        let targetDay = localStorage.getItem(STORAGE_KEYS.FILTER_DAY) || '全部';

        // 防呆
        if (!availableData[targetYear]) {
            targetYear = serverDefaultYear;
            targetSeason = serverDefaultSeason;
        }

        // B. 建構年份
        $yearSelect.empty();
        const years = Object.keys(availableData).sort((a, b) => b - a);
        years.forEach(y => {
            $yearSelect.append(new Option(`${y} 年`, y));
        });
        $yearSelect.val(targetYear).trigger('change.select2');

        // C. 建構季節
        updateSeasonOptions(targetYear, targetSeason);

        // D. 恢復篩選
        $premiereSelect.val(targetDay).trigger('change.select2');

        // E. 載入資料
        loadData(targetYear, targetSeason);
    }

    function updateSeasonOptions(year, targetSeason) {
        const seasons = availableData[year] || [];
        $seasonSelect.empty();
        
        if (seasons.length === 0) {
            $seasonSelect.append(new Option('無資料', ''));
        } else {
            seasons.forEach(s => {
                $seasonSelect.append(new Option(`${s} 番`, s));
            });

            if (targetSeason && seasons.includes(targetSeason)) {
                $seasonSelect.val(targetSeason);
            } else {
                $seasonSelect.val(seasons[0]);
            }
        }
        $seasonSelect.trigger('change.select2');
    }

    // --- 3. 載入資料 ---
    async function loadData(year, season) {
        year = year || $yearSelect.val();
        season = season || $seasonSelect.val();

        if (!year || !season) return;

        // 更新記憶
        localStorage.setItem(STORAGE_KEYS.YEAR, year);
        localStorage.setItem(STORAGE_KEYS.SEASON, season);

        const cacheKey = `${year}_${season}`;
        
        animeContainer.empty();
        statusMessage.removeClass('d-none').html('<i class="fas fa-spinner fa-spin"></i> 資料讀取中...');
        resultCountSpan.text('0');

        try {
            if (dataCache[cacheKey]) {
                currentAnimeList = dataCache[cacheKey];
            } else {
                const response = await fetch(`data/${cacheKey}.json?t=${new Date().getTime()}`);
                if (!response.ok) throw new Error('資料載入失敗');
                const data = await response.json();
                
                currentAnimeList = data.anime_list || [];
                dataCache[cacheKey] = currentAnimeList;

                if (data.generated_at) {
                    const d = new Date(data.generated_at);
                    updateTime.text(`更新於 ${d.getFullYear()}/${d.getMonth()+1}/${d.getDate()} ${d.getHours()}:${d.getMinutes()}`);
                }
            }
            
            renderAnime(currentAnimeList);
            statusMessage.addClass('d-none');

            // 【關鍵】首次載入執行恢復
            if (isFirstLoad) {
                restoreScrollPosition();
                isFirstLoad = false;
            }

        } catch (error) {
            console.error(error);
            statusMessage.html(`❌ 無法載入資料 (${year} ${season})`);
        }
    }

    // --- 4. 渲染邏輯 ---
    function renderAnime(list) {
        const day = $premiereSelect.val();
        const keyword = $searchInput.val().toLowerCase().trim();

        let filtered = list;

        if (day !== '全部') {
            filtered = list.filter(item => item.premiere_date === day);
        }

        if (keyword) {
            filtered = filtered.filter(item => 
                (item.anime_name && item.anime_name.toLowerCase().includes(keyword)) ||
                (item.story && item.story.toLowerCase().includes(keyword))
            );
        }

        animeContainer.empty();
        resultCountSpan.text(filtered.length);

        if (filtered.length === 0) {
            statusMessage.removeClass('d-none').text('沒有符合條件的動畫');
            return;
        } else {
            statusMessage.addClass('d-none');
        }

        const html = filtered.map(anime => {
            const img = (anime.anime_image_url && anime.anime_image_url !== '無圖片') ? anime.anime_image_url : 'https://placehold.co/300x450/333/999?text=No+Image';
            const story = anime.story || '暫無簡介';
            
            return `
            <div class="anime-card">
                <div class="card-img-wrapper">
                    <img src="${img}" class="card-img" loading="lazy">
                </div>
                <div class="card-body">
                    <h3 class="anime-title" data-name="${anime.anime_name}">${anime.anime_name}</h3>
                    <div class="info-row">
                        <span><i class="fas fa-calendar-alt"></i> ${anime.premiere_date || '?'}</span>
                        <span><i class="fas fa-clock"></i> ${anime.premiere_time || '?'}</span>
                    </div>
                    <div class="story-box" title="點擊查看詳情">${story}</div>
                    <button class="btn-add add-share" 
                        data-name="${anime.anime_name}"
                        data-img="${img}"
                        data-date="${anime.premiere_date}"
                        data-time="${anime.premiere_time}">
                        <i class="fas fa-plus"></i> 加入清單
                    </button>
                </div>
            </div>
            `;
        }).join('');

        animeContainer.html(html);
    }

    // --- 【優化】捲動位置管理 ---
    function restoreScrollPosition() {
        const savedPos = localStorage.getItem(STORAGE_KEYS.SCROLL);
        if (savedPos && parseInt(savedPos) > 0) {
            const pos = parseInt(savedPos);
            console.log("嘗試恢復位置:", pos);

            // 1. 立即跳轉 (Instant)
            window.scrollTo({ top: pos, behavior: 'instant' });

            // 2. 延遲校正 (等待圖片區塊佔位完成)
            setTimeout(() => {
                // 檢查是否偏離，如果偏離則修正 (例如圖片載入後高度變了)
                if (Math.abs(window.scrollY - pos) > 10) {
                    window.scrollTo({ top: pos, behavior: 'auto' });
                }
            }, 150);

            // 3. 二次校正 (針對較慢的手機或瀏覽器)
            setTimeout(() => {
                if (Math.abs(window.scrollY - pos) > 10) {
                    window.scrollTo({ top: pos, behavior: 'auto' });
                }
            }, 400);
        }
    }

    // 捲動監聽
    let scrollTimeout;
    $(window).on('scroll', function() {
        // 回到頂部按鈕顯示
        if ($(this).scrollTop() > 300) $('#backToTopBtn').addClass('show');
        else $('#backToTopBtn').removeClass('show');

        // 平常滑動時 Debounce 存檔 (降低效能消耗)
        clearTimeout(scrollTimeout);
        scrollTimeout = setTimeout(() => {
            localStorage.setItem(STORAGE_KEYS.SCROLL, $(window).scrollTop());
        }, 200);
    });

    // 【關鍵優化】當切換 App / 關閉分頁 / 鎖定螢幕時，立刻強制存檔 (無延遲)
    // 這能解決「砍後台」時來不及存檔導致位置偏上的問題
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') {
            localStorage.setItem(STORAGE_KEYS.SCROLL, $(window).scrollTop());
        }
    });

    // --- 事件綁定 ---
    
    $yearSelect.on('change', function() { 
        const year = $(this).val();
        updateSeasonOptions(year, null); 
        loadData();
    });

    $seasonSelect.on('change', function() { loadData(); });

    $premiereSelect.on('change', function() {
        localStorage.setItem(STORAGE_KEYS.FILTER_DAY, $(this).val());
        renderAnime(currentAnimeList);
    });
    
    let searchTimer;
    $searchInput.on('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => renderAnime(currentAnimeList), 300);
    });

    // --- 互動功能 ---

    $(document).on('click', '.anime-title', function() {
        const text = $(this).text().trim();
        navigator.clipboard.writeText(text).then(() => {
            Swal.fire({
                toast: true, position: 'top-end', icon: 'success', 
                title: '已複製片名', showConfirmButton: false, timer: 1500,
                background: '#2b2b2b', color: '#fff'
            });
        });
    });

    $(document).on('click', '.story-box', function() {
        const text = $(this).text().trim();
        const title = $(this).siblings('.anime-title').text().trim();
        Swal.fire({
            title: title,
            text: text,
            background: '#1e1e1e', color: '#e0e0e0',
            confirmButtonColor: '#bb86fc'
        });
    });

    $(document).on('click', '.add-share', function() {
        const data = $(this).data();
        if (shareList.some(i => i.name === data.name)) {
            Swal.fire({toast: true, position: 'top', icon: 'warning', title: '已在清單中', timer: 1000, showConfirmButton: false, background: '#2b2b2b', color:'#fff'});
            return;
        }
        shareList.push(data);
        renderShareList();
        Swal.fire({toast: true, position: 'top', icon: 'success', title: '已加入', timer: 1000, showConfirmButton: false, background: '#2b2b2b', color:'#fff'});
    });

    function renderShareList() {
        const $con = $('#shareList').empty();
        if (shareList.length === 0) {
            $con.html('<div class="empty-state" style="color:#888; text-align:center; padding:20px;">尚無內容</div>');
            $('#copyButton').prop('disabled', true);
            $('#shareListContainer').scrollTop(0);
            return;
        }

        $('#copyButton').prop('disabled', false);
        
        shareList.forEach((item, idx) => {
            $con.append(`
                <div class="share-item">
                    <img src="${item.img}">
                    <div class="share-item-info">
                        <div class="share-item-title">${item.name}</div>
                        <div>${item.date} ${item.time}</div>
                    </div>
                    <div class="share-remove" data-idx="${idx}" title="移除"><i class="fas fa-trash-alt"></i></div>
                </div>
            `);
        });
        
        const container = document.getElementById('shareListContainer');
        container.scrollTop = container.scrollHeight;
    }

    $(document).on('click', '.share-remove', function() {
        const idx = $(this).data('idx');
        shareList.splice(idx, 1);
        renderShareList();
    });

    $('#copyButton').click(async function() {
        const btn = $(this);
        btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin"></i> 處理中...');
        
        try {
            await Promise.all(shareList.map(item => {
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
                        Swal.fire({icon: 'success', title: '圖片已複製！', background: '#1e1e1e', color:'#fff'});
                        shareList = [];
                        renderShareList();
                    })
                    .catch(() => Swal.fire({icon: 'error', title: '複製失敗', text: '請手動下載圖片', background: '#1e1e1e', color:'#fff'}));
            });
        } catch (e) {
            console.error(e);
            Swal.fire({icon: 'error', title: '生成失敗', background: '#1e1e1e', color:'#fff'});
        } finally {
            btn.prop('disabled', false).html('<i class="fas fa-image"></i> 生成圖片');
        }
    });

    // 回到頂部按鈕
    $('#backToTopBtn').click(function() {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    });

    // 啟動
    initApp();
});