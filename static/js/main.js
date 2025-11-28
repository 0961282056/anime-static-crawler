// static/js/main.js
// 依賴：jQuery, Select2, SweetAlert2, html2canvas

$(document).ready(function () {
    // --- 【變數定義】 ---
    let currentAnimeList = []; // 儲存當前季度載入的所有動畫資料
    const animeContainer = $('#anime-results-container');
    const resultCountSpan = $('#result-count');
    const searchForm = $('#search-form'); // ⭐️ 優化：使用 ID 選擇器
    const shareListContainer = $('#shareList');
    const clearShareListBtn = $('#clearShareListBtn');
    const exportImageBtn = $('#exportImageBtn');
    const backToTopBtn = $('#backToTopBtn');
    let shareList = loadShareList(); // 載入本地儲存的分享清單

    // --- 【核心功能函數】 ---

    /**
     * 顯示 SweetAlert2 提示
     */
    function showAlert(title, text, icon, timer = 1500) {
        Swal.fire({
            title: title,
            text: text,
            icon: icon,
            timer: timer,
            showConfirmButton: false
        });
    }

    /**
     * 格式化單個動畫資料並生成 HTML 卡片
     */
    function createAnimeCard(anime) {
        const isAdded = shareList.some(item => item.anime_name === anime.anime_name);
        const btnClass = isAdded ? 'btn-danger remove-btn' : 'btn-primary add-btn';
        const btnText = isAdded ? '<i class="fas fa-minus-circle"></i> 移除清單' : '<i class="fas fa-plus-circle"></i> 加入清單';

        return `
            <div class="col">
                <div class="card h-100 anime-card">
                    <img src="${anime.anime_image_url || 'placeholder.jpg'}" 
                         class="card-img-top" 
                         alt="${anime.anime_name}" 
                         loading="lazy">
                    <div class="card-body d-flex flex-column">
                        <h3 class="card-title anime-title" data-anime-name="${anime.anime_name}">${anime.anime_name}</h3>
                        <div class="card-text d-flex flex-column flex-grow-1">
                            <div class="info-item">
                                <i class="fas fa-calendar-alt"></i> <strong>首播：</strong> ${anime.premiere_date} ${anime.premiere_time}
                            </div>
                            <div class="info-item">
                                <i class="fas fa-tag"></i> <strong>分類：</strong> ${anime.genre || '未分類'}
                            </div>
                            <p class="story-summary mt-2 flex-grow-1" title="${anime.story}">
                                <strong>故事概要：</strong> ${anime.story}
                            </p>
                        </div>
                        <div class="mt-3 text-center">
                            <button class="btn ${btnClass} w-100" 
                                    data-anime-name="${anime.anime_name}">
                                ${btnText}
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    /**
     * 渲染動畫列表到頁面
     */
    function renderAnimeList(list) {
        animeContainer.empty();
        resultCountSpan.text(list.length);
        if (list.length === 0) {
            animeContainer.html('<div class="col-12 text-center text-muted">本季或篩選條件下沒有找到動畫資料。</div>');
            return;
        }

        const html = list.map(createAnimeCard).join('');
        animeContainer.html(html);
    }

    /**
     * 載入指定年/季的 JSON 資料
     */
    async function loadData(year, season) {
        const jsonUrl = `static/data/${year}_${season}.json`;

        try {
            const response = await fetch(jsonUrl);
            if (!response.ok) {
                if (response.status === 404) {
                    throw new Error('404: 找不到該季度的資料檔案。');
                }
                throw new Error(`載入失敗，狀態碼: ${response.status}`);
            }
            const data = await response.json();
            currentAnimeList = data.anime_list || [];
            console.log(`成功載入 ${year} 年 ${season} 季共 ${currentAnimeList.length} 筆資料。`);
            return currentAnimeList;

        } catch (error) {
            console.error('載入資料發生錯誤:', error);
            currentAnimeList = [];
            showAlert('載入失敗', '找不到該季度的資料檔案，請重新選擇。', 'error');
            return [];
        }
    }

    /**
     * 根據下拉選單值篩選動畫列表
     */
    function filterAnime() {
        if (currentAnimeList.length === 0) {
            renderAnimeList([]);
            return;
        }

        const year = $('#year').val();
        const season = $('#season').val();
        const weekday = $('#weekday').val();

        let filteredList = currentAnimeList.filter(anime => {
            let match = true;

            // 檢查年份和季度 (主要在 loadData 時已經處理，此處用於二次確認或未來擴展)
            // if (anime.year !== year || anime.season !== season) {
            //     return false; 
            // }

            // 篩選星期幾 (如果不是 '全部')
            if (weekday !== '全部') {
                const animeWeekday = anime.premiere_date.split('（')[1].replace('）', '');
                match = match && (animeWeekday === weekday);
            }

            return match;
        });

        renderAnimeList(filteredList);
    }

    // --- 【分享清單邏輯】 ---

    /**
     * 載入本地儲存的分享清單
     */
    function loadShareList() {
        try {
            const list = localStorage.getItem('animeShareList');
            return list ? JSON.parse(list) : [];
        } catch (e) {
            console.error('載入本地清單失敗:', e);
            return [];
        }
    }

    /**
     * 儲存分享清單到本地
     */
    function saveShareList() {
        localStorage.setItem('animeShareList', JSON.stringify(shareList));
        renderShareList();
    }

    /**
     * 渲染分享清單
     */
    function renderShareList() {
        shareListContainer.empty();
        if (shareList.length === 0) {
            shareListContainer.html('<div class="text-center text-muted p-4">您的分享清單是空的。<br>點擊查詢結果中的「加入清單」來新增動畫。</div>');
            clearShareListBtn.hide();
            exportImageBtn.hide();
            return;
        }

        const html = shareList.map(anime => `
            <div class="d-flex align-items-center mb-2 share-card" data-anime-name="${anime.anime_name}">
                <img src="${anime.anime_image_url || 'placeholder.jpg'}" 
                     alt="${anime.anime_name}" 
                     class="img-thumbnail me-3" 
                     style="width: 50px; height: 50px; object-fit: cover;">
                <div class="flex-grow-1">
                    <strong style="font-size: 1.1rem;">${anime.anime_name}</strong>
                    <div class="text-muted small">${anime.premiere_date} ${anime.premiere_time}</div>
                </div>
                <button class="btn btn-sm btn-danger remove-from-share" data-anime-name="${anime.anime_name}">
                    <i class="fas fa-trash-alt"></i>
                </button>
            </div>
        `).join('');

        shareListContainer.html(html);
        clearShareListBtn.show();
        exportImageBtn.show();
    }

    /**
     * 複製文字到剪貼簿
     */
    async function copyToClipboard(text) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (err) {
            console.error('複製到剪貼簿失敗:', err);
            return false;
        }
    }

    // --- 【事件處理器】 ---

    // 1. 表單提交事件 (解決 405 錯誤的關鍵)
    searchForm.on('submit', async function (e) {
        e.preventDefault(); // ⭐️ 阻止瀏覽器發送傳統的 HTTP 請求 ⭐️
        
        const year = $('#year').val();
        const season = $('#season').val();

        // 顯示載入動畫，然後開始載入數據
        const loadingHtml = '<div class="col-12 text-center text-muted"><i class="fas fa-spinner fa-spin me-2"></i>資料載入中，請稍候...</div>';
        animeContainer.html(loadingHtml);
        resultCountSpan.text('...');
        
        // 載入資料並篩選
        await loadData(year, season);
        filterAnime();
    });

    // 2. 篩選條件變動事件 (Select2)
    $('#year, #season, #weekday').on('change', function () {
        // 如果是年份或季度變動，需要重新載入數據
        if ($(this).attr('id') === 'year' || $(this).attr('id') === 'season') {
            searchForm.trigger('submit'); // 觸發表單提交來重新載入數據
        } else {
            filterAnime(); // 僅在當前數據上進行篩選
        }
    });
    
    // 3. 動畫卡片點擊事件 (加入/移除清單)
    animeContainer.on('click', '.add-btn, .remove-btn', function() {
        const btn = $(this);
        const name = btn.data('anime-name');
        
        if (btn.hasClass('add-btn')) {
            // 加入清單
            const anime = currentAnimeList.find(a => a.anime_name === name);
            if (anime && !shareList.some(item => item.anime_name === name)) {
                shareList.push(anime);
                btn.removeClass('btn-primary add-btn').addClass('btn-danger remove-btn').html('<i class="fas fa-minus-circle"></i> 移除清單');
                showAlert('加入成功', `${name} 已加入分享清單！`, 'success');
            }
        } else {
            // 移除清單
            shareList = shareList.filter(item => item.anime_name !== name);
            btn.removeClass('btn-danger remove-btn').addClass('btn-primary add-btn').html('<i class="fas fa-plus-circle"></i> 加入清單');
            showAlert('已移除', `${name} 已從分享清單移除。`, 'warning');
        }
        
        saveShareList();
    });

    // 4. 分享清單中的移除按鈕
    shareListContainer.on('click', '.remove-from-share', function() {
        const name = $(this).data('anime-name');
        shareList = shareList.filter(item => item.anime_name !== name);
        saveShareList();
        
        // 更新主列表的按鈕狀態
        const mainListBtn = animeContainer.find(`.anime-card button[data-anime-name="${name}"]`);
        if (mainListBtn.length) {
             mainListBtn.removeClass('btn-danger remove-btn').addClass('btn-primary add-btn').html('<i class="fas fa-plus-circle"></i> 加入清單');
        }
        showAlert('已移除', `${name} 已從分享清單移除。`, 'warning');
    });

    // 5. 清空清單按鈕
    clearShareListBtn.on('click', function() {
        Swal.fire({
            title: '確認清空？',
            text: "確定要清空所有分享清單中的動畫嗎？",
            icon: 'warning',
            showCancelButton: true,
            confirmButtonText: '確定清空',
            cancelButtonText: '取消'
        }).then((result) => {
            if (result.isConfirmed) {
                shareList = [];
                saveShareList();
                // 重設主列表所有按鈕狀態
                animeContainer.find('.remove-btn').each(function() {
                    $(this).removeClass('btn-danger remove-btn').addClass('btn-primary add-btn').html('<i class="fas fa-plus-circle"></i> 加入清單');
                });
                showAlert('已清空', '分享清單已清空。', 'success');
            }
        });
    });

    // 6. 匯出圖片按鈕
    exportImageBtn.on('click', async function() {
        if (shareList.length === 0) {
            showAlert('清單為空', '請先加入動畫到分享清單。', 'info');
            return;
        }

        showAlert('生成中', '正在生成圖片，請稍候...', 'info', 10000);
        
        // 為了匯出美觀，暫時在清單上方加入標題
        const originalHtml = shareListContainer.html();
        const tempTitle = `<h4 class="text-center mb-3 fw-bold text-dark p-2" style="border-bottom: 2px solid #007bff;">我的動畫追番清單</h4>`;
        shareListContainer.prepend(tempTitle);

        try {
            const canvas = await html2canvas(shareListContainer[0], {
                scale: 2, // 提高解析度
                useCORS: true, // 處理跨域圖片 (Cloudinary 圖片應該沒問題)
                backgroundColor: '#ffffff' // 確保背景是白色
            });

            // 1. 嘗試複製圖片到剪貼簿 (Web API)
            try {
                const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
                const item = new ClipboardItem({ "image/png": blob });
                await navigator.clipboard.write([item]);
                showAlert('複製成功', '圖片已成功複製到剪貼簿！', 'success', 3000);
            } catch (err) {
                console.warn('圖片複製到剪貼簿失敗，將嘗試下載。', err);

                // 2. Fallback 1: 觸發下載 (適用於圖片複製失敗的瀏覽器)
                const link = document.createElement('a');
                link.download = `anime-share-list-${Date.now()}.png`;
                link.href = canvas.toDataURL('image/png');
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                showAlert('已下載', '圖片已下載到裝置（複製失敗時的備份）！', 'info', 2000);

                // 3. Fallback 2: 同時複製文字清單
                const textList = shareList.map(anime => `• ${anime.anime_name}\n  首播：${anime.premiere_date} ${anime.premiere_time}\n  故事：${anime.story}`).join('\n\n');
                await copyToClipboard(textList);
                console.log('文字清單已備份複製');
            }
        } catch (err) {
            console.error('html2canvas 生成錯誤：', err);
            showAlert('生成失敗', '無法生成圖片，請檢查圖片來源或瀏覽器設定。', 'error');

            // 最終 Fallback: 複製純文字清單
            const textList = shareList.map(anime => `• ${anime.anime_name}\n  首播：${anime.premiere_date} ${anime.premiere_time}\n  故事：${anime.story}`).join('\n\n');
            const success = await copyToClipboard(textList);
            if (success) {
                showAlert('已複製', '圖片生成失敗，但已將清單文字複製到剪貼簿。', 'info');
            }
        } finally {
            // 無論成功與否，都將暫時加入的標題移除
            shareListContainer.html(originalHtml);
        }
    });
    
    // 7. 滾動到頂部按鈕
    $(window).scroll(function() {
        if ($(this).scrollTop() > 100) {
            backToTopBtn.fadeIn();
        } else {
            backToTopBtn.fadeOut();
        }
    });

    backToTopBtn.click(function() {
        $('html, body').animate({scrollTop : 0}, 600);
        return false;
    });

    // --- 【初始化】 ---
    
    // 初始化 Select2
    $('.form-select').select2({
        minimumResultsForSearch: Infinity // 隱藏搜尋框
    });
    
    // 載入分享清單並渲染
    renderShareList();

    // 頁面載入後，自動載入當前年/季的資料並篩選
    searchForm.trigger('submit');
});