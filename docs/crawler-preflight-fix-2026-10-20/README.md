# 排程預檢版本不一致修正（2026-10-20）

實際失敗：[Scheduled Anime Crawler #39327089011](https://github.com/0961282056/anime-static-crawler/actions/runs/39327089011)。事件為 **schedule**，建立時間2026-10-20 05:23:19臺北，執行版本`3de665c5da3298d2b0cbdf24cf2a04f1b4bd63a4`。`crawl-and-prepare`的「Run pre-crawl quality gate」失敗，爬蟲、產物提交及發布PR步驟均未執行；最終通知工作正常完成。

## 原因與修正

GitHub的[檢查註記](failed-check-annotations.json)指出`validate-all`失敗：`Release inventory does not match the generated output`。[工作與步驟結果](failed-schedule.json)確認失敗發生於預檢。完整日誌下載重新導向到`productionresultssa11.blob.core.windows.net`遭目前網路政策拒絕；本批以允許的GitHub檢查註記與本地重現定位，沒有繞過該拒絕，也沒有將帶簽章的下載網址保存到儲存庫。

工作流程原先依序執行：

```sh
BUILD_ONLY=true BUILD_VERSION=preflight python generate_static.py
python manage.py validate-all
```

第一行的`BUILD_VERSION`只對該程序生效，會把`preflight`寫入`release.json`與首頁。第二行重新由內容計算版本，與清單中的`preflight`不符，因此中止。使用原流程在本地成功重現同一錯誤。Python套件檢查與兩份鎖檔安全稽核均通過；GitHub的Node20棄用警告也不是這次失敗原因。

移除第一行單獨使用的`BUILD_VERSION=preflight`，使兩個程序使用同一預設內容版本。`BUILD_ONLY=true`與`validate-all`保留，發布清單、完整資料、資產及搜尋索引驗證規則均保留。排程時間、啟用條件、並行鎖、授權、憑證與通知設定不變。

## 回歸與驗證

新增測試直接讀取crawler.yml中預檢的建置／驗證兩行，以獨立子程序執行；模擬工作層的`BUILD_ONLY=false`，並在臨時輸出目錄複製既有資料。測試先確認建置命令強制`BUILD_ONLY=true`，避免回歸時啟動真實爬蟲。

- 修正前：回歸測試在第二個程序重現同一release inventory錯誤。
- 修正後：完整Python **616通過、2個原生Windows專用略過**，coverage84.24%；實際建置後再開獨立`validate-all`程序通過，37季2,150筆與5資產正常。
- Ruff、格式、diff空白檢查通過；PR CI另驗證工作流程語法、shell、前端、標準瀏覽器與原生Windows。
- 沒有改動公開季度資料、既有簡介／ID／圖片／集數與來源規則；沒有執行爬蟲、操作Cloudinary、發測試留言或建立審核Issue。

PR檢查與合併push的結果另記於本批PR描述。沒有手動觸發或重跑schedule／workflow_dispatch；在下一個自然schedule實例完成之前，只能確認根因已重現、修正與預檢通過，不能宣稱自然排程已恢復成功。原失敗run的歷史狀態仍會是failure。
