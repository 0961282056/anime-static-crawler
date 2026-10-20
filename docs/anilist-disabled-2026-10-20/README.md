# 停用AniList自動API核對

2026-10-20依使用者決定，從PR #73合併版本`115332032267d8512b72fd81addeb1c7f8e8b57e`接續：停用自動AniList API核對，保留既有連結與精確來源綁定。這是移除可選網路依賴，沒有將Cloudflare 403標示為已修復，也沒有改代理、出口、憑證信任或允許清單。

## 行為與限制

- `SOURCE_AUDIT_ANILIST_ENABLED`預設`false`，正式每週工作流程亦明確設為`false`；只接受`true`／`false`，忽略大小寫與外圍空白，空值或拼錯直接報設定錯誤。停用時不建立客戶端或送出AniList請求，即使呼叫者注入客戶端也不呼叫它。
- JSON新增`secondary_source`設定狀態，停用明示`state=disabled`及`reason=disabled_by_configuration`。逐筆比較為`not_attempted`、沒有新觀察，嘗試及成功時間都為空；品質收據同樣標示未嘗試。Markdown明示停用，這些項目不進入待修差異或來源故障通知。
- 摘要`anilist_subjects_eligible`只計算有唯一、可核對明示ID的候選；`requested`／`attempted`／`observed`分開統計，停用時均為0。不能把保留的ID或來源頁新看到的連結當成API成功查證。
- 季度來源完整性、改名／來源差異、既有ID衝突、人工官方快照到期及官方目錄更新錯誤依原規則處理。主要來源取得失敗仍使CLI失敗並保留部分報告；停用不讓舊Issue或持久待辦自動結案。
- [37季、2,150筆逐檔不變證據](data-invariants.json)：保留772筆既有AniList連結、689個唯一ID；1,378筆可選未連結數照實保留並暫停主動補齊。簡介98、播出方式0、Bangumi1的缺漏數不變。
- 每日爬蟲、Bangumi／官方來源更新與網站既有參考連結照常。未以其他資料庫冒充AniList，也未將Bangumi API和其Archive宣稱為兩個獨立來源。

## 驗證

新增回歸涵蓋：預設停用時禁止建立／呼叫API、報告與品質收據不造假、CLI在主要來源失敗時仍失敗、ID衝突與官方快照複查仍產生待辦、舊審核Issue不因停用而關閉或留言、顯式啟用時既有有界核對仍可測試。

本地Python634通過、2項Windows整合測試本地略過；前端46、完整Chromium26通過；Ruff、依賴完整性、靜態建置與validate-all通過。原生Windows CI、PR及正式發布驗收結果記於本批PR。

[本地真實來源核對](live-audit-summary.json)完成37季、2,150筆，錯誤0；32季一致、5季為既有播出口徑差異（耐子、巨神、魔法紀錄、KJ FILE及OniPan!），沒有寫入修正。以防線禁止建立AniList客戶端，全部689個可核對ID均未要求／嘗試／成功觀察，772筆比較均為依設定未嘗試。這是本地唯讀驗收，不是GitHub自然schedule，也沒有呼叫通知流程。

## 接續維護

維護說明見[資料來源交叉核對與複查](../資料查證與品質.md)。目前主動工作以可靠簡介及Bangumi缺漏為主；AniList未連結數為可選覆蓋指標。未來只有明確決定重新啟用且原端點小量驗收成功，才恢復自動API核對。

本批未手動觸發schedule或workflow_dispatch，沒有寄信或呼叫通知寫入流程，也沒有更動Cloudinary。程式PR及main push不作為自然schedule觀察；下一次正式每週schedule的實際結果另行記錄。
