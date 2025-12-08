from typing import List, Dict, Tuple, Optional
from bs4 import BeautifulSoup
import requests, os, json, hashlib, time, re, logging
import multiprocessing
import cloudinary, cloudinary.uploader, cloudinary.utils
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

# ------------------------------------------------------
# requests Session & Pool 設定
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
# 進程間共享數據
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
# 簡易快取 (JSON)
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
# 輔助函式
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
# Discord 通知 (Rich Notification)
# ------------------------------------------------------
def send_discord_notification(status: str, year: str, season: str, count: int = 0, error_msg: str = ""):
    """【升級 3】發送漂亮的 Embed 通知到 Discord"""
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    
    # 如果沒設定 Webhook，就直接跳過，不報錯
    if not webhook_url:
        return

    # 設定顏色 (綠色成功，紅色失敗)
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
# 圖片處理
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
        
        # 【升級 2】使用自動格式 (f_auto) 與自動品質 (q_auto) 進行 WebP 優化
        upload_result = cloudinary.uploader.upload(
            response.content,
            public_id=public_id, overwrite=True, invalidate=True,
            transformation=[
                {"width": 300, "height": 450, "crop": "limit", "quality": "auto", "fetch_format": "auto"}
            ]
        )
            
        url, _ = cloudinary.utils.cloudinary_url(
            upload_result["public_id"],
            fetch_format="auto", quality="auto", width=300, height=450, crop="limit"
        )
        
        with manager_lock_global:
            local_cache[cloudinary_key] = url
        
        logger.info(f"[UPLOAD] {anime_name} 上傳完成 (WebP優化)")
        return url

    except Exception as e:
        logger.error(f"[ERROR] {anime_name} 圖片處理失敗: {e}")
        return image_url

def worker_process_anime(item_html_str: str) -> Optional[Dict]:
    """Worker: 解析並使用 Pydantic 驗證資料"""
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
        # 如果缺少必要欄位，模型會自動填入預設值 (在 models.py 定義)
        anime_obj = Anime(
            bangumi_id=item.get("acgs-bangumi-data-id", "未知ID"),
            anime_name=anime_name,
            anime_image_url=anime_image_url,
            premiere_date=premiere_date,  # 若為 None，模型會轉為 "無首播日期"
            premiere_time=premiere_time,  # 若為 None，模型會轉為 "無首播時間"
            story=story                   # 若為 None，模型會轉為 "暫無簡介"
        )
        
        # 轉回 dict 供後續處理
        return anime_obj.model_dump()

    except Exception as exc:
        logger.warning(f"處理失敗: {exc}")
        return None

# ------------------------------------------------------
# 主爬蟲邏輯
# ------------------------------------------------------
def fetch_anime_data(year: str, season: str, cache=None) -> List[Dict]:
    """主函式"""
    
    if season not in SEASON_TO_MONTH:
        return [{"error": "季節無效"}]

    url = f"https://acgsecrets.hk/bangumi/{year}{SEASON_TO_MONTH[season]:02d}/"
    
    with multiprocessing.Manager() as manager:
        try:
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
                # 視情況決定是否發送失敗通知，這裡選擇不視為嚴重錯誤
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
                # 發送失敗通知
                send_discord_notification("FAILURE", year, season, 0, error_msg)
                return []

            sorted_list = sorted(anime_list, key=parse_date_time)
            
            # 5. 存回快取
            save_local_cache(dict(shared_cache_dict))
            
            logger.info(f"成功爬取 {year} {season} 共 {len(sorted_list)} 筆資料")
            
            # 【升級 3】發送成功通知 (帶數據)
            send_discord_notification("SUCCESS", year, season, len(sorted_list))
            
            return sorted_list

        except Exception as e:
            logger.error(f"爬取失敗: {e}")
            # 發送失敗通知
            send_discord_notification("FAILURE", year, season, 0, str(e))
            return [{"error": f"系統錯誤: {str(e)}"}]

def get_current_season(month: int) -> str:
    if 1 <= month <= 3: return "冬"
    if 4 <= month <= 6: return "春"
    if 7 <= month <= 9: return "夏"
    return "秋"