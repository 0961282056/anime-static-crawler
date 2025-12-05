$(document).ready(function () {
    // --- 變數初始化 ---
    const availableData = window.AVAILABLE_DATA || {};
    // 後端傳來的預設值 (作為備案)
    const serverDefaultYear = window.DEFAULT_YEAR;
    const serverDefaultSeason = window.DEFAULT_SEASON;
    
    const animeContainer = $('#anime-results-container');
    const resultCountSpan = $('#result-count');
    const statusMessage = $('#status-message');
    const updateTime = $('#updateTime');
    const $yearSelect = $('#year');
    const $seasonSelect = $('#season');
    const $premiereSelect = $('#premiere_date'); // 星期篩選
    const $searchInput = $('#searchInput');
    
    const dataCache = {};
    let currentAnimeList = [];
    let shareList = [];

    // 【關鍵修正】定義所有需要記憶的 Key
    const STORAGE_KEYS = {
        YEAR: 'anime_user_year',
        SEASON: 'anime_user_season',
        FILTER_DAY: 'anime_user_filter_day', // 星期幾
        SCROLL: 'anime_user_scroll_pos'
    };

    let isFirstLoad = true; // 標記是否為首次載入，用於判斷是否恢復捲動

    // --- 1. 初始化 Select2 ---
    $("select").select2({
        width: '100%',
        minimumResultsForSearch: Infinity
    });

    // --- 2. 核心邏輯：初始化與狀態恢復 (取代原本的 initSelectors) ---
    function initApp() {
        // A. 嘗試從 localStorage 讀取上次的狀態，若無則使用後端預設值
        let targetYear = localStorage.getItem(STORAGE_KEYS.YEAR) || serverDefaultYear;
        let targetSeason = localStorage.getItem(STORAGE_KEYS.SEASON) || serverDefaultSeason;
        let targetDay = localStorage.getItem(STORAGE_KEYS.FILTER_DAY) || '全部';

        // 防呆檢查：如果記憶的年份在現有資料中不存在 (例如資料庫更新了)，則回退到預設值
        if (!availableData[targetYear]) {
            targetYear = serverDefaultYear;
            targetSeason = serverDefaultSeason;
        }

        // B. 建構年份選單
        $yearSelect.empty();
        const years = Object.keys(availableData).sort((a, b) => b - a);
        years.forEach(y => {
            $yearSelect.append(new Option(`${y} 年`, y));
        });
        
        // 設定選中年份 (觸發 Select2 更新)
        $yearSelect.val(targetYear).trigger('change.select2');

        // C. 建構季節選單 (傳入目標季節，確保選單內容正確)
        updateSeasonOptions(targetYear, targetSeason);

        // D. 恢復「星期篩選」的狀態
        $premiereSelect.val(targetDay).trigger('change.select2');

        // E. 開始載入資料 (這會觸發 renderAnime，進而觸發捲動恢復)
        loadData(targetYear, targetSeason);
    }

    // 更新季節選單
    function updateSeasonOptions(year, targetSeason) {
        const seasons = availableData[year] || [];
        $seasonSelect.empty();
        
        if (seasons.length === 0) {
            $seasonSelect.append(new Option('無資料', ''));
        } else {
            seasons.forEach(s => {
                $seasonSelect.append(new Option(`${s} 番`, s));
            });

            // 嘗試選中目標季節，若無則選第一個
            if (targetSeason && seasons.includes(targetSeason)) {
                $seasonSelect.val(targetSeason);
            } else {
                $seasonSelect.val(seasons[0]);
            }
        }
        $seasonSelect.trigger('change.select2');
    }

    // --- 3. 載入資料 (AJAX + Cache + 狀態寫入) ---
    async function loadData(year, season) {
        // 如果沒傳參數，就抓當前 UI 的值
        year = year || $yearSelect.val();
        season = season || $seasonSelect.val();

        if (!year || !season) return;

        // 【狀態記憶】每次載入新資料時，立即更新 localStorage
        localStorage.setItem(STORAGE_KEYS.YEAR, year);
        localStorage.setItem(STORAGE_KEYS.SEASON, season);

        const cacheKey = `${year}_${season}`;
        
        // UI 狀態
        animeContainer.empty();
        statusMessage.removeClass('d-none').html('<i class="fas fa-spinner fa-spin"></i> 資料讀取中...');
        resultCountSpan.text('0');

        try {
            if (dataCache[cacheKey]) {
                console.log(`[Cache Hit] ${cacheKey}`);
                currentAnimeList = dataCache[cacheKey];
            } else {
                console.log(`[Fetch] ${cacheKey}`);
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
            
            // 資料載入完成，進行渲染
            renderAnime(currentAnimeList);
            statusMessage.addClass('d-none');

            // 【捲動恢復關鍵】只有在網頁「首次載入」且資料渲染完畢後，才執行捲動恢復
            if (isFirstLoad) {
                restoreScrollPosition();
                isFirstLoad = false; // 標記已完成，之後的使用者切換不需要恢復捲動
            }

        } catch (error) {
            console.error(error);
            statusMessage.html(`❌ 無法載入資料 (${year} ${season})`);
        }
    }

    // --- 4. 渲染邏輯 ---
    function renderAnime(list) {
        const day = $premiereSelect.val(); // 讀取當前選中的星期
        const keyword = $searchInput.val().toLowerCase().trim();

        let filtered = list;

        // 篩選：星期
        if (day !== '全部') {
            filtered = list.filter(item => item.premiere_date === day);
        }

        // 篩選：關鍵字
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

    // --- 捲動位置管理 ---
    function restoreScrollPosition() {
        const savedPos = localStorage.getItem(STORAGE_KEYS.SCROLL);
        if (savedPos && parseInt(savedPos) > 0) {
            // 延遲執行確保 DOM 已經長好
            setTimeout(() => {
                window.scrollTo({
                    top: parseInt(savedPos),
                    behavior: 'auto' // 使用 auto 瞬間跳轉，避免 smooth 滾動的暈眩感
                });
                console.log("已恢復上次瀏覽位置");
            }, 150); 
        }
    }

    let scrollTimeout;
    $(window).on('scroll', function() {
        // 顯示回到頂部按鈕
        if ($(this).scrollTop() > 300) $('#backToTopBtn').addClass('show');
        else $('#backToTopBtn').removeClass('show');

        // 儲存捲動位置 (使用 Debounce 避免頻繁寫入)
        clearTimeout(scrollTimeout);
        scrollTimeout = setTimeout(() => {
            const currentPos = $(window).scrollTop();
            localStorage.setItem(STORAGE_KEYS.SCROLL, currentPos);
        }, 200);
    });

    // --- 事件綁定 ---
    
    // 年份變更 -> 更新季節選單 (傳入 null 讓其選第一個) -> 載入
    $yearSelect.on('change', function() { 
        const year = $(this).val();
        updateSeasonOptions(year, null); 
        loadData();
    });

    // 季節變更 -> 載入
    $seasonSelect.on('change', function() { loadData(); });

    // 星期變更 -> 【狀態記憶】寫入 Storage -> 重新渲染
    $premiereSelect.on('change', function() {
        localStorage.setItem(STORAGE_KEYS.FILTER_DAY, $(this).val());
        renderAnime(currentAnimeList);
    });
    
    // 搜尋 -> 重新渲染
    let searchTimer;
    $searchInput.on('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => renderAnime(currentAnimeList), 300);
    });

    // --- 互動功能 (複製、分享、長按) ---

    // 複製片名
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

    // 簡介詳情
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

    // 加入清單
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
            // 讓容器變回原始高度
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

    // 生成圖片
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

    // 回到頂部按鈕 (點擊時會清除記憶的位置，讓下次進來從頭開始)
    $('#backToTopBtn').click(function() {
        window.scrollTo({ top: 0, behavior: 'smooth' });
        // 選項：若想回到頂部後清除捲動記憶，可取消註解下行
        localStorage.removeItem(STORAGE_KEYS.SCROLL);
    });

    // --- 🚀 啟動應用程式 (使用新的 initApp) ---
    initApp();
});