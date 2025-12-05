# generate_static.py

import json
import os
from datetime import datetime

# 導入 Config 以使用 SEASON_TO_MONTH 進行月份比較
from config import Config 
from services.anime_service import fetch_anime_data, get_current_season 
# from cloudinary_cleaner import cleanup_cloudinary_resources # 【優化】註解掉清理服務，避免部署卡頓

from jinja2 import Environment, FileSystemLoader

# --- 設定 ---
OUTPUT_DIR = 'dist'
JSON_DIR = os.path.join(OUTPUT_DIR, 'data')
START_YEAR_ON_EMPTY = 2018 # 設定資料不足時的起始年份

def generate_quarterly_data(year, season, is_build_only=False):
    """爬取單一季度資料，生成 JSON 檔案"""
    
    json_filename = f'{year}_{season}.json'
    json_output_path = os.path.join(JSON_DIR, json_filename)

    # --- 新增：Build Only 模式邏輯 ---
    if is_build_only:
        if os.path.exists(json_output_path):
            print(f"🏗️ [Build Only] 載入現有資料：{year} {season}")
        else:
            print(f"⚠️ [Build Only] 缺少資料且跳過爬蟲：{year} {season}")
        # Build Only 模式下，直接結束函式，不執行爬蟲
        return
    # --------------------------------

    print(f"--- 開始爬取 {year} 年 {season} 季資料 ---")

    # 執行爬蟲
    anime_list = fetch_anime_data(year, season, None) 

    # 檢查爬蟲結果是否有效
    if not anime_list or ('error' in anime_list[0] if anime_list and isinstance(anime_list[0], dict) else False):
        error_msg = anime_list[0].get('error', '未知錯誤') if anime_list and isinstance(anime_list[0], dict) else '無有效資料'
        print(f"爬蟲失敗或無資料: {error_msg}")
        return
    
    data_to_save = {
        'anime_list': anime_list,
        'generated_at': datetime.now().isoformat()
    }
    
    # 寫入 JSON 檔案
    with open(json_output_path, 'w', encoding='utf-8') as f:
        json.dump(data_to_save, f, ensure_ascii=False, indent=4)
        
    print(f"✅ 成功生成 JSON 檔案：{json_output_path}")


def generate_static_files():
    """主函式：執行清理、爬取所有需要的季度資料並生成靜態檔案"""
    
    # =======================================================
    # 【步驟 A】: Cloudinary 圖片清理 (建議在 Actions 自動化中關閉)
    # =======================================================
    # print("--- 執行 Cloudinary 舊圖片清理 ---")
    # cleanup_cloudinary_resources(years_to_keep=15) 
    
    # =======================================================
    # 【步驟 B】: 爬蟲邏輯與 Build Only 檢查
    # =======================================================
    
    # 檢查環境變數，判斷是否為 Cloudflare 的構建環境
    is_build_only = os.environ.get('BUILD_ONLY', 'false').lower() == 'true'
    
    if is_build_only:
        print("🚀 偵測到 BUILD_ONLY 模式：跳過爬蟲，僅使用現有 JSON 生成 HTML。")
    
    now = datetime.now()
    current_year = now.year
    
    # 確保輸出目錄存在
    os.makedirs(JSON_DIR, exist_ok=True)
    
    # 檢查是否已經有 JSON 檔案
    json_files_exist = os.path.exists(JSON_DIR) and any(f.endswith('.json') for f in os.listdir(JSON_DIR))

    if not json_files_exist and not is_build_only:
        print(f"⚠️ 資料目錄為空。將從 {START_YEAR_ON_EMPTY} 年開始爬取資料。")
        years_range = list(range(START_YEAR_ON_EMPTY, current_year + 2))
    else:
        # 正常/增量模式
        if not is_build_only:
             print("✅ 執行增量爬取 (最近 4 年)。")
        years_range = list(range(current_year - 2, current_year + 2))

    
    # 收集所有目標年/季，用於下拉選單
    years_to_crawl = [] 
    
    # 遍歷所有目標年/季
    for year in years_range:
        year_str = str(year)
        
        for season, start_month_val in Config.SEASON_TO_MONTH.items():
            
            # 判斷邏輯：歷史季度 OR 當前/未來季度
            is_historical_quarter = not (
                year > current_year or
                (year == current_year and now.month < start_month_val)
            )
            
            json_output_path = os.path.join(JSON_DIR, f'{year_str}_{season}.json')
            
            # 加入列表條件
            if is_historical_quarter or year > current_year or (year == current_year and now.month >= start_month_val):
                years_to_crawl.append((year_str, season))
            
            # 跳過邏輯：如果是歷史季度且檔案存在且不是強制爬取，則跳過
            # 但如果是 Build Only 模式，在 generate_quarterly_data 內部會直接 return
            if is_historical_quarter and os.path.exists(json_output_path) and not is_build_only:
                print(f"✅ 跳過爬取歷史資料：{year_str} 年 {season} 季 JSON 檔案已存在。")
                continue
                
            # 傳遞 is_build_only 參數
            generate_quarterly_data(year_str, season, is_build_only=is_build_only) 

    # ------------------------------------
    # HTML 渲染：生成 index.html 
    # ------------------------------------
    
    file_loader = FileSystemLoader('templates') 
    env = Environment(loader=file_loader)
    template = env.get_template('index.html') 
    
    # 準備下拉選單
    unique_years = sorted(list(set(y[0] for y in years_to_crawl)), key=int, reverse=True)
    
    # 預設選單值
    selected_year = str(now.year)
    selected_season = get_current_season(now.month)
    
    # 渲染 HTML
    output_html = template.render(
        sorted_anime_list=[], # 首頁列表可留空或讀取當季資料
        error_message=None,
        selected_year=selected_year,
        selected_season=selected_season,
        years=unique_years,
        seasons=Config.SEASON_TO_MONTH.keys()
    )
    
    with open(os.path.join(OUTPUT_DIR, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(output_html)
    
    print("✅ 成功生成 index.html 靜態檔案。")

if __name__ == '__main__':
    generate_static_files()