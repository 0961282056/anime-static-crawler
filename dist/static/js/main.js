// static/js/main.js
// 依賴：jQuery, Select2, SweetAlert2, html2canvas

$(document).ready(function () {
    // --- 【新架構：資料處理和篩選核心】 ---

    let currentAnimeList = []; // 儲存當前季度載入的所有動畫資料
    const animeContainer = $('#anime-results-container');
    const resultCountSpan = $('#result-count');
    const searchForm = $('form'); // 選擇查詢表單

    /**
     * 格式化單個動畫資料並生成 HTML 卡片
     * @param {object} anime - 單個動畫的資料物件
     * @returns {string} - 包含動畫卡片的 HTML 字串
     */
    function createAnimeCard(anime) {
        // 確保使用 data-anime-name 屬性，以支援您的複製邏輯
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
                            <div class="info-section mb-2">
                                <small class="text-muted d-block">
                                    <i class="fas fa-calendar-alt me-1"></i>首播日期：${anime.premiere_date || '未知'}
                                </small>
                                <small class="text-muted d-block">
                                    <i class="fas fa-clock me-1"></i>首播時間：${anime.premiere_time || '未知'}
                                </small>
                            </div>
                            <div class="story-section">
                                <small class="text-muted story-summary">
                                    <i class="fas fa-book me-1"></i>${anime.story || '暫無劇情簡介'}
                                </small>
                            </div>
                        </div>
                        <button type="button" class="btn btn-success btn-sm mt-auto add-to-sharelist w-100">加入分享清單</button>
                    </div>
                </div>
            </div>
        `;
    }

    /**
     * 渲染結果到頁面，只處理 "星期幾" 篩選和渲染。
     * 這是原 filterAndRenderAnime 函式的簡化版。
     * @param {Array} data - 當前季度完整的動畫資料列表 (currentAnimeList)
     */
    function filterAndRenderResults(data) {
        const selectedPremiereDate = $('#premiere_date').val();
        let filteredList = data;
        
        // 只進行星期幾篩選 (不再需要年/季檢查，因為資料已經匹配年/季)
        if (selectedPremiereDate && selectedPremiereDate !== '全部') {
            filteredList = filteredList.filter(anime => 
                anime.premiere_date === selectedPremiereDate
            );
        }
        
        // 渲染邏輯
        animeContainer.empty();
        
        if (filteredList.length === 0) {
            animeContainer.append('<div class="col-12"><div class="alert alert-warning text-center" role="alert">找不到符合條件的動畫資料。</div></div>');
        } else {
            const html = filteredList.map(createAnimeCard).join('');
            animeContainer.append(html);
        }

        // 更新計數
        resultCountSpan.text(filteredList.length);
    }

    /**
     * 根據下拉選單的年/季值，動態載入對應的 JSON 檔案。
     * 載入成功後，執行星期幾篩選和渲染。
     * @param {Event} e - 事件物件 (可選)
     */
    async function loadAndFilterAnime(e) {
        if (e) e.preventDefault(); 
        
        const selectedYear = $('#year').val();
        const selectedSeason = $('#season').val(); 
        
        if (!selectedYear || !selectedSeason) {
            console.warn("年份或季節未選擇，跳過載入。");
            return;
        }

        // 1. 構建 JSON 檔案路徑: 假設您的 generate_static.py 將檔案放在 /dist/data/
        // 且檔名為 {year}_{season}.json (例如: /data/2025_秋.json)
        const jsonUrl = `./data/${selectedYear}_${selectedSeason}.json`; 
        
        // 顯示載入狀態
        animeContainer.empty().append('<div class="col-12 text-center"><div class="spinner-border text-primary" role="status"><span class="visually-hidden">Loading...</span></div><p class="mt-2">正在載入資料...</p></div>');
        resultCountSpan.text(0);
        
        try {
            console.log(`嘗試載入資料: ${jsonUrl}`);
            const response = await fetch(jsonUrl);

            if (!response.ok) {
                // 如果找不到檔案 (HTTP 404/403 等)
                throw new Error(`該季度資料不存在 (狀態: ${response.status})`);
            }
            
            const fullData = await response.json();
            
            // 假設您的 JSON 結構是 { "anime_list": [...] }
            currentAnimeList = fullData.anime_list || []; 
            
            if (currentAnimeList.length === 0) {
                animeContainer.html('<div class="col-12"><div class="alert alert-warning text-center" role="alert">該季度資料為空。</div></div>');
            } else {
                // 載入成功後，執行星期幾篩選並渲染
                filterAndRenderResults(currentAnimeList);
            }

        } catch (error) {
            console.error("載入或處理動畫資料時發生錯誤:", error);
            // 顯示資料不存在或載入失敗的訊息
            animeContainer.html(`<div class="col-12"><div class="alert alert-danger text-center" role="alert">載入 ${selectedYear} 年 ${selectedSeason} 季資料失敗。<br>請確認 JSON 檔案是否存在: <code>${jsonUrl}</code></div></div>`);
            currentAnimeList = []; // 清空資料
        }
    }
    
    /**
     * 載入 JSON 資料並初始化網站 (主要是事件綁定和首次載入)
     */
    function initializeWebsite() {
        // 綁定 Select 變更事件：
        // 1. 年份/季節變更 -> 觸發資料載入 (loadAndFilterAnime)
        $('#year, #season').on('change', loadAndFilterAnime);
        
        // 2. 首播日期 (星期幾) 變更 -> 只觸發前端篩選 (filterAndRenderResults)
        $('#premiere_date').on('change', function() {
            filterAndRenderResults(currentAnimeList);
        });

        // 綁定查詢按鈕的 submit 事件
        searchForm.on('submit', loadAndFilterAnime);
        
        // 頁面載入時，根據預設選單值載入資料
        loadAndFilterAnime(); 
    }

    // --- 【原始功能區：分享清單與複製邏輯】 (保持不變) ---

    // 初始化 Select2
    $("select").select2({
        width: '100%',
        placeholder: "選擇...",
        allowClear: true
    });

    let shareList = [];
    let pressTimer;

    // 通用複製文字函數
    async function copyToClipboard(text) {
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(text);
            } else {
                // Fallback for older browsers
                const textarea = document.createElement('textarea');
                textarea.value = text;
                textarea.style.position = 'fixed';
                textarea.style.opacity = '0';
                document.body.appendChild(textarea);
                textarea.focus();
                textarea.select();
                document.execCommand('copy');
                document.body.removeChild(textarea);
            }
            return true;
        } catch (err) {
            console.error('複製失敗：', err);
            return false;
        }
    }

    // 顯示 SweetAlert 訊息
    function showAlert(title, text, icon = 'info', timer = null, showConfirm = true) {
        const config = {
            title: title,
            text: text,
            icon: icon,
            confirmButtonText: '確定'
        };
        if (timer) {
            config.timer = timer;
            config.showConfirmButton = false;
        }
        if (!showConfirm) {
            config.showConfirmButton = false;
        }
        Swal.fire(config);
    }

    // 長按故事大綱顯示完整內容彈跳視窗
    $(document).on('touchstart mousedown', '.anime-card .story-summary', function (e) {
        e.preventDefault();
        const $this = $(this);
        const fullText = $this.text().trim();
        const animeName = $this.closest('.anime-card').find('.anime-title').data('anime-name') || $this.closest('.anime-card').find('.anime-title').text().trim();
        
        pressTimer = setTimeout(() => {
            $this.addClass('long-pressed');
            
            Swal.fire({
                title: `${animeName} - 故事大綱`,
                html: `<div style="text-align: left; white-space: pre-wrap; font-size: 1.3rem; line-height: 1.4;">${fullText}</div>`,
                icon: 'info',
                width: '500px',
                padding: '2rem',
                showConfirmButton: true,
                confirmButtonText: '關閉',
                confirmButtonColor: '#007bff',
                allowOutsideClick: true,
                allowEscapeKey: true
            }).then(() => {
                $this.removeClass('long-pressed');
            });
        }, 800); // 長按延遲 800ms
    }).on('touchend touchcancel mouseup mouseleave', '.anime-card .story-summary', function () {
        clearTimeout(pressTimer);
        $(this).removeClass('long-pressed');
    });

    // 長按/滑鼠按下複製動畫名稱
    $(document).on('touchstart mousedown', '.anime-card .anime-title', function (e) {
        e.preventDefault();
        const $this = $(this);
        const animeName = $this.data('anime-name') || $this.text().trim();
        
        pressTimer = setTimeout(async () => {
            $this.addClass('long-pressed');
            const success = await copyToClipboard(animeName);
            if (success) {
                showAlert('已複製', `${animeName} 已複製到剪貼簿！`, 'success', 1500);
            } else {
                showAlert('失敗', '複製失敗，請稍後再試！', 'error');
            }
            $this.removeClass('long-pressed');
        }, 800); // 長按延遲 800ms
    }).on('touchend touchcancel mouseup mouseleave', '.anime-card .anime-title', function () {
        clearTimeout(pressTimer);
        $(this).removeClass('long-pressed');
    });

    // 點擊動畫標題彈窗複製
    $(document).on('click', '.anime-card .anime-title', function (e) {
        e.stopPropagation(); // 避免長按觸發
        const $this = $(this);
        const animeName = $this.data('anime-name') || $this.text().trim();
        
        Swal.fire({
            title: '複製動畫名稱',
            text: `複製 "${animeName}"？`,
            icon: 'question',
            showCancelButton: true,
            confirmButtonText: '複製',
            cancelButtonText: '取消',
            confirmButtonColor: '#28a745'
        }).then(async (result) => {
            if (result.isConfirmed) {
                const success = await copyToClipboard(animeName);
                if (success) {
                    showAlert('已複製', `${animeName} 已複製到剪貼簿！`, 'success', 1500);
                } else {
                    showAlert('失敗', '複製失敗，請稍後再試！', 'error');
                }
            }
        });
    });

    // 加入分享清單
    $(document).on('click', '.anime-card .add-to-sharelist', function (e) {
        e.preventDefault();
        const $card = $(this).closest('.anime-card');
        const anime = {
            name: $card.find('.anime-title').text().trim(),
            image: $card.find('img').attr('src'),
            premiere_date: $card.find('.info-section small').first().text().replace('首播日期：', '').trim(),
            premiere_time: $card.find('.info-section small').eq(1).text().replace('首播時間：', '').trim(),
            story: $card.find('.story-summary').text().trim()
        };

        // 避免重複加入
        if (!shareList.some(item => item.name === anime.name)) {
            shareList.push(anime);
            updateShareList();
            showAlert('成功', `${anime.name} 已加入分享清單！`, 'success', 1200);
        } else {
            showAlert('已存在', '此動畫已在清單中！', 'info', 1500);
        }
    });

    // 更新分享清單 UI
    function updateShareList() {
        const $container = $('#shareList').empty();
        if (shareList.length > 0) {
            shareList.forEach((anime, index) => {
                const $shareCard = $(`
                    <div class="share-card row g-3 mb-3">
                        <div class="col-md-4">
                            <img src="${anime.image}" class="img-fluid rounded share-img" alt="${anime.name}" style="width: 300px; height: 300px; object-fit: contain;" loading="lazy">
                        </div>
                        <div class="col-md-8 share-content">
                            <h6 class="anime-name">${anime.name}</h6>
                            <div class="share-info">
                                <small class="text-muted d-block">首播日期：${anime.premiere_date}</small>
                                <small class="text-muted d-block">首播時間：${anime.premiere_time}</small>
                            </div>
                            <div class="share-story mt-2">
                                <small class="text-muted">${anime.story.substring(0, 100)}${anime.story.length > 100 ? '...' : ''}</small>
                            </div>
                            <button class="btn btn-outline-danger btn-sm remove-from-list mt-2" data-index="${index}">移除</button>
                        </div>
                    </div>
                `);
                $container.append($shareCard);
            });
            $('#copyButton').fadeIn(300).prop('disabled', false).text('📋');
        } else {
            $container.html('<p class="text-muted text-center py-4">分享清單為空，點擊「加入分享清單」添加動畫。</p>');
            $('#copyButton').fadeOut(300).prop('disabled', true);
        }
    }

    // 移除分享項目
    $(document).on('click', '.remove-from-list', function () {
        const index = parseInt($(this).data('index'));
        shareList.splice(index, 1);
        updateShareList();
        showAlert('已移除', '動畫已從清單移除！', 'info', 1200);
    });

    // 複製分享清單為圖片（核心功能：轉圖片 + 複製）
    $('#copyButton').click(async function () {
        if (shareList.length === 0) {
            return showAlert('無內容', '分享清單為空，請先添加動畫！', 'warning');
        }

        const $button = $(this).prop('disabled', true).html('<span class="spinner-border spinner-border-sm me-2"></span>生成中...');
        try {
            // 步驟 1: 等待所有圖片載入
            console.log('開始等待圖片載入...');
            const imagePromises = shareList.map((anime, index) => {
                return new Promise((resolve, reject) => {
                    if (anime.image && anime.image !== '無圖片' && anime.image.startsWith('http')) {
                        const img = new Image();
                        img.crossOrigin = 'anonymous'; // 嘗試跨域
                        img.onload = () => {
                            console.log(`圖片 ${index + 1}/${shareList.length} 載入成功: ${anime.name}`);
                            resolve();
                        };
                        img.onerror = (err) => {
                            console.warn(`圖片 ${index + 1}/${shareList.length} 載入失敗: ${anime.name}`, err);
                            // 即使失敗也 resolve，避免卡住
                            resolve();
                        };
                        img.src = anime.image;
                    } else {
                        console.log(`跳過無效圖片 ${index + 1}/${shareList.length}: ${anime.name}`);
                        resolve();
                    }
                });
            });
            await Promise.all(imagePromises);
            console.log('所有圖片載入完成');

            // 步驟 2: 生成 canvas
            console.log('開始生成 canvas...');
            const canvas = await html2canvas(document.getElementById('shareList'), {
                scale: window.devicePixelRatio > 1 ? 2 : 1, // 自適應高 DPI 螢幕
                useCORS: true,  // 允許跨域資源
                allowTaint: true,  // 允許 tainted canvas
                backgroundColor: '#ffffff',  // 白色背景，避免透明
                width: document.getElementById('shareListContainer').scrollWidth, // 使用外層容器來確保寬度
                height: document.getElementById('shareListContainer').scrollHeight, // 使用外層容器來確保高度
                logging: true  // 開啟 log 除錯
            });
            console.log('Canvas 生成完成，尺寸:', canvas.width, 'x', canvas.height);

            // 步驟 3: 轉 Blob 並複製到剪貼簿
            canvas.toBlob(async (blob) => {
                if (!blob) {
                    throw new Error('Blob 生成失敗');
                }
                console.log('Blob 生成完成，大小:', blob.size, 'bytes');

                try {
                    // 現代瀏覽器：直接寫入剪貼簿
                    await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
                    console.log('圖片成功複製到剪貼簿');
                    showAlert('已複製', `分享清單（${shareList.length} 項）已作為圖片複製！可直接貼上。`, 'success', 2000);
                    shareList = [];  // 清空清單
                    updateShareList();
                } catch (clipboardErr) {
                    console.warn('剪貼簿 API 失敗:', clipboardErr);
                    // Fallback 1: 下載 PNG
                    const link = document.createElement('a');
                    link.download = `anime-share-list-${Date.now()}.png`;
                    link.href = canvas.toDataURL('image/png');
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                    showAlert('已下載', '圖片已下載到裝置（複製失敗時的備份）！', 'info', 2000);

                    // Fallback 2: 同時複製文字清單
                    const textList = shareList.map(anime => `${anime.name}\n首播：${anime.premiere_date} ${anime.premiere_time}\n故事：${anime.story}`).join('\n\n');
                    await copyToClipboard(textList);
                    console.log('文字清單已備份複製');
                }
            }, 'image/png', 0.95); // 高品質 PNG

        } catch (err) {
            console.error('html2canvas 生成錯誤：', err);
            showAlert('生成失敗', '無法生成圖片，請檢查圖片來源或瀏覽器設定（試試 Chrome）。', 'error');

            // 最終 Fallback: 複製純文字清單
            const textList = shareList.map(anime => `• ${anime.name}\n  首播：${anime.premiere_date} ${anime.premiere_time}\n  故事：${anime.story}`).join('\n\n');
            const success = await copyToClipboard(textList);
            if (success) {
                showAlert('文字備份', `已複製文字清單（${shareList.length} 項）到剪貼簿！`, 'info', 2000);
            }
        } finally {
            $button.prop('disabled', false).html('📋');
        }
    });

    // --- 網頁載入後執行初始化 ---
    initializeWebsite();
});