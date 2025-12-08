from typing import List, Dict, Tuple, Optional
from bs4 import BeautifulSoup
import requests, os, json, hashlib, time, re, logging
import multiprocessing
# FIX: 確保導入 cloudinary.api 以供檢查空間使用狀況
import cloudinary, cloudinary.uploader, cloudinary.utils, cloudinary.api 
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime

# 【升級 1】引入 Pydantic 模型
from models import Anime
from config import Config

# ------------------------------------------------------
# 初始化與設定
# ------------------------------------------------------
load_dotenv()
logging.basicConfig(level=logging.INFO) 
logger = logging.getLogger(__name__)
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)

CACHE_FILE = os.path.join(os.getcwd(), 'cloudinary_cache.json')
JSON_DIR = os.path.join(os.getcwd(), 'dist', 'data') # 新增 JSON_DIR 常數

# ------------------------------------------------------
# requests Session & Pool 設定 (略)
# ------------------------------------------------------
pool_size = 5
retry_strategy = Retry(
    total=3, backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504]
)
adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size,
                      max_retries=retry_strategy)

cloudinary_adapter = HTTPAdapter(pool_connections=4, pool_maxsize=4,
                                 max_retries=retry_strategy)

SEASON_TO_MONTH = Config.SEASON_TO_MONTH
WEEKDAY_MAP = Config.WEEKDAY_MAP

# ------------------------------------------------------
# 進程間共享數據 (略)
# ------------------------------------------------------
session_global = None 
cloudinary_config_global = {} 
manager_lock_global = None
manager_cache_global = None

def init_worker(shared_lock, shared_cache_dict):
    """每個進程啟動時初始化資源"""
    global session_global, cloudinary_config_global, manager_lock_global, manager_cache_global
    
    manager_lock_global = shared_lock
    manager_cache_global = shared_cache_dict
    
    session_global = requests.Session()
    session_global.mount("http://", adapter)
    session_global.mount("https://", adapter)
    session_global.mount("https://api.cloudinary.com", cloudinary_adapter)
    
    cloudinary_config_global = {
        'cloud_name': os.getenv("CLOUDINARY_CLOUD_NAME"),
        'api_key': os.getenv("CLOUDINARY_API_KEY"),
        'api_secret': os.getenv("CLOUDINARY_API_SECRET"),
        'long_url_signature': True,
        'secure': True
    }
    cloudinary.config(**cloudinary_config_global)
    cloudinary.config(http_client=session_global)
    
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)

# ------------------------------------------------------
# 簡易快取 (JSON) (略)
# ------------------------------------------------------
def load_local_cache() -> Dict:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"載入快取失敗: {e}")
            return {}
    return {}

def save_local_cache(data: Dict):
    try:
        filtered_data = {k: v for k, v in data.items() if k.startswith('cloudinary_')}
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(filtered_data, f, ensure_ascii=False, indent=4) 
    except Exception as e:
        logger.error(f"儲存快取失敗: {e}")

# ------------------------------------------------------
# 輔助函式 (略)
# ------------------------------------------------------
def parse_date_time(anime: Dict) -> Tuple[int, float]:
    try:
        if anime["premiere_date"] == "無首播日期":
            return 8, float("inf")
        weekday = WEEKDAY_MAP.get(anime["premiere_date"], 7)
        if anime["premiere_time"] == "無首播時間":
            return weekday, 0.0
        match = re.match(r"(\d{1,2}):(\d{2})", anime["premiere_time"])
        if not match:
            raise ValueError
        hour, minute = int(match.group(1)), int(match.group(2))
        return weekday, hour + minute / 60.0
    except Exception:
        return 7, float("inf")

# ------------------------------------------------------
# Discord 通知 (略)
# ------------------------------------------------------
def send_discord_notification(status: str, year: str, season: str, count: int = 0, error_msg: str = ""):
    """發送漂亮的 Embed 通知到 Discord"""
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    
    if not webhook_url: return

    color = 3066993 if status == "SUCCESS" else 15158332
    title = "✅ 動畫爬蟲更新成功" if status == "SUCCESS" else "🚨 動畫爬蟲執行失敗"
    
    description = f"**季度**: {year} {season}\n"
    if status == "SUCCESS":
        description += f"**資料筆數**: {count} 筆\n**狀態**: 已更新至 GitHub & Cloudflare"
    else:
        description += f"**錯誤原因**: {error_msg}\n請檢查 GitHub Actions Logs。"

    payload = {
        "username": "Anime Crawler Bot",
        "avatar_url": "https://cdn-icons-png.flaticon.com/512/4712/4712109.png",
        "embeds": [{
            "title": title,
            "description": description,
            "color": color,
            "footer": {"text": f"執行時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"}
        }]
    }
    
    try:
        requests.post(webhook_url, json=payload, timeout=5)
    except Exception as e:
        logger.error(f"Discord 通知發送失敗: {e}")

# ------------------------------------------------------
# Cloudinary 空間檢查 (修復版：改檢查 Credit 額度)
# ------------------------------------------------------
def check_cloudinary_storage_quota(usage_limit_percent: int = 90) -> bool:
    """檢查 Cloudinary Credit 額度是否超過限制百分比。"""
    try:
        if not cloudinary.config().cloud_name:
            # 確保配置已載入
            cloudinary.config(
                cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
                api_key=os.getenv("CLOUDINARY_API_KEY"),
                api_secret=os.getenv("CLOUDINARY_API_SECRET"),
                secure=True
            )
        
        usage_data = cloudinary.api.usage()
        
        # 🎯 關鍵修復：從 'credits' 欄位獲取使用百分比
        credits_data = usage_data.get('credits', {})
        usage_percent = credits_data.get('used_percent')
        
        # 如果 'used_percent' 不存在，則退回舊的檢查方式（以防萬一）
        if usage_percent is None:
             logger.warning(f"Cloudinary API usage 數據不完整，無法檢查額度。原始回應: {usage_data}")
             return True # 跳過檢查

        logger.info(f"Cloudinary 額度使用率: {usage_percent:.2f}% (限制: {usage_limit_percent}%)")
        
        if usage_percent >= usage_limit_percent:
            logger.error(f"🚨 Cloudinary 額度使用率已達 {usage_percent:.2f}%，已超過限制 ({usage_limit_percent}%)。")
            return False 
        
        return True
        
    except Exception as e:
        logger.error(f"無法檢查 Cloudinary 空間使用狀況: {e}")
        return True

# ------------------------------------------------------
# 圖片處理 (已修正上傳畫質問題)
# ------------------------------------------------------
def upload_to_cloudinary(image_url: str, anime_name: str) -> str:
    """處理圖片上傳或快取命中"""
    if image_url == "無圖片":
        return "無圖片"
    
    session = session_global
    local_cache = manager_cache_global 
    
    try:
        response = session.get(image_url, timeout=6)
        response.raise_for_status()
        
        content_hash = hashlib.md5(response.content).hexdigest()
        public_id = f"anime_covers/{content_hash}"
        cloudinary_key = f"cloudinary_{content_hash}"
        
        # 檢查快取
        with manager_lock_global:
            if cloudinary_key in local_cache:
                return local_cache[cloudinary_key]
        
        # ✅ FIX: 移除 width/height/crop 限制，讓 Cloudinary 上保留原圖畫質
        upload_result = cloudinary.uploader.upload(
            response.content,
            public_id=public_id, overwrite=True, invalidate=True,
            transformation=[
                {"quality": "auto", "fetch_format": "auto"} # 僅優化格式和品質，不限制尺寸
            ]
        )
            
        # 2. 關鍵修改：產生 URL 時，移除尺寸限制
        # 生成的 URL 不包含 width=300, height=450, crop="limit"，
        # 這樣快取和 JSON 中儲存的就是 Cloudinary 上的原圖連結
        url, _ = cloudinary.utils.cloudinary_url(
            upload_result["public_id"],
            fetch_format="auto", 
            quality="auto:best" # 使用 auto:best 確保最高品質
        )
        
        with manager_lock_global:
            local_cache[cloudinary_key] = url
        
        logger.info(f"[UPLOAD] {anime_name} 上傳完成 (原圖保留, WebP優化)")
        return url

    except Exception as e:
        logger.error(f"[ERROR] {anime_name} 圖片處理失敗: {e}")
        return image_url

def worker_process_anime(item_html_str: str) -> Optional[Dict]:
    """Worker: 解析並使用 Pydantic 驗證資料 (略)"""
    try:
        item = BeautifulSoup(item_html_str, "lxml").find("div", class_="CV-search")
        if not item: return None
        
        anime_name_elem = item.find("h3", {"class": "entity_localized_name"})
        anime_name = anime_name_elem.get_text(strip=True) if anime_name_elem else None
        
        premiere_date_elem = item.find("div", {"class": "time_today main_time"})
        premiere_date, premiere_time = None, None
        
        if premiere_date_elem:
            text = premiere_date_elem.get_text(strip=True)
            week_match = re.search(r"每週([一二三四五六日天])", text)
            if week_match: premiere_date = week_match.group(1)
            time_match = re.search(r"(\d{1,2})時(\d{1,2})分", text)
            if time_match: premiere_time = f"{int(time_match.group(1)):02d}:{int(time_match.group(2)):02d}"

        image_tag = item.find("div", {"class": "overflow-hidden anime_cover_image"})
        image_url = image_tag.img["src"] if image_tag and image_tag.img else "無圖片"
        
        # 執行圖片上傳
        anime_image_url = upload_to_cloudinary(image_url, anime_name or "未知") 

        story_elem = item.find("div", {"class": "anime_story"})
        story = story_elem.get_text(strip=True) if story_elem else None
        
        # 【升級 1】使用 Pydantic 模型建立與驗證
        anime_obj = Anime(
            bangumi_id=item.get("acgs-bangumi-data-id", "未知ID"),
            anime_name=anime_name,
            anime_image_url=anime_image_url,
            premiere_date=premiere_date,  
            premiere_time=premiere_time,  
            story=story                   
        )
        
        # 轉回 dict 供後續處理
        return anime_obj.model_dump()

    except Exception as exc:
        logger.warning(f"處理失敗: {exc}")
        return None

# ------------------------------------------------------
# 新增：智能清理輔助函式
# ------------------------------------------------------
def find_oldest_season_file() -> Optional[Tuple[str, str]]:
    """找出 dist/data 下最舊的 YYYY_SEASON.json 檔案 (Year, Season)"""
    if not os.path.isdir(JSON_DIR):
        return None

    # 只選取符合 YYYY_SEASON.json 格式的檔案
    json_files = [f for f in os.listdir(JSON_DIR) if re.match(r'^\d{4}_(春|夏|秋|冬)\.json$', f)]
    if not json_files:
        return None

    def get_sort_key(filename):
        # filename format: YYYY_SEASON.json
        match = re.match(r'^(\d{4})_(\S+)\.json$', filename)
        if not match:
            return float('inf')
        year = int(match.group(1))
        season_name = match.group(2)
        # 使用 Config.SEASON_TO_MONTH 進行排序，確保季節順序正確 (冬, 春, 夏, 秋)
        month = Config.SEASON_TO_MONTH.get(season_name, 13) 
        return year * 100 + month

    oldest_file = min(json_files, key=get_sort_key)
    match = re.match(r'^(\d{4})_(\S+)\.json$', oldest_file)
    if match:
        return match.group(1), match.group(2)
    return None

def cleanup_specific_season(year: str, season: str, folder_prefix: str = "anime_covers/") -> int:
    """清理指定季度在 Cloudinary 上的圖片和本地 JSON"""
    json_filename = f'{year}_{season}.json'
    json_path = os.path.join(JSON_DIR, json_filename)
    public_ids_to_delete = []
    
    logger.warning(f"🧹 開始清理最舊季度: {year} {season} 的圖片資源。")

    # 1. 從 JSON 建立待刪除 Public ID 列表
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for anime in data.get('anime_list', []):
                    url = anime.get('anime_image_url')
                    if url and 'cloudinary.com' in url:
                        # 從 URL 中解析出 Public ID
                        match = re.search(r'v\d+/({})/([\w]+)'.format(folder_prefix.strip('/')), url)
                        if match:
                            public_id = f"{match.group(1)}/{match.group(2)}"
                            public_ids_to_delete.append(public_id)
        except Exception as e:
            logger.error(f"讀取或解析 JSON 檔案失敗: {json_path}. 錯誤: {e}")
    else:
        logger.warning(f"找不到 {json_path}，跳過白名單建立。")

    deleted_count = 0
    if public_ids_to_delete:
        logger.info(f"準備刪除 {year} {season} 相關的 {len(public_ids_to_delete)} 筆雲端資源...")
        
        # 2. 執行批量刪除
        batch_size = 100
        for i in range(0, len(public_ids_to_delete), batch_size):
            batch = public_ids_to_delete[i:i + batch_size]
            try:
                delete_result = cloudinary.api.delete_resources(
                    batch, resource_type="image", type="upload"
                )
                deleted_count += len(delete_result.get('deleted', {}))
            except Exception as e:
                logger.error(f"批量刪除時發生錯誤 (批次 {i//batch_size + 1}): {e}")
                
        logger.info(f"✅ Cloudinary 成功刪除 {year} {season} 資源共 {deleted_count} 筆。")
        
    # 3. 刪除本地 JSON 檔案
    if os.path.exists(json_path):
        try:
            os.remove(json_path)
            logger.info(f"✅ 成功刪除本地 JSON 檔案: {json_filename}")
        except Exception as e:
            logger.error(f"刪除本地 JSON 檔案失敗: {e}")

    # 4. 同步清除本地快取
    if public_ids_to_delete:
        deleted_public_ids_for_cache = set(f"cloudinary_{pid.split('/')[-1]}" for pid in public_ids_to_delete)
        local_cache = load_local_cache()
        
        keys_to_delete = set(local_cache.keys()) & deleted_public_ids_for_cache
        
        if keys_to_delete:
            for key in keys_to_delete:
                del local_cache[key]
            save_local_cache(local_cache)
            logger.info(f"✅ 成功同步移除 {len(keys_to_delete)} 筆本地快取記錄。")
            
    # ✅ 新增：發送清理成功的 Discord 通知
    if deleted_count > 0:
        send_discord_notification(
            status="FAILURE", 
            year=year, 
            season=season, 
            count=deleted_count, 
            error_msg=f"🧹 警告：已啟動智能清理，成功刪除 {deleted_count} 筆 {year} {season} 的圖片資源。空間已釋放。"
        )

    return deleted_count

# ------------------------------------------------------
# 主爬蟲邏輯 (新增智能清理步驟)
# ------------------------------------------------------
def fetch_anime_data(year: str, season: str, cache=None) -> List[Dict]:
    """主函式：包含空間檢查與智能清理機制"""
    
    if season not in SEASON_TO_MONTH:
        return [{"error": "季節無效"}]

    url = f"https://acgsecrets.hk/bangumi/{year}{SEASON_TO_MONTH[season]:02d}/"
    
    with multiprocessing.Manager() as manager:
        try:
            # 🎯 空間檢查與智能清理迴圈
            max_cleanup_attempts = 3
            quota_check_ok = check_cloudinary_storage_quota()
            
            for attempt in range(max_cleanup_attempts):
                if quota_check_ok:
                    break
                    
                logger.warning(f"嘗試執行智能清理 (第 {attempt + 1} 次)...")
                oldest_season = find_oldest_season_file()
                
                if oldest_season is None:
                    logger.error("❌ 智能清理失敗：找不到可刪除的舊季度 JSON 檔案。")
                    break
                    
                oldest_year, oldest_season_name = oldest_season
                # 避免刪除正在爬取的季度
                if (oldest_year, oldest_season_name) == (year, season):
                    logger.error("❌ 智能清理失敗：最舊季度為當前爬取季度，無法刪除。")
                    break

                # 執行清理
                cleanup_specific_season(oldest_year, oldest_season_name)
                
                # 重新檢查配額
                quota_check_ok = check_cloudinary_storage_quota()
                
            if not quota_check_ok:
                logger.error("❌ 爬蟲停止：Cloudinary 空間不足，多次清理後仍無法解決。")
                send_discord_notification("FAILURE", year, season, 0, "Cloudinary 空間不足，智能清理失敗，已停止爬蟲。")
                return [{"error": "Cloudinary 空間不足，智能清理失敗"}]
            
            # ------------------------------------------------------
            # 正常爬蟲邏輯開始
            # ------------------------------------------------------
            
            # 1. 抓取 HTML 
            with requests.Session() as s:
                s.mount("http://", adapter)
                s.mount("https://", adapter)
                response = s.get(url, timeout=10) 
                response.raise_for_status()
            response.encoding = "utf-8"
            
            # 2. 解析
            soup = BeautifulSoup(response.text, "lxml")
            anime_items = soup.select("div#acgs-anime-list div.CV-search")
            if not anime_items:
                msg = f"{year} {season} 來源網站無資料 (HTML結構正確但無項目)"
                logger.warning(msg)
                return []

            item_html_strings = [str(item) for item in anime_items]
            
            # 3. 初始化共享資源
            shared_lock = manager.Lock()
            shared_cache_dict = manager.dict() 
            shared_cache_dict.update(load_local_cache())
            
            # 4. 多進程處理
            max_workers = os.cpu_count() or 1
            with multiprocessing.Pool(processes=max_workers, initializer=init_worker, initargs=(shared_lock, shared_cache_dict)) as pool:
                results = pool.map(worker_process_anime, item_html_strings)

            anime_list = [res for res in results if res is not None]
            
            if not anime_list:
                error_msg = f"{year} {season} 爬取結果為空 (可能解析失敗)"
                logger.warning(f"⚠️ {error_msg}")
                send_discord_notification("FAILURE", year, season, 0, error_msg)
                return []

            sorted_list = sorted(anime_list, key=parse_date_time)
            
            # 5. 存回快取
            save_local_cache(dict(shared_cache_dict))
            
            logger.info(f"成功爬取 {year} {season} 共 {len(sorted_list)} 筆資料")
            
            # 【升級 3】發送成功通知
            send_discord_notification("SUCCESS", year, season, len(sorted_list))
            
            return sorted_list

        except Exception as e:
            logger.error(f"爬取失敗: {e}")
            send_discord_notification("FAILURE", year, season, 0, str(e))
            return [{"error": f"系統錯誤: {str(e)}"}]

def get_current_season(month: int) -> str:
    if 1 <= month <= 3: return "冬"
    if 4 <= month <= 6: return "春"
    if 7 <= month <= 9: return "夏"
    return "秋"