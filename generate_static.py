import json
import os
from datetime import datetime
from jinja2 import Environment, FileSystemLoader
from config import Config 
import sentry_sdk 

# --- 初始化 Sentry ---
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

    from services.anime_service import fetch_anime_data 

    try:
        anime_list = fetch_anime_data(year, season, None)
    
    except Exception as e:
        current_year = datetime.now().year
        error_msg = str(e)
        
        is_future = int(year) > current_year
        is_network_error = "504" in error_msg or "Max retries exceeded" in error_msg or "404" in error_msg
        
        if is_future and is_network_error:
            print(f"⚠️ [容錯跳過] 未來季度 {year} {season} 網站尚未準備好或回應超時。")
            print(f"   錯誤訊息: {error_msg[:100]}...") 
            return 
        else:
            print(f"❌ [嚴重錯誤] 爬取 {year} {season} 失敗！")
            raise e 

    if not anime_list:
        print(f"⚠️ 爬蟲回傳空資料：{year} {season}")
    
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

    # 定義季度陣列與計算當前絕對季度
    seasons_order = ['冬', '春', '夏', '秋']
    current_season_idx = (now.month - 1) // 3 # 0:冬, 1:春, 2:夏, 3:秋
    absolute_current_q = current_year * 4 + current_season_idx

    targets = [] # 存放要爬取的 (year_str, season) 列表

    if not json_files_exist and not is_build_only:
        print(f"⚠️ 資料目錄為空。將從 {START_YEAR_ON_EMPTY} 年開始全量爬取。")
        # 全量模式：從初始年份的第一季，一直抓到未來 1 季
        start_q = START_YEAR_ON_EMPTY * 4 
        end_q = absolute_current_q + 1
    else:
        if not is_build_only:
             print("✅ 執行精準增量爬取 (過去 1 年至未來 1 季)。")
        # 增量模式：過去 4 個季度 (1 年) 到未來 1 個季度
        start_q = absolute_current_q - 4 
        end_q = absolute_current_q + 1   

    # 將連續整數還原為年份與季節
    for q in range(start_q, end_q + 1):
        target_year = str(q // 4)
        target_season = seasons_order[q % 4]
        targets.append((target_year, target_season))

    # 執行爬蟲迴圈
    for year_str, season in targets:
        start_month_val = Config.SEASON_TO_MONTH[season]
        year_int = int(year_str)
        
        # 判斷是否為歷史季度 (當下時間已超過該季度的起始月份)
        is_historical_quarter = not (
            year_int > current_year or
            (year_int == current_year and now.month < start_month_val)
        )
        
        json_output_path = os.path.join(JSON_DIR, f'{year_str}_{season}.json')
        
        # 效能優化：若是已結束的歷史季度，且檔案已存在，則不發起網路請求
        if is_historical_quarter and os.path.exists(json_output_path) and not is_build_only:
            continue
            
        generate_quarterly_data(year_str, season, is_build_only=is_build_only) 

    # 生成 HTML 索引
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
    season_order_map = {'冬': 1, '春': 2, '夏': 3, '秋': 4}
    for year in available_data:
        available_data[year].sort(key=lambda s: season_order_map.get(s, 99))

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