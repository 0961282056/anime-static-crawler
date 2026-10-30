# 前端模組

編輯本目錄的原始碼，執行 `python manage.py build-frontend` 更新 `static/js/main.js`，再執行 `npm run test:frontend`、靜態建置及瀏覽器測試。不要直接修改生成檔。

`modules.json` 是明確的組合順序與依賴圖。這些是同一個私有作用域內的原生 JavaScript 原始模組，使用具名 helper 及方法物件組合，不是瀏覽器原生 ESM；組合器不做轉譯、import 改寫或動態程式執行，不引入新的 Node 建置依賴。正式站仍只請求帶 build_version 的 main.js，沿用 CSP、Alpine 啟動時序及 immutable 快取策略。

| 模組 | 職責／介面 |
|---|---|
| settings | 時限、狀態文字、讀取建置設定 |
| errors | 請求取消、錯誤分類、安全使用者訊息 |
| schema | 季度／搜尋索引驗證、精確官方簡介來源規則 |
| transport | fetch 時限、可取消等待、同一 app 的 pending 請求狀態 |
| images | Cloudinary URL 轉換 |
| data | dataMethods：版本快取、重試、競態、頁面資料載入 |
| search | searchMethods：索引搜尋、相關季度併發載入、結果標籤與 filteredAnime getter |
| presentation | presentationMethods：卡片、集數、來源與狀態顯示 |
| sharing | sharingMethods：剪貼簿、清單、懶載入 html2canvas、圖片匯出及清理 |
| app | 每個實例的狀態、儲存設定、初始化、Alpine 註冊及方法組合 |

`app` 以 property descriptors 組合方法，保留 filteredAnime／visibleAnime getter 的延遲求值和 Alpine 響應式行為；不能改成展開執行 getter 的 `{...searchMethods}`。共享請求狀態以每個 app 的 `_pendingDataRequests` Map 為 WeakMap 鍵，避免 Alpine 不同 scope proxy 分裂同一請求。每位訂閱者獨立取消；最後一位離開後，下一個 microtask 才取消底層 fetch，讓同一事件中的替代訂閱仍可共用。取消後的遲到回應不寫快取，全站搜尋任一 worker 失敗也取消其餘 worker。

季度請求依 `quarter_versions` 的 SHA-256 核對實際回應位元組，通過格式及版本檢查後才寫快取；不以網址查詢參數當成已取得舊檔案的證明。版本不同會回報 `SEARCH_VERSION`，由重試更新發布資訊，避免舊索引與新季度內容混用。瀏覽器需提供 HTTPS／localhost 安全環境下的 Web Crypto。合成瀏覽器資料透過 `tests/helpers/versioned-data.cjs` 同步建立對應雜湊及頁面設定，保留真實版本檢查。

全站搜尋的 `filteredAnime` 保留完整結果；`visibleAnime` 每次只增加 60 筆卡片，篩選及總數依完整結果計算，季度瀏覽不分批。分享清單最多 20 部，匯出使用開始時的快照，只移除實際匯出的項目；字型／渲染／匯出有期限，並限制畫布面積與邊長。設定集中於 `settings.js`。html2canvas 載入 Promise 刻意在模組作用域共享；它的內部渲染及瀏覽器剪貼簿作業沒有可用的取消介面，應用程式逾時後會清理自己的 DOM／狀態，並阻擋遲到 callback 再下載或清除清單。

桌機左側面板依動態視窗高度限制並可上下捲動；分享清單另限制在視窗高度的 40%／300px 以內，項目增加不會撐高整頁。窄版面板回到正常文件流，保留清單內捲動。面板與清單可用 Tab 聚焦後按 Home、End 或 PageUp／PageDown，移除項目後沿用相鄰項目的焦點；長名稱換行，移除按鈕至少 44px。簡介彈窗的內容區可獨立捲動，支援關閉按鈕與 Escape，關閉後還原原卡片焦點。這些行為由正式 CSP 下的桌機、低高度與手機直／橫向回歸測試驗證。

產物以 IIFE 封裝，不在 window 註冊 helper 或 app 方法。瀏覽器只透過既有 alpine:init 註冊 animeApp。測試為存取私有 helper 而在 VM 中插入測試掛點，不會將掛點寫入產物。

`generate_static.py` 和 `verify-dist` 會檢查生成檔完全符合來源；即使指定 BUILD_VERSION 也不能略過。遺漏模組、重複名稱／檔案、錯序依賴、目錄跳脫或未更新生成檔都拒絕建置。Python fixtures 可省略整個 frontend 目錄，正式專案則在測試中另驗證已追蹤生成檔。
