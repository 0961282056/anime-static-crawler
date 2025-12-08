import os
import logging
import json
import re  # 確保導入，用於解析 Cloudinary URL
from datetime import datetime, timedelta
import cloudinary.api
from dotenv import load_dotenv
from config import Config  # 導入 Config 以使用季度月份對應

# ------------------------------------------------------
# 初始化與設定
# ------------------------------------------------------
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 設定快取檔案路徑和資料目錄
CACHE_FILE = os.path.join(os.getcwd(), 'cloudinary_cache.json')
JSON_DIR = os.path.join(os.getcwd(), 'dist', 'data')

# 設置 Cloudinary 連線
try:
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
        secure=True
    )
except Exception as e:
    logger.error(f"Cloudinary 配置失敗: {e}. 請檢查 CLOUDINARY_* 變數。")
    pass 

def load_local_cache() -> dict:
    """載入本地的 Cloudinary 快取檔案。"""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                content = json.load(f)
                # 確保回傳的是字典，如果檔案是空的或是空列表，回傳空字典
                return content if isinstance(content, dict) else {}
        except json.JSONDecodeError:
            logger.warning(f"快取檔案 {CACHE_FILE} 格式錯誤或為空，視為空快取。")
            return {}
    return {}

def save_local_cache(cache_data: dict):
    """儲存本地的 Cloudinary 快取檔案。"""
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"無法儲存快取檔案: {CACHE_FILE}。錯誤: {e}")

def get_quarters_to_keep(years_to_keep: int) -> set:
    """計算出最近 n 年內 (包含當前) 需要保留的季度 (Year, Season) 集合。"""
    now = datetime.now()
    quarters_to_keep = set()
    
    # 遍歷從 n 年前到現在的所有月份
    start_date = now - timedelta(days=365 * years_to_keep)
    
    current_year = start_date.year
    current_month = start_date.month

    while current_year <= now.year or (current_year == now.year and current_month <= now.month):
        # 確保 season_month 是 1, 4, 7, 10
        season_month = (current_month - 1) // 3 * 3 + 1
        
        # 根據月份計算季度名稱
        season_name = next((k for k, v in Config.SEASON_TO_MONTH.items() if v == season_month), '未知')
        
        if season_name != '未知':
            quarters_to_keep.add((str(current_year), season_name))

        # 進到下一個季度
        current_month += 3
        if current_month > 12:
            current_month -= 12
            current_year += 1
            
    # 將未來一季也納入保留範圍
    current_quarter_start_month = (now.month - 1) // 3 * 3 + 1
    next_month = current_quarter_start_month + 3
        
    if next_month > 12:
        next_year = now.year + 1
        next_month -= 12
    else:
        next_year = now.year
        
    next_season_name = next((k for k, v in Config.SEASON_TO_MONTH.items() if v == next_month), None)

    if next_season_name:
         quarters_to_keep.add((str(next_year), next_season_name))

    return quarters_to_keep


def cleanup_cloudinary_resources(years_to_keep: int = 15, folder_prefix: str = "anime_covers/") -> int:
    """
    1. 檢查本地快取，若為空則進入「全面重置模式」。
    2. 若不為空，則建立白名單，僅保留最近 n 年的圖片。
    3. 遍歷 Cloudinary 刪除不需要的圖片。
    """
    
    if not os.getenv("CLOUDINARY_API_KEY"):
        logger.warning("Cloudinary API 憑證缺失，跳過圖片清理與快取同步。")
        return 0

    # ------------------------------------------------------
    # 步驟 0: 檢查快取狀態 (決定是否全數刪除)
    # ------------------------------------------------------
    local_cache = load_local_cache()
    public_ids_to_keep = set()
    
    # 如果快取為空，觸發全面清理
    if not local_cache:
        logger.warning(f"⚠️ 檢測到本地快取 ({CACHE_FILE}) 為空或不存在。")
        logger.warning(f"🚨 將執行「全面重置」模式：刪除 Cloudinary 上所有 '{folder_prefix}' 下的資源！")
        # public_ids_to_keep 保持為空 set()，這會導致後續步驟刪除所有找到的資源
        
    else:
        # ------------------------------------------------------
        # 步驟 1: 建立 Public ID 白名單 (正常模式)
        # ------------------------------------------------------
        logger.info(f"--- 步驟 1: 建立 Cloudinary 圖片白名單 (保留最近 {years_to_keep} 年的季度資料) ---")
        
        quarters_to_keep = get_quarters_to_keep(years_to_keep)
        
        for year, season in quarters_to_keep:
            json_filename = f'{year}_{season}.json'
            json_path = os.path.join(JSON_DIR, json_filename)
            
            if os.path.exists(json_path):
                try:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        for anime in data.get('anime_list', []):
                            url = anime.get('anime_image_url')
                            if url and 'cloudinary.com' in url:
                                # 從 Cloudinary URL 中解析出 Public ID
                                match = re.search(r'v\d+/({})/([\w]+)'.format(folder_prefix.strip('/')), url)
                                if match:
                                    public_id = f"{match.group(1)}/{match.group(2)}"
                                    public_ids_to_keep.add(public_id)
                                
                except Exception as e:
                    logger.error(f"讀取或解析 JSON 檔案失敗: {json_path}. 錯誤: {e}")

        logger.info(f"白名單建立完成。共 {len(public_ids_to_keep)} 筆圖片 (來自 {len(quarters_to_keep)} 個季度) 將被保留。")


    # ------------------------------------------------------
    # 步驟 2: 遍歷 Cloudinary 資源並刪除不在白名單的
    # ------------------------------------------------------
    total_deleted_cloud = 0
    
    logger.info("--- 步驟 2: 遍歷雲端資源並刪除白名單以外的圖片 ---")

    public_ids_to_delete_all = [] 
    next_cursor = None
    
    while True:
        try:
            # 使用 resources() 遍歷所有資源
            resources_result = cloudinary.api.resources(
                type='upload', 
                prefix=folder_prefix, 
                max_results=500,
                next_cursor=next_cursor,
            )
            
            resources_list = resources_result.get('resources', [])
            
            if not resources_list:
                break

            current_batch_to_delete = []
            for res in resources_list:
                public_id = res.get('public_id')
                
                # 如果 Public ID 不在保留列表中 (或是白名單為空)，則標記刪除
                if public_id and public_id not in public_ids_to_keep:
                    current_batch_to_delete.append(public_id)

            if current_batch_to_delete:
                public_ids_to_delete_all.extend(current_batch_to_delete)
                logger.info(f"發現 {len(current_batch_to_delete)} 筆待刪除資源...")

            next_cursor = resources_result.get('next_cursor')
            if not next_cursor:
                break
            
        except Exception as e:
            logger.error(f"Cloudinary 遍歷過程中發生錯誤: {e}")
            break
            
    # 執行批量刪除
    if public_ids_to_delete_all:
        logger.info(f"--- 步驟 2B: 準備刪除總共 {len(public_ids_to_delete_all)} 筆雲端資源 ---")
        
        batch_size = 100
        for i in range(0, len(public_ids_to_delete_all), batch_size):
            batch = public_ids_to_delete_all[i:i + batch_size]
            try:
                # 批量刪除
                delete_result = cloudinary.api.delete_resources(
                    batch, 
                    resource_type="image", 
                    type="upload"
                )
                
                deleted_count = len(delete_result.get('deleted', {})) 
                
                total_deleted_cloud += deleted_count
                logger.info(f"成功刪除一批 {deleted_count} 筆雲端資源。")
            except Exception as e:
                logger.error(f"批量刪除時發生錯誤 (批次 {i//batch_size + 1}): {e}")
                
    logger.info(f"--- 雲端清理完成。總共刪除 {total_deleted_cloud} 筆雲端資源。---")

    # ------------------------------------------------------
    # 步驟 3: 同步清理本地快取 (刪除已刪除 Public ID 的快取記錄)
    # ------------------------------------------------------
    
    # 如果是全面重置模式 (local_cache 為空)，這一步其實沒什麼好刪的，但邏輯通用
    if public_ids_to_delete_all:
        deleted_public_ids_for_cache = set(f"cloudinary_{pid.split('/')[-1]}" for pid in public_ids_to_delete_all)
        total_deleted_cache = 0
        
        # 重新讀取一次快取 (防止在執行過程中快取被其他進程修改，雖然此腳本通常單獨執行)
        local_cache = load_local_cache()
        original_cache_size = len(local_cache)
        
        if local_cache:
            # 找出快取中需要刪除的鍵
            keys_to_delete = set(local_cache.keys()) & deleted_public_ids_for_cache
            
            for key in keys_to_delete:
                del local_cache[key]
                total_deleted_cache += 1
                
            if total_deleted_cache > 0:
                save_local_cache(local_cache)
                logger.info(f"成功同步快取。從 {original_cache_size} 筆記錄中移除 {total_deleted_cache} 筆已刪除的圖片快取。")
            else:
                logger.info("本地快取中沒有找到需要移除的舊記錄。")
            
    logger.info(f"--- Cloudinary 圖片清理與快取同步作業全部完成。---")
    return total_deleted_cloud

if __name__ == '__main__':
    # 執行清理，預設保留 15 年
    cleanup_cloudinary_resources(years_to_keep=15)