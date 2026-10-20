# 動畫新番資訊站

這是一個「定時爬取動畫資料、保存封面、產生靜態網站」的專案。正式網站由 Cloudflare Pages 發布；GitHub Actions 負責每天更新資料；Cloudinary 保存封面圖片。

維護請從[文件入口](docs/README.md)開始：查看最新統計與發布基準、[有效待辦](docs/後續工作順序.md)，以及[已安排的CSP／跨瀏覽器改善](docs/CSP與跨瀏覽器改善計畫.md)。日期報告與舊CSV保留為查證快照，不能代替目前進度。

## 現在的架構

```text
acgsecrets.hk
      │
      ▼
GitHub Actions 定時爬蟲
      ├── 驗證資料契約與品質
      ├── 封面上傳到 Cloudinary
      ├── 原子寫入 dist/data/*.json
      └── 只有資料改變時才建立 automation Pull Request
                    │
                    ▼
Quality Gate 全綠後由 GitHub App auto-merge 到 main
                    │
                    ▼
Cloudflare Pages 執行 bash build.sh
      ├── 驗證所有季度 JSON
      ├── templates + static 產生 dist
      └── 發布靜態網站
```

重要安全原則：

- 爬蟲不會為了 Cloudinary 額度自動刪除季度 JSON 或圖片。
- Cloudinary 清理預設只產生 dry-run manifest；程式硬性要求圖片至少 30 天、manifest 再等待至少 30 天，且單次最多 50 張／總資產 2%。資產 ID 只接受專案既有的 32 位 MD5 與新版 64 位 SHA-256 小寫十六進位格式，不接受任意名稱或子目錄。正式流程先讓 cache-only PR 通過 Quality Gate 並合併，確認 `main` 已不再快取候選 URL，才由 required reviewer 與 crawler 共用鎖保護的 job 執行刪除。
- `.env`、密碼、API secret、Webhook 不可提交到 Git。
- crawler 與 retention 都不得直接推送 `main`。不持有正式服務憑證的 publisher 只能透過 `automation-publisher` environment 取得限本儲存庫使用的 GitHub App token，建立 Pull Request；`Quality Gate / quality` 成功後才由 GitHub auto-merge，GitHub Actions 與該 App 都不可加入 ruleset bypass。
- 正式 GitHub crawler（每日排程與人工 Run workflow）必須在 `crawler-production` 設定可用的 `DISCORD_WEBHOOK_URL`；本機執行可不設定 Discord。
- 每日台灣時間 09:15 另有唯讀 selector canary：只抓取當季來源 HTML、驗證所有卡片可解析且 ID 不重複，不寫 JSON／cache、不呼叫 Cloudinary；只有失敗才使用 `crawler-production` 的 Discord webhook 告警。
- 初次部署或輪替期間，`CRAWLER_SCHEDULE_ENABLED` 保持 `false`；手動驗證成功後才開啟每日排程。這個開關只有新版 crawler workflow 合併到 `main` 後才有效。
- 執行 Cloudinary retention 前必須先把 `CRAWLER_SCHEDULE_ENABLED` 設為 `false`，並確認沒有 crawler 或任何以 `main` 為目標的 Pull Request；cache safety PR 必須在刪圖前先合併，整個 workflow 成功後才能恢復排程。
- `static/` 是前端資源的唯一來源；`dist/static/` 由建置自動產生。
- Windows 若暫時鎖住 `dist/static`，建置會以有限次 exponential backoff 重試；重試耗盡仍會保留原輸出並安全失敗，不會無限等待或略過錯誤。
- Alpine 使用本站託管、固定雜湊的 CSP build；正式 CSP 不允許 `unsafe-eval`。目前仍因分享圖片的動態樣式保留 `style-src 'unsafe-inline'`，移除前必須先完成該功能的樣式重構與瀏覽器回歸。
- 季度切換只允許最新請求更新畫面；記憶體快取保留完整季度資料與更新時間，5 分鐘後重新驗證。瀏覽器儲存不可用時仍可查詢與篩選。
- 封面列表使用 300／600／900 寬度的 Cloudinary 圖片變體，保留原始資產；分享圖片使用較高解析度，`html2canvas` 在第一次分享時才載入。
- 爬蟲在正式抓取前執行依賴安全稽核。資料 PR 的 required check 若確定失敗／取消，publisher 會提早結束並列出檢查連結；尚未出現的檢查與暫時 API 錯誤仍有限等待，只有 PR 已合併才算發佈成功。
- 發佈前的爬蟲錯誤、資料契約錯誤或品質 gate 失敗，都會讓工作流程失敗並保留 `main` 上一版資料。資料 PR 已通過 gate 並合併後，後續 Discord 傳送失敗仍會讓 workflow 紅燈，但不會回滾已合併資料。
- JSON schema 會驗證 `bangumi_id`、星期、`HH:MM`（允許動畫排程使用 24–29 時）、來源季度 URL，以及 Cloudinary `anime_covers/<32 或 64 位雜湊>` 路徑；格式不符時在原子寫入前停止。
- `premiere_date` 是播出星期，另支援 `一至五` 與 `不定期公開`；後者與 `無首播日期`（尚未確認）不同。平日篩選包含週一至五連播；不同播出／合集口徑的說明由 `services/reviewed_broadcast_sources.json` 精確綁定並產生前端設定，更新說明也會更新應用版本。查證見 [三筆播出範圍](docs/broadcast-scopes-2026-10-19/README.md)。
- 集數更新每天優先處理連載、未首播、當季未知及最近七天完結作品；跨季同作品以最新收錄季度判定，歷史長篇連載仍每日優先。歷史未知每 30 天、歷史完結每 90 天重查，以固定日期分散首次到期；歷史來源失敗每七天重試。錢天堂由 NHK 完整目錄每七天自動核對，失敗隔一天重試；其餘人工官方快照維持原確認時間。
- 每輪集數更新最多 240 次請求、約十分鐘請求時間預算；有歷史工作時保留最多 64 次請求及四分之一剩餘時間，避免任一類長期排不到。未使用的歷史額度可繼續處理每日工作。來源變慢或失敗仍可能延後，不能把每日排程當作每天全部更新的保證。
- `python manage.py quality-report` 與爬取記錄會列出每日優先／歷史逾期數、最早確認時間及每日優先資料超過 48 小時未確認的數量；失敗嘗試不會刷新確認時間。正式發布驗收見[七筆補齊與更新容量驗證](docs/發布完成驗證-2026-10-16.md)。

## 部署與歷史維護

初次部署、從舊版升級、停排程及平台設定請依[部署與平台設定手冊](docs/部署與平台設定.md)操作。舊版不一定支援排程開關，應使用手冊中的完整停用流程；不要將歷史升級步驟當作目前仍未完成的工作。

歷史`未知ID`回填已完成；不要在另一台電腦或分支重跑一次性回填。當前資料統計集中於[文件入口](docs/README.md)，歷史數量保留在當時的報告中。

## 專案主要檔案

| 路徑 | 用途 |
|---|---|
| `generate_static.py` | 執行爬蟲、驗證資料並建置靜態網站 |
| `manage.py` | 資料／靜態輸出驗證、selector canary 與安全通知命令 |
| `services/parser.py` | 解析來源網站 HTML |
| `services/selector_canary.py` | 每日唯讀來源 selector／parser 契約檢查 |
| `services/data_repository.py` | JSON schema、品質 gate 與原子寫入 |
| `services/image_store.py` | 安全下載圖片並上傳 Cloudinary |
| `services/retention.py` | 只刪除全站未引用圖片的保留政策 |
| `cloudinary_cleaner.py` | 人工 dry-run／執行 retention 的命令列工具 |
| `backfill_ids.py` | 一次性修復歷史 `未知ID`；預設 dry-run |
| `templates/` | Jinja2 HTML 來源 |
| `static/` | CSS、JavaScript 的唯一來源 |
| `dist/data/` | Git 追蹤的季度資料 |
| `build.sh` | Cloudflare Pages 唯一正式建置入口 |
| `_headers` | Cloudflare Pages 安全標頭與快取規則 |
| `.github/workflows/selector-canary.yml` | 每日唯讀來源檢查；只有失敗才通知 Discord |
| `.env.example` | 可公開的環境變數範本，不含任何真實值 |

## 五分鐘本機檢查

需求：Git、Python 3.11。Windows 建議使用 PowerShell；執行 `build.sh` 時需要 Git Bash 或 WSL。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r requirements-dev.txt
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

前端行為回歸與瀏覽器檢查（Node.js 20 以上）：

```powershell
npm ci
npm run test:frontend
npx playwright install chromium
# 先在另一個終端機執行 BUILD_ONLY 建置，再啟動本機測試服務：
python -m http.server 4173 --bind 127.0.0.1 --directory dist
# 回到原終端機：
npm run test:e2e
```

瀏覽器回歸涵蓋來源資料載入、篩選、封面，以及快速季度切換、快取與儲存失敗的處理。測試用服務只監聽本機，不執行爬蟲或 Cloudinary 上傳。

只建置網站、不連線爬蟲或 Cloudinary：

```powershell
$env:BUILD_ONLY = "true"
python generate_static.py
python manage.py validate-all
Remove-Item Env:BUILD_ONLY
```

成功時，網站輸出會在 `dist/`。這個模式不需要 `.env`；本機需要執行完整爬蟲時，Discord webhook 仍是選用設定。

若 PowerShell 阻擋虛擬環境啟用，不必改全機安全政策，可以直接執行：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## 文件索引

完整導覽見[文件入口](docs/README.md)，接續改善見[後續工作順序](docs/後續工作順序.md)。已完成的工作移至歷史紀錄，CSP及跨瀏覽器工作目前列為後續計畫。

1. [部署與平台設定手冊](docs/部署與平台設定.md)：本機安裝、GitHub Environment／Secrets、Cloudflare Pages、Discord及上線驗收。
2. [安全維運與災難復原手冊](docs/安全維運與災難復原.md)：憑證、保留政策、備份與復原。
3. [集數與播出狀態](docs/集數與播出狀態.md)：已播／總集數、播畢判斷、來源與失敗保留。
4. [CSP與跨瀏覽器改善計畫](docs/CSP與跨瀏覽器改善計畫.md)：前置重構、實作順序與完成標準，尚未實作。

已完成的進度、架構基準與來源查證報告由[歷史索引](docs/README.md#歷史與查證紀錄)導覽；每份快照依當時版本閱讀，不當作即時待辦。

不要直接從 retention 或 Git 歷史重寫開始。正確順序是：先讓舊秘密失效、完成一般部署並確認穩定，再另外安排高風險維護窗口。

## 常用命令

```text
python manage.py validate-data   驗證全部季度 JSON
python manage.py verify-dist     比對 static 與 dist/static
python manage.py validate-all    同時執行上述檢查
python generate_static.py        使用 .env 執行爬蟲並建置
python backfill_ids.py           檢查歷史 ID backfill；預設不寫檔
bash build.sh                    Cloudflare 的正式 build-only 建置
python cloudinary_cleaner.py ... Cloudinary retention；預設 dry-run
```

## 發生問題時

爬蟲與驗證命令現在會輸出「錯誤代碼、執行階段、原因、位置、處理建議」，GitHub Actions 也會附上失敗摘要。已知錯誤預設不列出冗長 traceback；需要追查時可使用 `LOG_LEVEL=DEBUG`，未預期錯誤仍會提供經敏感值遮蔽的技術詳細資料。詳見[錯誤診斷與重試](docs/錯誤診斷與重試.md)。

網頁載入失敗會顯示繁體中文說明與「重新載入」按鈕。同季度更新失敗會明確標示保留的舊資料；切換季度失敗不會顯示上一季度的資料。瀏覽器儲存偏好失敗不影響主要查詢功能。

- GitHub Action 變紅：先不要重新執行很多次，確認錯誤是來源網站、資料品質、Cloudinary 還是設定問題。
- 網站新版壞掉：先在 Cloudflare Pages 回滾到上一個成功部署，再修 Git。
- JSON 異常：停用排程，從最新 `main` 建復原分支，以 `git revert` 回復最近的自動資料提交，再經 Pull Request 與 required check 合併。
- 懷疑秘密外洩：先在原服務撤銷／輪替，再更新 GitHub；刪除 Git 檔案本身不能讓舊秘密失效。
- 誤刪 Cloudinary 圖片：立即停用爬蟲與清理，從 Cloudinary Backup／Deleted assets 復原。

完整處理方式請看[安全維運與災難復原手冊](docs/安全維運與災難復原.md)。

## 每週資料來源核對

執行 `python manage.py source-audit` 可唯讀核對所有已收錄季度、產生官方人工快照複查清單，AniList 自動 API 核對預設停用，既有明確連結仍保留。報告位於忽略的 `audit-output/`；每週排程在 GitHub Actions 保存摘要與完整 artifact，另以 GitHub Issue 通知需人工複查或維運的問題，相同證據不重複提醒；超額候選存入持久待辦，`C 待查 YYYY-MM-DD` 可指定到期提醒。網站可查看來源方式、成功查證／最近嘗試時間與簡介來源。來源失敗與資料差異分開記錄，不自動覆蓋公開資料。審核回覆方式、規則與限制見[資料來源交叉核對與複查](docs/資料來源交叉核對與複查.md)。
