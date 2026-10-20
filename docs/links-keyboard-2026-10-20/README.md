# 資料庫連結查證與鍵盤操作修正

本批從最新main `f96477310df376f359ebde4b945e553ba9d1ccff`（PR #72）接續。觀察時間：2026-10-20；新增Bangumi／AniList連結均為0，保留缺漏1／1,378，沒有猜ID或更動來源範圍。[37季、2,150筆逐檔不變證據](data-invariants.json)亦確認簡介仍缺98筆，播出方式仍無缺漏。

## 連結查證

- [實效環境](environment.json)：revision 23與observed revision 23一致、observations_current=true、network=enforced。`api.bgm.tv`、`graphql.anilist.co`、`acgsecrets.hk`均在有效允許清單。本次沒有修改網路設定；工具沒有正式設定寫入能力。
- [AniList重測](anilist.json)：使用現存明示ID 101360，向`https://graphql.anilist.co`發送專案既有有界查詢，HTTP 403且為Cloudflare封鎖頁。這次不是「網域未列入允許清單」；未換代理、端點或工具繞過限制。待同端點恢復可用，再按精確原名、類型、日期及季別／cour驗證，不以搜尋排名或上一季代替。
- [Bangumi完整季度目錄](bangumi-catalog.json)：以既有`fetch_quarter_catalog`讀完2019年4、5、6月所有動畫分頁，去重203筆，無《猫のニャッホ》原名精確匹配。保存目錄ID、日期、來源名稱與原始彙整雜湊。
- [Bangumi完整搜尋](bangumi-search-complete.json)：`POST /v0/search/subjects`，keyword=`猫のニャッホ`、type=2，按回應實際每頁20筆讀完441筆並核對總數不變、ID不重複；無原名精確匹配。前一批只讀首批結果的限制已消除，但這仍不能證明作品不存在於Bangumi或排除未列出的別名／錯誤日期；沒有可確認候選就保留anime-318《奇喵的畫家》的未知ID。後續待製作方／來源頁明示連結，或資料庫新增可核對條目。
- [37季直接來源頁](primary-links.json)：全部HTTP 200，卡片ID唯一；只比較卡片ID、中文名與原名完全一致的記錄。新增明示連結0、已有ID衝突0。每頁保留URL、觀察時間、正文SHA-256與卡片數；不把既有連結或同名不同版本算成補入。

既有可靠簡介、集數、播出日與範圍規則均未更動，無需新增資料覆寫例外。上一季、母條目及不同cour的連結也不能用來消除缺漏。

## 鍵盤操作

標題保留h3，內含原生複製按鈕；簡介與移除也使用原生button，可用Tab進入及Enter／Space啟動。按鈕名稱包含對應作品，簡介標示會開啟對話框；裝飾圖示不讀出。四類按鈕均有清楚的焦點外框。

簡介關閉回到原按鈕；分享清單移除後聚焦下一筆、最後一筆移除則回前一筆，全部清空則回到分享清單標題。以作品名作清單穩定key，與既有禁止同名重複加入規則一致。回頂端有「回到頁面頂端」名稱，啟動後焦點回到頁首標題，避免停在隱藏按鈕。

新增真實Alpine與正式CSP下的鍵盤回歸，涵蓋桌面1280px與手機viewport 360px、焦點外框、複製文字、完整簡介、Escape關閉、連續移除與回頂端。360px是Chromium viewport測試，不代表真實手機或其他引擎驗收；CSP收緊及跨瀏覽器仍依原改善計畫。

## 驗證

本地Python620通過、2項Windows整合測試本地略過；前端46、完整Chromium26通過；Ruff、依賴完整性、靜態建置與validate-all通過，35個本地文件連結有效。PR品質／原生Windows檢查、正式站逐檔及瀏覽器驗收，結果記於本批PR。未手動觸發排程、建立測試Issue／留言或操作Cloudinary；本批PR與push不當作自然schedule證據。
