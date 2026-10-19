# 第 6、7 項：證據與品質趨勢、前端拆分

基準 main `4e3936374e09a5ed05f4932939bed3c95ea8c8d8`，按 6→7 實作，各自測試後提交。37 季／2,150 筆與 Cloudinary cache 均未變更。

## 第 6 項

- `quality-evidence` 唯讀命令，來源核對與離線整理共用 evidence receipt 格式。
- 精確身分、季度、欄位、來源／方法／範圍、成功／嘗試／快照時間分開記錄；舊資料未知欄位維持未知。預算耗盡未開始請求時，不寫嘗試時間。
- Git 第一父系資料版本重建八項品質數量／比率與同身分差異；新增／移除不誤算為解決。預設 120 版、可擴大至 1,000，截斷及相容性明列。
- 完整證據 JSON、Markdown、可直接開啟的 SVG；離線資料另保存 dist/data 快照及逐檔 hash，明列是否符合 commit，未提交資料也可重現。
- main 每次更新由唯讀工作自動產生，artifact 保存 90 天；Git 是可重建的持久歷史，不新增通知／Issue 或寫入權限。

完整格式及操作見[證據紀錄與品質趨勢](../證據紀錄與品質趨勢.md)。本地實際重建 120 個版本，零不相容版本；現況缺簡介 105、首播日 5、Bangumi 1、AniList 1,378。

## 第 7 項

- 10 個職責模組、明列依賴與順序；Python 確定性組合，沒有新增依賴。
- 私有 IIFE 生成單一 main.js，保留原有版本快取、CSP、Alpine 啟動時序。
- property descriptors 保留 filteredAnime getter；app 狀態獨立，pending WeakMap 仍依 owner 隔離，html2canvas Promise 刻意共用。
- 建置前檢查來源／產物一致，指定 BUILD_VERSION 也不能跳過；遺漏、重複、錯序、路徑跳脫與過期產物拒絕建置。
- 原始模組可獨立閱讀及審查；生成檔標示 linguist-generated，避免成為主要審查內容。

模組界面與操作見[frontend/README.md](../../frontend/README.md)。這是建置期模組組合，不宣稱瀏覽器原生 ESM 或額外壓縮／載入性能改善。

## 審查及驗證

審查重點：證據原始內容可重現、commit 與未提交資料不可混淆；重新整理報告不可冒充新查證；歷史刪除／換身分不冒充問題已解決；公開資料寫入邊界；getter 延遲求值、不同 app 的可變狀態與請求取消；單一產物及版本 hash；工作流程唯讀權限及無外部副作用。

本地 Python **584 通過、2 個 Windows 本地略過**，覆蓋率 **83.78%**；前端行為 **41 通過**；Chromium **18 通過**。包含髒資料快照、歷史重建／截斷、失敗／沿用證據、來源不明、模組圖、過期生成檔、私有作用域及 getter／狀態隔離回歸。瀏覽器實際檢查 Alpine、跨季搜尋、競態、錯誤恢復、來源顯示、分享及 CSP。

Ruff、format、node syntax、actionlint、shellcheck、pip check、BUILD_ONLY 建置、validate-all 及資料／cache 基準比對通過。最後整合補強只涉及證據快照，未再更動已通過瀏覽器驗證的執行邏輯。PR／原生 Windows／預覽／正式工作與正式網站實際結果，於合併後記在本批 PR。

限制：歷史查證方法或日期沒有記錄仍維持未知；品質數量不是正確性保證。這次沒有進行新一輪來源查證、補簡介、觸發人工審查通知或修改 Cloudinary；缺漏及既有待辦仍按證據處理。
