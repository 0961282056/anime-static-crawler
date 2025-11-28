# generate_static.py (優化版：只爬取缺失/最新資料)

import json
import os
from datetime import datetime

# 導入 Config 以使用 SEASON_TO_MONTH 進行月份比較
from config import Config 
from services.anime_service import fetch_anime_data, get_current_season 
from cloudinary_cleaner import cleanup_cloudinary_resources # <--- 【新增】導入清理服務

from jinja2 import Environment, FileSystemLoader

# --- 設定 ---
OUTPUT_DIR = 'dist'
JSON_DIR = os.path.join(OUTPUT_DIR, 'data')

def generate_quarterly_data(year, season):
    """爬取單一季度資料，生成 JSON 檔案"""
    
    print(f"--- 開始爬取 {year} 年 {season} 季資料 ---")

    # 假設 fetch_anime_data 已經包含多進程和 Cloudinary 上傳/快取處理
    anime_list = fetch_anime_data(year, season, None) 

    # 檢查爬蟲結果是否有效
    if not anime_list or ('error' in anime_list[0] if anime_list and isinstance(anime_list[0], dict) else False):
        error_msg = anime_list[0].get('error', '未知錯誤') if anime_list and isinstance(anime_list[0], dict) else '無有效資料'
        print(f"爬蟲失敗或無資料: {error_msg}")
        return

    json_filename = f'{year}_{season}.json'
    json_output_path = os.path.join(JSON_DIR, json_filename)
    
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
    # 【新增步驟 A】: 執行 Cloudinary 圖片清理
    # =======================================================
    print("--- 執行 Cloudinary 舊圖片清理（保留約 15 年內資料） ---")
    # 呼叫清理函數，設定 years_to_keep=15
    # 注意：如果您的 Cloudinary API 金鑰在 .env 中未正確設置，此處會跳過清理。
    cleanup_cloudinary_resources(years_to_keep=15) 
    print("--- Cloudinary 清理完成 ---")
    
    # =======================================================
    # 爬蟲邏輯：決定要爬取的年/季 (保留近兩年)
    # =======================================================
    
    now = datetime.now()
    current_year = now.year
    
    # 計算需要爬取的年範圍 (保留 current_year 往前推 2 年的資料)
    years_to_crawl = list(range(current_year - 2, current_year + 2))
    
    # 確保輸出目錄存在
    os.makedirs(JSON_DIR, exist_ok=True)
    
    # 遍歷所有目標年/季
    for year in years_to_crawl:
        year_str = str(year)
        
        # Season mapping: 1-3月=冬, 4-6月=春, 7-9月=夏, 10-12月=秋
        for season, start_month_val in Config.SEASON_TO_MONTH.items():
            
            # 1. 判斷是否為歷史季度
            is_historical_quarter = not (
                year > current_year or
                (year == current_year and now.month < start_month_val)
            )
            
            json_output_path = os.path.join(
                JSON_DIR, 
                f'{year_str}_{season}.json'
            )
            
            # 2. 條件式跳過：如果是歷史季度且 JSON 文件已存在，則跳過爬蟲
            if is_historical_quarter and os.path.exists(json_output_path):
                print(f"✅ 跳過爬取歷史資料：{year_str} 年 {season} 季 JSON 檔案已存在。")
                continue
                
            # 3. 執行爬蟲：包含所有缺失的歷史數據、當前季度、以及所有未來季度
            generate_quarterly_data(year_str, season) 

    # ------------------------------------
    # HTML 渲染：生成 index.html 
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
        years=years_for_dropdown,
        seasons=Config.SEASON_TO_MONTH.keys()
    )
    
    # 寫入最終的 index.html
    with open(os.path.join(OUTPUT_DIR, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(output_html)
    
    print("✅ 成功生成 index.html 靜態檔案。")


if __name__ == '__main__':
    generate_static_files()