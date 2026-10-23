// Requires: settings, images
const HTML2CANVAS_URL = 'https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js';
const HTML2CANVAS_INTEGRITY = 'sha384-ZZ1pncU3bQe8y31yfZdMFdSpttDoPmOZg2wguVK9almUodir1PghgT0eY7Mrty8H';
let html2canvasLoadPromise = null;
function withShareDeadline(promise, milliseconds, code) {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(createShareError(code, '分享圖片處理逾時')), milliseconds);
        Promise.resolve(promise).then(resolve, reject).finally(() => clearTimeout(timer));
    });
}
function createShareError(code, message) {
    const error = new Error(message);
    error.name = 'ShareImageError';
    error.code = code;
    return error;
}
function describeClipboardError(error, supported = true) {
    if (!supported) {
        return '目前瀏覽器不支援自動複製，請手動選取動畫名稱複製。';
    }
    if (error && error.name === 'NotAllowedError') {
        return '瀏覽器拒絕剪貼簿權限，請允許此網站使用剪貼簿，或手動選取動畫名稱複製。';
    }
    return '複製文字失敗，請確認瀏覽器權限後再試，或手動選取動畫名稱複製。';
}
function describeShareError(error) {
    const messages = {
        SHARE_IMAGE_URL: '分享清單中有圖片網址未通過安全驗證，請重新加入該動畫後再試。',
        SHARE_IMAGE_LOAD: '部分圖片無法從圖片服務載入，請檢查網路後再試。',
        SHARE_RENDERER_TIMEOUT: '分享圖片元件載入逾時，請檢查網路後再試。',
        SHARE_RENDERER_NETWORK: '分享圖片元件載入失敗，請檢查網路後再試。',
        SHARE_RENDERER: '瀏覽器無法完成分享圖片渲染，請稍後再試。',
        SHARE_FONT_TIMEOUT: '等待字型逾時，請檢查網路後再試。',
        SHARE_RENDER_TIMEOUT: '分享圖片渲染逾時，請減少清單項目後重試。',
        SHARE_EXPORT_TIMEOUT: '圖片匯出逾時，請確認下載或剪貼簿權限後再試。',
        SHARE_SIZE: '清單產生的圖片過大，請減少項目並分批匯出。',
        SHARE_EXPORT: '圖片匯出失敗，請確認瀏覽器下載權限後再試。',
    };
    const requestedCode = error && error.code;
    const code = Object.prototype.hasOwnProperty.call(messages, requestedCode)
        ? requestedCode
        : 'SHARE_EXPORT';
    return {
        code,
        title: code === 'SHARE_IMAGE_URL' ? '圖片網址無法使用' : '生成圖片失敗',
        message: messages[code] || '瀏覽器無法完成圖片匯出，請稍後再試。',
    };
}
function loadHtml2Canvas() {
    if (typeof window !== 'undefined' && typeof window.html2canvas === 'function') {
        return Promise.resolve(window.html2canvas);
    }
    if (html2canvasLoadPromise) return html2canvasLoadPromise;

    html2canvasLoadPromise = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        let settled = false;
        const timeoutId = setTimeout(() => {
            if (settled) return;
            settled = true;
            reject(createShareError('SHARE_RENDERER_TIMEOUT', 'html2canvas 載入逾時'));
        }, HTML2CANVAS_TIMEOUT_MS);
        const finish = (callback, value) => {
            if (settled) return;
            settled = true;
            clearTimeout(timeoutId);
            callback(value);
        };
        script.src = HTML2CANVAS_URL;
        script.integrity = HTML2CANVAS_INTEGRITY;
        script.crossOrigin = 'anonymous';
        script.async = true;
        script.dataset.animeHtml2canvas = 'true';
        script.onload = () => {
            if (typeof window.html2canvas === 'function') {
                finish(resolve, window.html2canvas);
            } else {
                finish(reject, createShareError('SHARE_RENDERER', 'html2canvas 載入後未提供函式'));
            }
        };
        script.onerror = () => finish(reject, createShareError('SHARE_RENDERER_NETWORK', 'html2canvas 載入失敗'));
        document.head.appendChild(script);
    }).catch(error => {
        html2canvasLoadPromise = null;
        const failedScript = document.querySelector('script[data-anime-html2canvas="true"]');
        if (failedScript && failedScript.parentNode) failedScript.parentNode.removeChild(failedScript);
        throw error;
    });

    return html2canvasLoadPromise;
}
const sharingMethods = {
    copyText(text) {
        const clipboard = typeof navigator !== 'undefined' && navigator ? navigator.clipboard : null;
        const supported = Boolean(clipboard && typeof clipboard.writeText === 'function');
        if (!supported) {
            if (typeof Swal !== 'undefined' && Swal.fire) {
                Swal.fire({
                    toast: true, position: 'top-end', icon: 'warning',
                    title: '無法自動複製', text: describeClipboardError(null, false),
                    timer: 2500, showConfirmButton: false,
                    background: '#2b2b2b', color: '#fff'
                });
            }
            return Promise.resolve(false);
        }

        return Promise.resolve()
            .then(() => clipboard.writeText(String(text ?? '')))
            .then(() => {
                if (typeof Swal !== 'undefined' && Swal.fire) {
                    Swal.fire({
                        toast: true, position: 'top-end', icon: 'success',
                        title: '已複製', showConfirmButton: false, timer: 1000,
                        background: '#2b2b2b', color: '#fff'
                    });
                }
                return true;
            })
            .catch(error => {
                if (typeof Swal !== 'undefined' && Swal.fire) {
                    Swal.fire({
                        toast: true, position: 'top-end', icon: 'error',
                        title: '複製失敗', text: describeClipboardError(error),
                        timer: 3000, showConfirmButton: false,
                        background: '#2b2b2b', color: '#fff'
                    });
                }
                return false;
            });
    },

    addToShare(anime) {
        const identity = JSON.stringify([anime.bangumi_id || null, anime.anime_name, anime.original_name || '', anime.external_ids?.bangumi || null]);
        if (this.shareList.some(item => item.identity === identity)) {
            Swal.fire({toast: true, position: 'top', icon: 'warning', title: '已在清單中', timer: 1000, showConfirmButton: false, background: '#2b2b2b', color: '#fff'});
            return;
        }
        if (this.shareList.length >= SHARE_MAX_ITEMS) {
            Swal.fire({icon: 'warning', title: '分享清單已滿', text: `單次最多 ${SHARE_MAX_ITEMS} 部，請先匯出或移除部分項目。`, background: '#1e1e1e', color: '#fff'});
            return;
        }
        this.shareList.push({
            identity,
            token: ++this._shareItemSequence,
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

    removeFromShare(index, event) {
        const restoreFocus = event && event.currentTarget === document.activeElement;
        this.shareList.splice(index, 1);
        if (restoreFocus) {
            this.$nextTick(() => {
                const buttons = document.querySelectorAll('#shareListContainer .share-remove');
                const target = buttons[Math.min(index, buttons.length - 1)]
                    || document.getElementById('shareListTitle');
                if (target) target.focus();
            });
        }
    },

    async generateShareImage() {
        if (this._shareExportRunning || !this.shareList.length) return;
        this._shareExportRunning = true;
        const exportItems = this.shareList.map(item => ({...item}));
        const exportedTokens = new Set(exportItems.map(item => item.token));
        const removeExportedItems = () => {
            this.shareList = this.shareList.filter(item => !exportedTokens.has(item.token));
        };
        let exporting = true;
        const btn = document.getElementById('copyButton');
        const originalText = btn ? btn.innerHTML : '';
        let exportContainer = null;
        try {
            if (exportItems.length > SHARE_MAX_ITEMS) {
                throw createShareError('SHARE_SIZE', '分享項目超過安全上限');
            }
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 正在生成分享圖片...';
            }

            const renderHtml2Canvas = await loadHtml2Canvas();
            if (typeof renderHtml2Canvas !== 'function') {
                throw createShareError('SHARE_RENDERER', 'html2canvas 載入後無法使用');
            }

            // 建立隱藏容器；URL 驗證也在此 try 內，確保任何例外都會復原按鈕。
            exportContainer = document.createElement('div');
            exportContainer.dataset.animeShareExport = 'true';
            exportContainer.style.cssText = `
                position: fixed; top: 0; left: -9999px; z-index: -1;
                width: 600px;
                background-color: #1a1a1a;
                padding: 40px;
                display: flex; flex-direction: column; gap: 40px;
                font-family: 'Noto Sans TC', sans-serif;
            `;

            // 使用 DOM API 建卡片，避免把外部資料拼成 HTML。
            exportItems.forEach(item => {
                let imageUrl;
                try {
                    imageUrl = new URL(item.img, window.location.origin);
                } catch (error) {
                    throw createShareError('SHARE_IMAGE_URL', '分享圖片網址格式錯誤');
                }
                if (
                    imageUrl.protocol !== 'https:' ||
                    imageUrl.hostname !== 'res.cloudinary.com' ||
                    !imageUrl.pathname.includes('/image/upload/')
                ) {
                    throw createShareError('SHARE_IMAGE_URL', '分享圖片來源不是允許的 Cloudinary URL');
                }

                const transformedUrl = buildCloudinaryTransformationUrl(
                    imageUrl.toString(),
                    'q_auto:best,f_auto'
                );
                if (!transformedUrl) {
                    throw createShareError('SHARE_IMAGE_URL', '分享圖片 URL 格式不受支援');
                }

                const card = document.createElement('div');
                card.style.cssText = `
                    width: 100%; display: flex; flex-direction: column;
                    background-color: #2b2b2b; border-radius: 24px;
                    overflow: hidden; box-shadow: 0 20px 50px rgba(0,0,0,0.5);
                `;

                const imageWrapper = document.createElement('div');
                imageWrapper.style.cssText = 'width: 100%; line-height: 0;';
                const image = document.createElement('img');
                image.src = transformedUrl;
                image.crossOrigin = 'anonymous';
                image.alt = '';
                image.decoding = 'async';
                image.style.cssText = 'width: 100%; height: auto; display: block;';
                imageWrapper.appendChild(image);

                const titleWrapper = document.createElement('div');
                titleWrapper.style.cssText = `
                    padding: 30px 35px; background-color: #252525;
                    border-top: 1px solid #333; display: flex;
                    flex-direction: column; justify-content: center;
                `;
                const title = document.createElement('h2');
                title.textContent = String(item.name || '');
                title.style.cssText = `
                    margin: 0; font-size: 42px; font-weight: 700;
                    color: #ffffff; line-height: 1.4; min-height: 1.4em;
                `;
                titleWrapper.appendChild(title);
                card.append(imageWrapper, titleWrapper);
                exportContainer.appendChild(card);
            });

            document.body.appendChild(exportContainer);

            // 架構防禦：剪貼簿不可用時自動下載。
            const triggerFallbackDownload = (blob) => {
                try {
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.style.display = 'none';
                    a.href = url;
                    a.download = `anime_list_${new Date().getTime()}.png`;
                    document.body.appendChild(a);
                    a.click();
                    window.URL.revokeObjectURL(url);
                    document.body.removeChild(a);
                } catch (error) {
                    throw createShareError('SHARE_EXPORT', '圖片下載備案失敗');
                }

                Swal.fire({
                    icon: 'success',
                    title: '圖片已下載！',
                    text: '因瀏覽器限制剪貼簿，已自動為您下載圖片',
                    background: '#1e1e1e', color: '#fff',
                    timer: 3000, showConfirmButton: false
                });
            };

            // 等待所有圖片與字體載入，避免截圖競態。
            const images = Array.from(exportContainer.querySelectorAll('img'));
            await Promise.all(images.map(img => {
                if (img.complete) {
                    if (typeof img.naturalWidth === 'number' && img.naturalWidth === 0) {
                        return Promise.reject(createShareError('SHARE_IMAGE_LOAD', '分享圖片無法載入'));
                    }
                    return Promise.resolve();
                }
                return new Promise((resolve, reject) => {
                    let settled = false;
                    const finish = (callback, value) => {
                        if (settled) return;
                        settled = true;
                        clearTimeout(timeoutId);
                        callback(value);
                    };
                    const timeoutId = setTimeout(() => {
                        finish(reject, createShareError('SHARE_IMAGE_LOAD', '分享圖片載入逾時'));
                    }, SHARE_IMAGE_TIMEOUT_MS);
                    img.onload = () => finish(resolve);
                    img.onerror = () => finish(reject, createShareError('SHARE_IMAGE_LOAD', '分享圖片無法載入'));
                });
            }));
            if (document.fonts && document.fonts.ready) {
                await withShareDeadline(document.fonts.ready, SHARE_FONT_TIMEOUT_MS, 'SHARE_FONT_TIMEOUT');
            }
            const width = Math.max(1, exportContainer.scrollWidth);
            const height = Math.max(1, exportContainer.scrollHeight);
            const scale = Math.min(3, Math.sqrt(SHARE_CANVAS_MAX_PIXELS / (width * height)), SHARE_CANVAS_MAX_DIMENSION / Math.max(width, height));
            if (!Number.isFinite(scale) || scale < 1) {
                throw createShareError('SHARE_SIZE', '分享圖片尺寸超出安全上限');
            }

            let canvas;
            try {
                canvas = await withShareDeadline(renderHtml2Canvas(exportContainer, {
                    scale, useCORS: true, allowTaint: true,
                    backgroundColor: '#1a1a1a', logging: false, letterRendering: 1
                }), SHARE_RENDER_TIMEOUT_MS, 'SHARE_RENDER_TIMEOUT');
            } catch (error) {
                if (error?.code === 'SHARE_RENDER_TIMEOUT') throw error;
                throw createShareError('SHARE_RENDERER', '瀏覽器無法完成分享圖片渲染');
            }
            if (!canvas || typeof canvas.toBlob !== 'function') {
                throw createShareError('SHARE_EXPORT', '瀏覽器未提供圖片匯出功能');
            }

            await withShareDeadline(new Promise((resolve, reject) => {
                const rejectExport = () => reject(createShareError('SHARE_EXPORT', '圖片匯出失敗'));
                try {
                    canvas.toBlob(blob => {
                        if (!exporting) return;
                        try {
                            if (!blob) return rejectExport();

                            const clipboard = typeof navigator !== 'undefined' && navigator ? navigator.clipboard : null;
                            if (clipboard && window.ClipboardItem) {
                                clipboard.write([new window.ClipboardItem({'image/png': blob})])
                                    .then(() => {
                                        if (!exporting) return;
                                        Swal.fire({
                                            icon: 'success', title: '圖片已複製！',
                                            text: '可直接貼上至 LINE 或社群，已匯出的項目已移除',
                                            background: '#1e1e1e', color: '#fff',
                                            timer: 2000, showConfirmButton: false
                                        });
                                        removeExportedItems();
                                        resolve();
                                    })
                                    .catch(() => {
                                        if (!exporting) return;
                                        console.warn('[Clipboard] 寫入被拒絕，啟動下載備案');
                                        try {
                                            triggerFallbackDownload(blob);
                                            removeExportedItems();
                                            resolve();
                                        } catch (error) {
                                            reject(error);
                                        }
                                    });
                            } else {
                                console.warn('[Clipboard] API 不支援，啟動下載備案');
                                try {
                                    triggerFallbackDownload(blob);
                                    removeExportedItems();
                                    resolve();
                                } catch (error) {
                                    reject(error);
                                }
                            }
                        } catch (error) {
                            reject(error instanceof Error ? error : createShareError('SHARE_EXPORT', '圖片匯出失敗'));
                        }
                    }, 'image/png');
                } catch (error) {
                    rejectExport();
                }
            }), SHARE_EXPORT_TIMEOUT_MS, 'SHARE_EXPORT_TIMEOUT');
        } catch (error) {
            const safeError = describeShareError(error);
            console.error('[ShareImage Error]', {code: safeError.code});
            if (typeof Swal !== 'undefined' && Swal.fire) {
                Swal.fire({
                    icon: 'error',
                    title: safeError.title,
                    text: `${safeError.message}（錯誤代碼：${safeError.code}）`,
                    background: '#1e1e1e', color: '#fff'
                });
            }
        } finally {
            exporting = false;
            this._shareExportRunning = false;
            if (exportContainer && document.body.contains(exportContainer)) {
                document.body.removeChild(exportContainer);
            }
            if (btn) {
                btn.disabled = this.shareList.length === 0;
                btn.innerHTML = originalText;
            }
        }
    }
};
