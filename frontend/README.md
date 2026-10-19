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

`app` 以 property descriptors 組合方法，保留 filteredAnime getter 的延遲求值和 Alpine 響應式行為；不能改成展開執行 getter 的 `{...searchMethods}`。共享請求狀態以 WeakMap 隔離各 app，資料快取與分享清單由各實例建立；html2canvas 載入 Promise 則刻意在模組作用域共享。

產物以 IIFE 封裝，不在 window 註冊 helper 或 app 方法。瀏覽器只透過既有 alpine:init 註冊 animeApp。測試為存取私有 helper 而在 VM 中插入測試掛點，不會將掛點寫入產物。

`generate_static.py` 和 `verify-dist` 會檢查生成檔完全符合來源；即使指定 BUILD_VERSION 也不能略過。遺漏模組、重複名稱／檔案、錯序依賴、目錄跳脫或未更新生成檔都拒絕建置。Python fixtures 可省略整個 frontend 目錄，正式專案則在測試中另驗證已追蹤生成檔。
