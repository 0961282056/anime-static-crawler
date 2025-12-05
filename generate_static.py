import json
import os
from datetime import datetime
from jinja2 import Environment, FileSystemLoader
from config import Config 

# --- 設定 ---
OUTPUT_DIR = 'dist'
JSON_DIR = os.path.join(OUTPUT_DIR, 'data')
START_YEAR_ON_EMPTY = 2018 

# 【優化】將依賴 heavy libraries 的 import 移出全域範圍
# from services.anime_service import fetch_anime_data, get_current_season (移除這行)

def get_current_season(month: int) -> str:
    """從 anime_service 搬過來的簡單邏輯，避免依賴"""
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
    # ----------------------

    print(f"--- 開始爬取 {year} 年 {season} 季資料 ---")

    # 【關鍵優化】只有在真正要爬蟲時，才匯入 heavy libraries
    # 這樣 Cloudflare (Build Only 模式) 就不會因為沒安裝 requests/lxml 而報錯
    from services.anime_service import fetch_anime_data 

    anime_list = fetch_anime_data(year, season, None) 

    if not anime_list or ('error' in anime_list[0] if anime_list and isinstance(anime_list[0], dict) else False):
        print(f"爬蟲無有效資料，跳過存檔：{year} {season}")
        return
    
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
            
            # 判斷是否跳過
            if is_historical_quarter and os.path.exists(json_output_path) and not is_build_only:
                # print(f"✅ 跳過爬取歷史資料：{year_str} 年 {season} 季...")
                continue
            
            # 符合條件才執行 (傳遞 is_build_only)
            if is_historical_quarter or year > current_year or (year == current_year and now.month >= start_month_val):
                generate_quarterly_data(year_str, season, is_build_only=is_build_only) 

    # =======================================================
    # HTML 生成邏輯 (這部分依賴 Jinja2，Cloudflare 必須執行)
    # =======================================================
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
