# 文件入口

目前進入維護收斂：保留現有靜態網站與資料口徑，優先維持更新、驗證與發布穩定。第一次接手先讀[架構與資料流](架構與資料流.md)，要動手則讀[開發與測試](開發與測試.md)。接續工作前同步最新 `main`，再查[維護順序與重啟條件](後續工作順序.md)。

## 文件導覽

| 想了解／處理 | 唯一主要入口 |
|---|---|
| 整體架構、資料流、平台責任、目錄與排程 | [架構與資料流](架構與資料流.md) |
| 本機安裝、前端生成、建置與測試命令 | [開發與測試](開發與測試.md) |
| GitHub Environment／App、Cloudflare、Discord 設定與部署 | [部署與平台設定](部署與平台設定.md) |
| 憑證輪替、圖片清理、備份與復原 | [安全維運與災難復原](安全維運與災難復原.md) |
| 集數、已播／預定、星期及特殊版本口徑 | [集數與播出狀態](集數與播出狀態.md) |
| 來源與引用、審核回覆、證據時間與品質趨勢 | [資料查證與品質](資料查證與品質.md) |
| Python／npm／Actions 自動更新與 CDN 維護 | [依賴與Actions維護](依賴與Actions維護.md) |
| 執行錯誤代碼、保留資料與重試 | [錯誤診斷與重試](錯誤診斷與重試.md) |
| 維護順序、收斂完成條件與資料重啟條件 | [後續工作順序](後續工作順序.md) |
| 尚未實作的 CSP／跨瀏覽器改善 | [改善計畫](CSP與跨瀏覽器改善計畫.md) |
| 已完成事項、原始查證、舊報告 | [歷史與證據索引](archive/README.md) |

## 資料與發布基準

2026-10-23 依 [PR #87](https://github.com/0961282056/anime-static-crawler/pull/87) 合併版 `2237f25be2a293afa925e05c5594c5106b09567b` 的季度 JSON 重新計算。這是資料快照，不代替每次程式變更的測試與發布驗收：

| 項目 | 數量 |
|---|---:|
| 季度／季度記錄 | 37／2,150 |
| 簡介缺漏 | 98 |
| 播出方式缺漏 | 0 |
| Bangumi 連結缺漏 | 1 |
| AniList 連結缺漏（可選，暫停主動補齊） | 1,378 |
| 集數 stale／conflict | 0／0 |

數量按季度記錄計算，不是獨立作品數；不同缺漏欄位會重疊。`python manage.py quality-report` 可離線重算缺漏與刷新狀態；到期尚待刷新不等於來源連線失敗，也不與 stale／conflict 混用。命令與品質證據用途見[開發手冊](開發與測試.md#查資料品質)。

| 要確認的現況 | 查詢位置 |
|---|---|
| 最新 main 與每批變更 | [main 提交](https://github.com/0961282056/anime-static-crawler/commits/main/)與[Pull Requests](https://github.com/0961282056/anime-static-crawler/pulls)；本機先同步 main，再看 `git log -1` |
| 當次測試與排程 | PR 的 Checks 及 [Actions](https://github.com/0961282056/anime-static-crawler/actions)；核對 event、head commit、各工作結果與 artifact，不沿用舊版測試總數 |
| 正式站正在提供哪版 | [release.json](https://anime-static-crawler.pages.dev/release.json)與對應的 Production verification 工作；合併、部署、公開檔案／瀏覽器驗收分開確認 |

已完成事故、遷移及先前排程觀察統一見[歷史索引](archive/README.md#事故與過渡操作)。設定時間不代表實際執行時間；schedule、workflow_dispatch、push 與 PR 必須分開判讀。

## 資料補齊入口

這些是保留的查證缺口，取得新來源或修訂證據時才重啟。先讀[最近查證與範圍防護](high-priority-data-2026-10-20/README.md)，再查[98 筆簡介清單](wikipedia-fill-2026-10-19/remaining-stories.csv)及[完整剩餘清單索引](wikipedia-fill-2026-10-19/remaining-inventory.json)。清單保留原來源時間，不表示本次全部重新連線查證。百科固定修訂、採用原文與授權見[維基百科補齊紀錄](wikipedia-fill-2026-10-19/README.md)。

- 當季四筆：藥師第三季前半、鼠小弟第二季、夜櫻第二季第二部分、鑽石王牌 actⅡ 第二季 2nd cour，仍缺對應季別／cour 原文；預定集數不是簡介。
- 2019 夏《屁屁偵探》已確認第三系列，只缺該系列介紹；班長不能借利根川母條目，《學校沒教的重要事情》的 9 話說明只適用整理整頓編。
- 《奇喵的畫家》缺 Bangumi 連結，完整目錄與搜尋尚無原名精確候選，見[10/20 連結查證](links-keyboard-2026-10-20/README.md)。
- [AniList 自動 API 核對已停用](anilist-disabled-2026-10-20/README.md)，保留既有 772 筆連結與身分綁定；未連結數屬可選覆蓋統計，暫停主動補齊。季度來源與官方快照核對照常。

存取限制以當次環境實效設定及 HTTP 結果為準，不將舊報告中的 403、允許清單或排程狀態當作目前設定。

## 歷史與查證紀錄

已完成的修復、架構 1–7、舊進度與日期報告統一收進[歷史與證據索引](archive/README.md)。索引提供保留的原始證據目錄及被整併報告的固定 Git 版本；不把舊統計、已撤回判斷或「等待某日」重新當成有效待辦。

程式與測試使用的查證檔案維持既有路徑。新增操作說明優先更新本頁所列手冊；只有新的來源證據才另存批次目錄。
