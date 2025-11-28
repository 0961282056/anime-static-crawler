// static/js/main.js
// 依賴：jQuery, Select2, SweetAlert2, html2canvas

$(document).ready(function () {
    // --- 【新架構：資料處理和篩選核心】 ---

    let currentAnimeList = []; // 儲存當前季度載入的所有動畫資料
    const animeContainer = $('#anime-results-container');
    const resultCountSpan = $('#result-count');
    // ⭐️ 【關鍵修改】：使用 #search-form 精確選取表單 ⭐️
    const searchForm = $('#search-form'); 
    
    // --- 滾動到頂部按鈕 ---
    const backToTopBtn = $('#backToTopBtn');

    /**
     * 格式化單個動畫資料並生成 HTML 卡片
     * @param {object} anime - 單個動畫的資料物件
     * @returns {string} - 包含動畫卡片的 HTML 字串
     */
    function createAnimeCard(anime) {
        // ... (保持不變 - 此處內容根據您提供的 snippet 判斷)
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
                            <div class="info-group">
                                <span><strong>首播日:</strong> ${anime.premiere_date} ${anime.premiere_time} (${anime.premiere_day_of_week})</span>
                                <span><strong>類型:</strong> ${anime.anime_type}</span>
                                <span><strong>季度:</strong> ${anime.year} 年 ${anime.season} 季</span>
                            </div>
                            <p class="story-summary">${anime.story}</p>
                        </div>
                        <div class="mt-auto d-flex justify-content-between align-items-center">
                            <a href="${anime.official_website_url}" target="_blank" class="btn btn-sm btn-outline-primary me-2">官網</a>
                            <button class="btn btn-sm btn-info add-to-share" data-anime-name="${anime.anime_name}" data-anime-id="${anime.anime_id}">加入清單</button>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    /**
     * 載入並顯示資料
     */
    function loadData(year, season) {
        // ... (保持不變 - 您的 loadData 函數內容)
        // 假設您的 loadData 函數會處理 JSON 載入和 Select2 初始化
    }

    /**
     * 篩選動畫列表
     */
    function filterAnime() {
        // ... (保持不變 - 您的 filterAnime 函數內容)
        // 假設您的 filterAnime 函數會從 select/input 讀取值並篩選 currentAnimeList
    }

    // ... (其他輔助函數：createShareCard, updateShareListDisplay, copyToClipboard, showAlert, setupShareList...)
    
    // ⭐️ 【關鍵修正】：攔截表單提交事件，防止 405 錯誤 ⭐️
    searchForm.on('submit', function (e) {
        e.preventDefault(); // 阻止瀏覽器發送傳統的 HTTP 請求
        filterAnime(); // 執行前端篩選邏輯
    });

    // --- 【滾動到頂部按鈕邏輯】 ---
    // 顯示/隱藏按鈕
    $(window).scroll(function() {
        if ($(this).scrollTop() > 300) { 
            backToTopBtn.fadeIn();
        } else {
            backToTopBtn.fadeOut();
        }
    });

    // 點擊按鈕，平滑滾動到頁面頂部
    backToTopBtn.on('click', function(e) {
        e.preventDefault();
        $('html, body').animate({scrollTop : 0}, 800);
        return false;
    });


    // --- 程式初始化 ---
    // 1. 初始化 Select2
    $('#year, #season, #weekday, #keyword').select2({
        // ... (Select2 設定)
    });

    // 2. 初始載入資料 (讀取 URL 參數或使用預設值)
    const urlParams = new URLSearchParams(window.location.search);
    const initialYear = urlParams.get('year') || $('#year').val();
    const initialSeason = urlParams.get('season') || $('#season').val();
    
    // 初始載入
    loadData(initialYear, initialSeason).then(() => {
        // 資料載入後執行初始篩選
        filterAnime(); 
    });
});