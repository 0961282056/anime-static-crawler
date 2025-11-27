# generate_static.py (優化版：只爬取缺失/最新資料)

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

def generate_quarterly_data(year, season):
    """爬取單一季度資料，生成 JSON 檔案"""
    
    print(f"--- 開始爬取 {year} 年 {season} 季資料 ---")

    anime_list = fetch_anime_data(year, season, None) 

    if not anime_list or ('error' in anime_list[0] if anime_list and isinstance(anime_list[0], dict) else False):
        error_msg = anime_list[0].get('error', '未知錯誤') if anime_list and isinstance(anime_list[0], dict) else '無有效資料'
        print(f"爬蟲失敗或無資料: {error_msg}")
        return

    json_filename = f'{year}_{season}.json'
    json_output_path = os.path.join(JSON_DIR, json_filename)
    
    data_to_save = {
        'anime_list': anime_list,
        'target_year': year, 
        'target_season': season,
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    with open(json_output_path, 'w', encoding='utf-8') as f:
        json.dump(data_to_save, f, ensure_ascii=False, indent=4)
    print(f"成功生成 JSON 資料: {json_output_path} ({len(anime_list)} 筆)")

def generate_static_files():
    """執行爬蟲並生成靜態 HTML 和多季度 JSON 檔案，跳過已存在的歷史數據。"""
    now = datetime.now()
    os.makedirs(JSON_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 決定要爬取的範圍 (從 2018 年冬季開始到明年)
    current_year = now.year
    current_month = now.month
    
    start_year = 2018 
    end_year = current_year + 1 
    
    years_to_crawl = [str(y) for y in range(start_year, end_year + 1)]
    seasons = ["冬", "春", "夏", "秋"]
    
    # 獲取當前季節對應的數字月份，用於比較（例如：秋季對應 10 月）
    current_season = get_current_season(current_month)
    current_season_month_val = Config.SEASON_TO_MONTH.get(current_season)
    
    # 執行多季度爬蟲
    for year_str in years_to_crawl:
        year = int(year_str)
        
        for season in seasons:
            season_month_val = Config.SEASON_TO_MONTH[season]
            json_filename = f'{year_str}_{season}.json'
            json_output_path = os.path.join(JSON_DIR, json_filename)
            
            # 1. 檢查是否為未來季度 (明年除了冬季外通常不會有資料，所以跳過)
            if year == current_year + 1 and season != "冬":
                continue 
            
            # 2. 判斷是否為「舊的歷史季度」
            is_historical_quarter = (
                year < current_year or
                (year == current_year and season_month_val < current_season_month_val)
            )
            
            # 3. 條件式跳過：如果是歷史季度且 JSON 文件已存在，則跳過爬蟲
            if is_historical_quarter and os.path.exists(json_output_path):
                print(f"✅ 跳過爬取歷史資料：{year_str} 年 {season} 季 JSON 檔案已存在。")
                continue
                
            # 4. 執行爬蟲：包含所有缺失的歷史數據、當前季度、以及所有未來季度
            generate_quarterly_data(year_str, season) 

    # ------------------------------------
    # HTML 渲染：生成 index.html (保持不變)
    # ------------------------------------
    
    file_loader = FileSystemLoader('templates') 
    env = Environment(loader=file_loader)
    
    template = env.get_template('index.html') 
    
    # 準備下拉選單的選項
    years_for_dropdown = sorted(years_to_crawl, key=int, reverse=True)
    
    # 取得當前年/季作為預設選單值
    selected_year = str(now.year)
    selected_season = get_current_season(now.month)
    
    # 渲染 HTML
    output_html = template.render(
        sorted_anime_list=[], 
        error_message=None,
        selected_year=selected_year,
        selected_season=selected_season,
        premiere_date='全部',
        years=years_for_dropdown,
        seasons=seasons,
        premiere_dates=['全部', '日', '一', '二', '三', '四', '五', '六']
    )

    html_output_path = os.path.join(OUTPUT_DIR, 'index.html')
    with open(html_output_path, 'w', encoding='utf-8') as f:
        f.write(output_html)
    print(f"成功生成靜態 HTML 檔案: {html_output_path}")

if __name__ == '__main__':
    generate_static_files()