# 動畫新番資訊站

這是一個「定時爬取動畫資料、保存封面、產生靜態網站」的專案。正式網站由 Cloudflare Pages 發布；GitHub Actions 負責每天更新資料；Cloudinary 保存封面圖片。

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
- 發佈前的爬蟲錯誤、資料契約錯誤或品質 gate 失敗，都會讓工作流程失敗並保留 `main` 上一版資料。資料 PR 已通過 gate 並合併後，後續 Discord 傳送失敗仍會讓 workflow 紅燈，但不會回滾已合併資料。
- JSON schema 會驗證 `bangumi_id`、星期、`HH:MM`（允許動畫排程使用 24–29 時）、來源季度 URL，以及 Cloudinary `anime_covers/<32 或 64 位雜湊>` 路徑；格式不符時在原子寫入前停止。

## 從舊版升級前先停排程

不要只先設定 `CRAWLER_SCHEDULE_ENABLED=false`，因為舊 crawler workflow 不認得這個變數。請先到 GitHub **Actions**：

1. 選擇舊的每日 crawler workflow，按 **Disable workflow**。
2. 取消所有 Running 與 Pending 的 crawler jobs。
3. 建立並推送 last-good tag，再從 `codex/` 開頭的分支送 Pull Request。
4. 等 `Quality Gate / quality` 綠燈並合併新版 workflow 後，才可重新 Enable workflow；先保持 `CRAWLER_SCHEDULE_ENABLED=false` 做人工驗證。

完整的新手 Git 指令、GitHub Ruleset 與平台異動請照[部署與平台設定手冊](docs/部署與平台設定.md)操作。

歷史 ID 修復狀態（2026-07-13）：dry-run 與正式回填均已完成，36 季、共 2,067 筆資料已改用來源真實 ID；嚴格資料驗證通過，`未知ID` 為 0。不要在另一台電腦或另一個分支重複執行一次性回填。

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

1. [目前架構與平台設定現況報告](docs/專案架構與平台設定現況報告-2026-07-14.md)：先用這份了解現在的架構、平台狀態、已知風險與優先順序。
2. [部署與平台設定手冊](docs/部署與平台設定.md)：本機安裝、GitHub Environment／Secrets、Cloudflare Pages、Discord，以及逐步上線驗收。
3. [安全維運與災難復原手冊](docs/安全維運與災難復原.md)：Cloudinary 無停機輪替、歷史帳密與 LINE token、30 天 retention、復原流程及 Git 歷史清理。

已過時的重構前分析報告不再保留在工作樹；需要追查當時的問題基準時，使用 Git history 即可。

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

- GitHub Action 變紅：先不要重新執行很多次，確認錯誤是來源網站、資料品質、Cloudinary 還是設定問題。
- 網站新版壞掉：先在 Cloudflare Pages 回滾到上一個成功部署，再修 Git。
- JSON 異常：停用排程，從最新 `main` 建復原分支，以 `git revert` 回復最近的自動資料提交，再經 Pull Request 與 required check 合併。
- 懷疑秘密外洩：先在原服務撤銷／輪替，再更新 GitHub；刪除 Git 檔案本身不能讓舊秘密失效。
- 誤刪 Cloudinary 圖片：立即停用爬蟲與清理，從 Cloudinary Backup／Deleted assets 復原。

完整處理方式請看[安全維運與災難復原手冊](docs/安全維運與災難復原.md)。
