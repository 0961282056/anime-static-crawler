# AniList 明示連結補齊（2026-10-24）

接續最新 main `451cc0cdb7ae125c4c9161fceb3f4fc42985adc1`。以新取得的 bangumi-data 社群對照補入 **1,041 筆季度記錄、935 個精確卡片身分、35 季**，AniList 缺漏由 1,378 降為 **337**。原有 772 筆連結保留，共 1,813 筆。全站維持 37 季、2,150 筆，簡介／播出方式／Bangumi 缺漏仍為 98／0／1。

## 來源、授權與可重現位置

- 來源：[bangumi-data 固定版本](https://github.com/bangumi-data/bangumi-data/blob/94ea82be75d9e617c7970bb0fd0670ae5c60e53d/dist/data.json)，commit `94ea82be75d9e617c7970bb0fd0670ae5c60e53d`，該提交時間為臺北 2026-10-24 08:00:10；不表示每筆作品於當日更新。
- 實際取得的原始 JSON：7,906,809 bytes，SHA256 `c21466156d528eea24f2b32c7b36c9ec11273801037efdc698eccf3a475a893f`，包含 8,925 筆來源記錄。
- [執行規則及逐筆證據](../../services/reviewed_external_links.json)保存取得時間、原始文件雜湊、`source_item_index`、來源類型及首播時間。以 `/items/{source_item_index}` 可回查同筆 `title`、`sites` 中的 `bangumi`／`aniList` 明示 ID。`original_name` 為逐字相同的來源 `title`，沒有改寫或模糊配對。
- [固定版本授權聲明](https://github.com/bangumi-data/bangumi-data/blob/94ea82be75d9e617c7970bb0fd0670ae5c60e53d/README.md#license)：bangumi-data contributors，[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。本站只採用經作品及季度篩選的連結對應，網站頁尾提供來源、固定版本、授權與變更說明。

## 採用條件與範圍防護

既有 Bangumi ID 與來源明示 ID 相同，原文名逐字相同；同筆 TV／web 來源必須明列唯一 AniList ID，且完整來源快照中兩個 ID 都只屬於一筆記錄。來源首播不得晚於卡片所在季度；也不得與公開資料已有的任何相關 ID 對應衝突。只補空值，不用來源排序、翻譯名、相近名稱或總集數推測身分。

寫入規則精確綁定季度、來源卡片、中文名、原名及 Bangumi ID，沒有擴及未核准季度。後續爬蟲缺連結時保留補入值；明示衝突或卡片改換身分時拒絕寫入並要求新證據。兩筆既有維基簡介（`anime-75`、`anime-1041`）同步登錄新 AniList ID，其簡介原文、固定修訂、署名及原查證時間保持原值。

本批只新增參考連結。沒有用社群資料更新簡介、集數、已播進度、播出方式或集數查證時間；魚先生、錢天堂及 PR #66 的口徑保持原值。全 2,150 筆記錄逐欄比對，只允許 1,041 個空 AniList ID 補值；其餘記錄欄位完全相同，35 個季度的 `generated_at` 反映此次資料寫入。

## 保留缺口與差異

[完整剩餘及差異清單](review-status.json)逐筆列出季度、卡片、兩種名稱、現有 ID、來源位置與原因。

| 剩餘原因 | 季度記錄 |
|---|---:|
| 原文名與來源不完全相同，尚需補版本／別名證據 | 300 |
| 來源沒有明示的一組 ID | 21 |
| 同一 Bangumi ID 對應多筆來源 | 7 |
| 同一 AniList ID 對應多筆來源 | 7 |
| 來源首播時間晚於卡片季度 | 2 |
| 合計 | 337 |

兩筆日期差異為 2018 春《兔子馬修》及 2021 冬《舞伎家的料理人》，社群來源分別記在 2018 年 7 月與 2021 年 10 月，需再核對首播／重播、TV／網路版範圍。原文名差異含標點與季數寫法，也有版本副標題差異；這批全部保留空值，後續需明確別名或版本證據，不能只以字串相似度放行。

另觀察到 **11 筆既有季度連結**與社群對照不同，包含《艾梅洛閣下II世事件簿》等，另列於清單的 `existing_disagreements`。這是待查證差異，尚未判定哪個來源正確，不以社群資料批次覆寫原連結。

## 存取與查證界線

本次環境 revision 93、observed 93、`observations_current=true`、network `enforced`；中文維基百科已在有效允許清單，但一次實際 `exturlusage` API 請求仍為 HTTP 403，沒有取得該次正文，也沒有換代理、工具或路徑重試。沒有可呼叫的環境修改／發布工具，沒有宣稱修改設定。

bangumi-data 由允許的 GitHub／raw.githubusercontent.com 正常取得，是獨立的社群資料來源。AniList API 維持退役，本批沒有送出 AniList 請求，連結對應不代表已重新確認 AniList 的現站內容、可用性或集數計數範圍。沒有讀取 Wikidata，亦未把它列為已查證來源。

測試、PR、合併及正式站版本驗收結果集中記錄於本批 PR；不手動觸發排程、測試通知或 Cloudinary 操作。
