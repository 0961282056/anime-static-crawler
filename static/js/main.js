$(document).ready(function () {
    // --- 變數初始化 ---
    const availableData = window.AVAILABLE_DATA || {};
    const defaultYear = window.DEFAULT_YEAR;
    const defaultSeason = window.DEFAULT_SEASON;
    
    const animeContainer = $('#anime-results-container');
    const resultCountSpan = $('#result-count');
    const statusMessage = $('#status-message');
    const updateTime = $('#updateTime');
    const $yearSelect = $('#year');
    const $seasonSelect = $('#season');
    
    const dataCache = {};
    let currentAnimeList = [];
    let shareList = [];

    // --- 【新增】捲動記憶相關變數 ---
    const SCROLL_KEY = 'anime_scroll_position'; // 儲存的 Key
    let isFirstLoad = true; // 標記是否為網頁剛打開的第一次載入

    // --- 1. 初始化 Select2 ---
    $("select").select2({
        width: '100%',
        minimumResultsForSearch: Infinity
    });

    // --- 2. 核心邏輯：動態選單與資料載入 ---
    function initSelectors() {
        // 填充年份
        $yearSelect.empty();
        const years = Object.keys(availableData).sort((a, b) => b - a);
        years.forEach(y => {
            $yearSelect.append(new Option(`${y} 年`, y));
        });

        // 設定預設年份
        if (years.includes(defaultYear)) {
            $yearSelect.val(defaultYear);
        } else if (years.length > 0) {
            $yearSelect.val(years[0]);
        }

        // 更新季節並載入
        updateSeasonOptions(defaultSeason); 
    }

    function updateSeasonOptions(targetSeason) {
        const year = $yearSelect.val();
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
        loadData();
    }

    // --- 3. 載入資料 ---
    async function loadData() {
        const year = $yearSelect.val();
        const season = $seasonSelect.val();

        if (!year || !season) return;

        const cacheKey = `${year}_${season}`;
        
        // UI 狀態
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

        } catch (error) {
            console.error(error);
            statusMessage.html(`❌ 無法載入資料 (${year} ${season})`);
        }
    }

    // --- 4. 渲染邏輯 ---
    function renderAnime(list) {
        const day = $('#premiere_date').val();
        let filtered = list;
        if (day !== '全部') {
            filtered = list.filter(item => item.premiere_date === day);
        }

        const keyword = $('#searchInput').val().toLowerCase().trim();
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

        // --- 【新增】渲染完成後，執行捲動恢復 ---
        if (isFirstLoad) {
            restoreScrollPosition();
            isFirstLoad = false; // 只有第一次載入需要恢復，之後的切換不需要
        }
    }

    // --- 【新增】捲動位置管理函式 ---
    
    // 1. 恢復位置
    function restoreScrollPosition() {
        const savedPos = localStorage.getItem(SCROLL_KEY);
        // 如果有儲存的位置，且位置大於 0
        if (savedPos && parseInt(savedPos) > 0) {
            // 使用 setTimeout 確保 DOM 已經完全長出來後再捲動
            setTimeout(() => {
                window.scrollTo({
                    top: parseInt(savedPos),
                    behavior: 'auto' // 使用 auto 瞬間跳轉，避免 smooth 滾動的視覺干擾
                });
                console.log('已恢復上次瀏覽位置:', savedPos);
            }, 100); // 100ms 延遲確保圖片佔位符已渲染
        }
    }

    // 2. 儲存位置 (使用 Debounce 防抖動，避免滑動時頻繁寫入)
    let scrollTimeout;
    $(window).on('scroll', function() {
        clearTimeout(scrollTimeout);
        scrollTimeout = setTimeout(() => {
            const currentPos = $(window).scrollTop();
            localStorage.setItem(SCROLL_KEY, currentPos);
        }, 200); // 停止滑動 200ms 後才儲存
        
        // 原有的回到頂部按鈕邏輯
        if ($(this).scrollTop() > 300) $('#backToTopBtn').addClass('show');
        else $('#backToTopBtn').removeClass('show');
    });

    // --- 事件綁定 ---
    
    $yearSelect.on('change', function() { updateSeasonOptions(); });
    $seasonSelect.on('change', loadData);
    $('#premiere_date').on('change', () => renderAnime(currentAnimeList));
    
    let timer;
    $('#searchInput').on('input', () => {
        clearTimeout(timer);
        timer = setTimeout(() => renderAnime(currentAnimeList), 300);
    });

    // --- 互動功能 ---

    // 複製片名
    $(document).on('click', '.anime-title', function() {
        const text = $(this).text();
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
        const text = $(this).text();
        const title = $(this).siblings('.anime-title').text();
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

    // 渲染分享清單
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

    // 移除清單項目
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

    // 回到頂部按鈕點擊
    $('#backToTopBtn').click(function() {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    });

    // 啟動
    initSelectors();
});