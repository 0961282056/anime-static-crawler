import json
import os
from datetime import datetime
from jinja2 import Environment, FileSystemLoader
from config import Config 
import sentry_sdk # 【新增】引用 Sentry

# --- 【新增】初始化 Sentry ---
# 請在 GitHub Secrets 和 Cloudflare 後台設定 SENTRY_DSN 環境變數
if os.getenv("SENTRY_DSN"):
    sentry_sdk.init(
        dsn=os.getenv("SENTRY_DSN"),
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
    )
# ---------------------------

# --- 設定 ---
OUTPUT_DIR = 'dist'
JSON_DIR = os.path.join(OUTPUT_DIR, 'data')
START_YEAR_ON_EMPTY = 2018 

def get_current_season(month: int) -> str:
    if 1 <= month <= 3: return "冬"
    if 4 <= month <= 6: return "春"
    if 7 <= month <= 9: return "夏"
    return "秋"

def generate_quarterly_data(year, season, is_build_only=False):
    """爬取單一季度資料，生成 JSON 檔案"""
    json_filename = f'{year}_{season}.json'
    json_output_path = os.path.join(JSON_DIR, json_filename)

    # --- Build Only 模式 ---
    if is_build_only:
        if os.path.exists(json_output_path):
            print(f"🏗️ [Build Only] 載入現有資料：{year} {season}")
        else:
            print(f"⚠️ [Build Only] 缺少資料且跳過爬蟲：{year} {season}")
        return

    print(f"--- 開始爬取 {year} 年 {season} 季資料 ---")

    # 延遲匯入，避免 Build Only 模式缺套件報錯
    from services.anime_service import fetch_anime_data 

    # 🔥🔥🔥 【關鍵修改區段 Start】 🔥🔥🔥
    try:
        anime_list = fetch_anime_data(year, season, None)
    
    except Exception as e:
        # 判斷是否為「未來年份」的「連線/超時錯誤」
        # 邏輯：如果是今年以後的年份 (如 2027)，且發生 504 或連線失敗，我們視為「正常現象」並跳過
        current_year = datetime.now().year
        error_msg = str(e)
        
        is_future = int(year) > current_year
        is_network_error = "504" in error_msg or "Max retries exceeded" in error_msg or "404" in error_msg
        
        if is_future and is_network_error:
            print(f"⚠️ [容錯跳過] 未來季度 {year} {season} 網站尚未準備好或回應超時。")
            print(f"   錯誤訊息: {error_msg[:100]}...") # 只印出前 100 字避免洗版
            return # 直接結束此函式，不存檔，也不報錯，讓迴圈繼續跑下一個
        else:
            # 如果是「現在」或「過去」的季度失敗，或者不是網路問題，則必須報錯
            print(f"❌ [嚴重錯誤] 爬取 {year} {season} 失敗！")
            raise e # 重新拋出異常，讓 GitHub Action 標記為失敗並通知 Sentry
    # 🔥🔥🔥 【關鍵修改區段 End】 🔥🔥🔥

    # 檢查是否為空列表 (若是空列表則 fetch_anime_data 內部已經發過 Discord 警告了)
    if not anime_list:
        print(f"⚠️ 爬蟲回傳空資料：{year} {season}")
        # 如果您希望空資料不要覆蓋舊檔案，可以在這裡 return
    
    data_to_save = {
        'anime_list': anime_list,
        'generated_at': datetime.now().isoformat()
    }
    
    with open(json_output_path, 'w', encoding='utf-8') as f:
        json.dump(data_to_save, f, ensure_ascii=False, indent=4)
        
    print(f"✅ 成功生成 JSON 檔案：{json_output_path}")


def generate_static_files():
    """主函式"""
    is_build_only = os.environ.get('BUILD_ONLY', 'false').lower() == 'true'
    
    if is_build_only:
        print("🚀 偵測到 BUILD_ONLY 模式：將跳過重型依賴安裝與爬蟲。")
    
    now = datetime.now()
    current_year = now.year
    
    os.makedirs(JSON_DIR, exist_ok=True)
    
    # 決定爬取範圍
    json_files_exist = os.path.exists(JSON_DIR) and any(f.endswith('.json') for f in os.listdir(JSON_DIR))

    if not json_files_exist and not is_build_only:
        print(f"⚠️ 資料目錄為空。將從 {START_YEAR_ON_EMPTY} 年開始爬取資料。")
        years_range = list(range(START_YEAR_ON_EMPTY, current_year + 2))
    else:
        if not is_build_only:
             print("✅ 執行增量爬取 (最近 4 年)。")
        years_range = list(range(current_year - 2, current_year + 2))

    # 執行爬蟲迴圈
    for year in years_range:
        year_str = str(year)
        for season, start_month_val in Config.SEASON_TO_MONTH.items():
            
            is_historical_quarter = not (
                year > current_year or
                (year == current_year and now.month < start_month_val)
            )
            
            json_output_path = os.path.join(JSON_DIR, f'{year_str}_{season}.json')
            
            if is_historical_quarter and os.path.exists(json_output_path) and not is_build_only:
                continue
            
            if is_historical_quarter or year > current_year or (year == current_year and now.month >= start_month_val):
                generate_quarterly_data(year_str, season, is_build_only=is_build_only) 

    # 生成 HTML
    available_data = {} 
    if os.path.exists(JSON_DIR):
        for filename in os.listdir(JSON_DIR):
            if filename.endswith(".json") and "_" in filename:
                try:
                    name_part = filename.replace(".json", "")
                    year_part, season_part = name_part.split('_')
                    if year_part not in available_data: available_data[year_part] = []
                    available_data[year_part].append(season_part)
                except ValueError: continue

    sorted_years = sorted(available_data.keys(), key=int, reverse=True)
    season_order = {'冬': 1, '春': 2, '夏': 3, '秋': 4}
    for year in available_data:
        available_data[year].sort(key=lambda s: season_order.get(s, 99))

    default_year = str(now.year)
    default_season = get_current_season(now.month)
    
    if sorted_years:
        if default_year not in available_data:
            default_year = sorted_years[0]
            default_season = available_data[default_year][0]
        elif default_season not in available_data[default_year]:
            default_season = available_data[default_year][0]

    file_loader = FileSystemLoader('templates') 
    env = Environment(loader=file_loader)
    template = env.get_template('index.html') 
    
    output_html = template.render(
        selected_year=default_year,
        selected_season=default_season,
        years=sorted_years,
        available_data_json=json.dumps(available_data, ensure_ascii=False),
        available_data=available_data 
    )
    
    with open(os.path.join(OUTPUT_DIR, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(output_html)
    
    print("✅ 成功生成 index.html 靜態檔案。")

if __name__ == '__main__':
    generate_static_files()