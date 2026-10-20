# 依賴與 Actions 維護

本頁記錄更新機制、目前載入的前端依賴與升級檢查。安裝與測試命令見[開發與測試](開發與測試.md)，CSP 重構另依[改善計畫](CSP與跨瀏覽器改善計畫.md)。盤點日期：2026-10-20，承接 PR #75。

## Windows 與 Linux 都是合併條件

`ci.yml` 的 `linux-quality` 執行完整 Linux 品質檢查，`windows-static` 實測 Windows 檔案替換、ACL 繼承與回復。既有 required context `quality` 等待兩者，透過 `if: always()` 明確檢查結果；任一 failure、cancelled、skipped 或缺少結果都失敗，只有兩者 success 才繼續。

`quality` 另下載同次 Linux 工作上傳的公開 `release.json`，驗證 artifact digest、巢狀目錄解壓縮及 commit。這項實測不使用正式服務憑證、不執行爬蟲，也不操作 Cloudinary。

GitHub main ruleset 仍要求 `quality`。直接新增 `Windows static build` required context 的 REST 寫入回覆 403（Resource not accessible by integration），沒有宣稱控制台規則已更新；Windows 透過既有必要 `quality` 成為合併條件。嚴格要求分支最新、PR、禁止刪除／force-push 及空 bypass list 保持原規則。

## 自動更新涵蓋範圍

[dependabot.yml](../.github/dependabot.yml) 設定每週二臺北時間 09:00 檢查；實際啟動可能延遲。

| 類別 | 追蹤檔案 | 同時開啟的版本更新 PR 上限 |
|---|---|---:|
| Python／pip | 根目錄 `.in` 與 pip-compile 產生的四份含 hash `.txt` | 3 |
| npm | `package.json`／`package-lock.json`，目前為 Playwright 測試工具 | 2 |
| GitHub Actions | 工作流程中的固定 SHA 引用 | 2 |

Actions 的 minor／patch 更新可分組；major 更新分開審查。沒有設定 Dependabot 自動合併或規則 bypass，也不提供 crawler／Cloudinary 憑證給依賴 PR。版本更新 PR 上限不代表安全更新 PR 的總上限。

Dependabot 原生支援 pip-compile 的 `.in` 與編譯輸出，實作可追查 [PipCompileFileUpdater](https://github.com/dependabot/dependabot-core/blob/main/python/lib/dependabot/python/file_updater/pip_compile_file_updater.rb)。CI 仍重編四份 lock 並比對，因此提案若漏更新相依 lock、Python 版本或編譯選項不符，不能直接合併；須修正產物並重新通過檢查。

設定合併至預設分支後由 GitHub 讀取；首次實際 Dependabot 執行與更新 PR 仍待觀察，不將 schema 驗證當作遠端更新已完成。安全告警、平台端更新設定及實際可用性，亦須以 GitHub 觀察為準。

## Actions 版本與相容性

| Action | 固定版本 | 執行環境 |
|---|---|---|
| `actions/checkout` | 7.0.0 | Node 24 |
| `actions/setup-python` | 6.3.0 | Node 24 |
| `actions/create-github-app-token` | 3.2.0 | Node 24 |
| `actions/upload-artifact` | 7.0.2 | Node 24 |
| `actions/download-artifact` | 8.0.2 | Node 24 |

全部使用完整 40 位 commit SHA，版本註解供閱讀，不使用浮動 tag 執行。上述 runtime 已逐一讀取對應 SHA 的 `action.yml` 核對。

Artifact 升級保留命名、overwrite、保留期限與原有工作分離：

- Upload 明確使用 `archive: true`，維持有名稱的 ZIP；不使用單檔免壓縮模式，避免 `name` 被檔名取代。
- Download 明確使用 `digest-mismatch: error`，依名稱下載當次 workflow 的產物；不設定跨 repository／run token 或模糊 pattern。
- 現有 main SHA、allowlist、artifact provenance 與 retention 的 cache-first／人工核准規則保留。不得以關閉 digest、放寬權限或直接寫 main 解決升級失敗。
- PR／push CI 的跨工作傳輸實測新版上下載；爬蟲 publisher、來源通知及 retention 的真實使用情境，仍分別等待正式事件，不為驗證手動觸發或刪圖。

上游版本說明：[upload-artifact 7.0.2](https://github.com/actions/upload-artifact/releases/tag/v7.0.2)、[download-artifact 8.0.2](https://github.com/actions/download-artifact/releases/tag/v8.0.2)。

## 前端與 CDN 清單

以下資源不在 npm 執行依賴內，Dependabot 的 npm 設定不會替它們更新 URL、SRI 或 vendored 檔案。

| 資源 | 目前版本／用途 | 宣告位置 | 固定與授權 |
|---|---|---|---|
| Bootstrap CSS | 5.1.3，版面 | `templates/base.html`，jsDelivr | 固定版本、SHA-384 SRI；MIT |
| Font Awesome Free CSS／字型 | 5.15.4，圖示 | `templates/base.html`，cdnjs | CSS 固定版本、SHA-384 SRI；程式 MIT、字型 SIL OFL 1.1、圖示 CC BY 4.0，升級需一起核對上游授權 |
| SweetAlert2 | 11.26.25，彈窗與提示 | `templates/base.html`，jsDelivr | 固定版本、SHA-384 SRI；MIT |
| html2canvas | 1.4.1，首次分享時載入 | `frontend/sharing.js`，jsDelivr | 固定版本、SHA-384 SRI；MIT |
| Alpine CSP | 3.15.12，前端互動 | `static/vendor/`，本站提供 | SHA-256、npm tarball integrity 與 MIT 授權見 [ALPINE-LICENSE](../static/vendor/ALPINE-LICENSE.md) |

本次四個外部 CSS／JS URL 實際 HTTP 200，取得內容的 SHA-384 全部與程式宣告一致；此結果只確認當次內容與既有 SRI 相符，不代表它們已是最新版本、沒有漏洞或所有字型檔均已逐檔驗證。Alpine 的保存檔另有固定 SHA-256 測試。

每月及前端重大修改前複查 CDN／Alpine：先看維護狀態、安全公告、授權與相容性，再決定升級。CSP 不使用一般 Alpine build 取代 CSP build；分享、剪貼簿與彈窗更新須有成功及失敗恢復的實測。

## 升級與驗收順序

1. 確認上游版本、來源、授權、breaking changes 與所需 runtime；使用目前 Python 3.11 重新產生含 hash lock，不手改 hash。npm 使用 lock；CDN 同步修改 URL、SRI，vendored 同步更新內容與授權。
2. 修改 `frontend/` 時重新生成 `static/js/main.js`。驗證依賴圖、pip-audit／npm audit、Ruff、工作流程／shell、Python、前端及瀏覽器測試；缺少安全公告不當成安全證明。
3. PR 的 Linux、Windows、彙總 `quality` 與 Cloudflare 預覽通過後合併，正式站版本、公開檔案及瀏覽器驗收再確認。
4. 區分本地測試、PR、push、schedule、Dependabot 更新與正式維運案例。未實際觀察者保留待驗證，不發測試通知或動用正式圖片補足紀錄。
