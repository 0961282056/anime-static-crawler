import json
import os
from datetime import datetime

# 導入 Config 以使用 SEASON_TO_MONTH 進行月份比較
from config import Config 
from services.anime_service import fetch_anime_data, get_current_season 

from jinja2 import Environment, FileSystemLoader

# --- 設定 ---
OUTPUT_DIR = 'dist'
JSON_DIR = os.path.join(OUTPUT_DIR, 'data')
START_YEAR_ON_EMPTY = 2018 

def generate_quarterly_data(year, season, is_build_only=False):
    """爬取單一季度資料，生成 JSON 檔案"""
    json_filename = f'{year}_{season}.json'
    json_output_path = os.path.join(JSON_DIR, json_filename)

    # Build Only 模式：只檢查檔案，不爬蟲
    if is_build_only:
        if os.path.exists(json_output_path):
            print(f"🏗️ [Build Only] 載入現有資料：{year} {season}")
        else:
            print(f"⚠️ [Build Only] 缺少資料且跳過爬蟲：{year} {season}")
        return

    print(f"--- 開始爬取 {year} 年 {season} 季資料 ---")
    anime_list = fetch_anime_data(year, season, None) 

    # 關鍵：如果沒資料，就不存檔！這樣後續掃描時就不會出現這個季度
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
    now = datetime.now()
    current_year = now.year
    
    os.makedirs(JSON_DIR, exist_ok=True)
    
    # 決定爬取年份範圍
    json_files_exist = os.path.exists(JSON_DIR) and any(f.endswith('.json') for f in os.listdir(JSON_DIR))
    if not json_files_exist and not is_build_only:
        years_range = list(range(START_YEAR_ON_EMPTY, current_year + 2))
    else:
        years_range = list(range(current_year - 2, current_year + 2))

    # 執行爬蟲迴圈
    for year in years_range:
        year_str = str(year)
        for season, start_month_val in Config.SEASON_TO_MONTH.items():
            is_historical_quarter = not (year > current_year or (year == current_year and now.month < start_month_val))
            json_output_path = os.path.join(JSON_DIR, f'{year_str}_{season}.json')
            
            if is_historical_quarter and os.path.exists(json_output_path) and not is_build_only:
                continue
            
            if is_historical_quarter or year > current_year or (year == current_year and now.month >= start_month_val):
                generate_quarterly_data(year_str, season, is_build_only=is_build_only) 

    # =======================================================
    # 【關鍵功能】: 掃描 dist/data 目錄，找出真正存在的檔案
    # =======================================================
    available_data = {} # 結構: { "2026": ["冬", "春"], "2025": ["冬", "春", "夏", "秋"] }
    
    if os.path.exists(JSON_DIR):
        for filename in os.listdir(JSON_DIR):
            if filename.endswith(".json") and "_" in filename:
                try:
                    # 解析檔名: 2026_冬.json -> year=2026, season=冬
                    name_part = filename.replace(".json", "")
                    year_part, season_part = name_part.split('_')
                    
                    if year_part not in available_data:
                        available_data[year_part] = []
                    available_data[year_part].append(season_part)
                except ValueError:
                    continue

    # 資料排序 (年: 倒序, 季: 冬春夏秋)
    sorted_years = sorted(available_data.keys(), key=int, reverse=True)
    season_order = {'冬': 1, '春': 2, '夏': 3, '秋': 4}
    for year in available_data:
        available_data[year].sort(key=lambda s: season_order.get(s, 99))

    # 決定預設選中值 (優先當前，若無則選最新的)
    default_year = str(now.year)
    default_season = get_current_season(now.month)
    
    if sorted_years:
        if default_year not in available_data:
            default_year = sorted_years[0]
            default_season = available_data[default_year][0]
        elif default_season not in available_data[default_year]:
            # 若該年有資料但該季沒有，選該年第一季
            default_season = available_data[default_year][0]

    # 生成 HTML
    file_loader = FileSystemLoader('templates') 
    env = Environment(loader=file_loader)
    template = env.get_template('index.html') 
    
    output_html = template.render(
        selected_year=default_year,
        selected_season=default_season,
        years=sorted_years,
        # 將整理好的資料轉成 JSON 字串傳給 JS
        available_data_json=json.dumps(available_data, ensure_ascii=False),
        available_data=available_data 
    )
    
    with open(os.path.join(OUTPUT_DIR, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(output_html)
    
    print("✅ 成功生成 index.html 靜態檔案。")

if __name__ == '__main__':
    generate_static_files()