from typing import List, Dict, Tuple
from bs4 import BeautifulSoup
import requests, os, json, hashlib, time, random, re, logging
from flask_caching import Cache # 雖然未用，但保留
from config import Config
import cloudinary, cloudinary.uploader, cloudinary.utils
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import multiprocessing # <-- 修正：需要使用 Manager 來管理共享資源

# ------------------------------------------------------
# 初始化與設定
# ------------------------------------------------------
load_dotenv()
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)

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
# 進程間共享數據 (全局變數用於在 init_worker 中接收共享對象)
# ------------------------------------------------------
session_global = None 
cloudinary_config_global = {} 
manager_lock_global = None     # 用於接收共享鎖
manager_cache_global = None    # 用於接收共享快取字典

def init_worker(shared_lock, shared_cache_dict):
    """每個進程啟動時初始化 session, Cloudinary, 共用鎖, 和共用快取。"""
    global session_global
    global cloudinary_config_global
    global manager_lock_global
    global manager_cache_global
    
    # 賦值共享對象到進程內全局變數
    manager_lock_global = shared_lock
    manager_cache_global = shared_cache_dict
    
    # 重新初始化 requests session
    session_global = requests.Session()
    session_global.mount("http://", adapter)
    session_global.mount("https://", adapter)
    session_global.mount("https://api.cloudinary.com", cloudinary_adapter)
    
    # 重新配置 Cloudinary (優化點 2: 只在 worker 啟動時配置一次)
    cloudinary_config_global = {
        'cloud_name': os.getenv("CLOUDINARY_CLOUD_NAME"),
        'api_key': os.getenv("CLOUDINARY_API_KEY"),
        'api_secret': os.getenv("CLOUDINARY_API_SECRET"),
        'long_url_signature': True,
        'secure': True
    }
    cloudinary.config(**cloudinary_config_global)
    # 配置 session 給 Cloudinary (確保使用進程內的連接池)
    cloudinary.config(http_client=session_global)
    
    # 初始化 logger
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)


# ------------------------------------------------------
# 簡易快取（優化點 1: 移除檔案讀寫，因為 GitHub Actions 不持久化 /tmp）
# ------------------------------------------------------
# CACHE_FILE = "/tmp/anime_cache.json" # 移除

def load_local_cache() -> Dict:
    """從檔案載入快取，但現在只返回空字典，因為 /tmp 不持久化。"""
    return {}

def save_local_cache(data: Dict):
    """將 Manager 快取字典的最終結果儲存到檔案中 (現在不做任何事)。"""
    pass

# ------------------------------------------------------
# 日期與時間排序解析 - 保持不變
# ------------------------------------------------------
def parse_date_time(anime: Dict) -> Tuple[int, float]:
    # ... (保持不變)
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
# 上傳圖片至 Cloudinary（使用共享鎖和 Manager 快取）
# ------------------------------------------------------
def upload_to_cloudinary(image_url: str, cache: Cache = None) -> str:
    if image_url == "無圖片":
        return "無圖片"
    
    # 使用 worker 內初始化的 session 和 Manager 共享快取
    session = session_global
    local_cache = manager_cache_global 
    
    # 檢查 URL 快取
    url_cache_key = f"image_url_{hash(image_url)}"
    if url_cache_key in local_cache:
        return local_cache[url_cache_key]

    try:
        # 1. 下載圖片
        response = session.get(image_url, timeout=6)
        response.raise_for_status()
        content_hash = hashlib.md5(response.content).hexdigest()
        public_id = f"anime_covers/{content_hash}"
        
        # 2. 檢查 Cloudinary 內容快取 (使用共享快取)
        cloudinary_key = f"cloudinary_{content_hash}"
        if cloudinary_key in local_cache:
            return local_cache[cloudinary_key]

        # 3. 上傳圖片 (優化點 2: 移除重複配置，只保留核心上傳邏輯)
        with manager_lock_global:
            time.sleep(random.uniform(0.1, 0.25))
            upload_result = cloudinary.uploader.upload(
                response.content,
                public_id=public_id, overwrite=True, invalidate=True,
                transformation=[{"width": 300, "height": 300, "crop": "limit", "quality": 90}]
            )

        # 4. 生成 URL 並寫入共享快取
        # 注意：這裡使用 worker 內配置的 Cloudinary，無需傳遞 config
        url, _ = cloudinary.utils.cloudinary_url(
            upload_result["public_id"],
            fetch_format="jpg", quality=90, width=300, height=300, crop="limit"
        )
        local_cache[url_cache_key] = url
        local_cache[cloudinary_key] = url
        return url

    except Exception as e:
        logger.error(f"[ERROR] 上傳失敗: {image_url[:50]}..., 錯誤: {e}")
        # 如果上傳失敗，返回原始 URL 作為備用
        return image_url

# ------------------------------------------------------
# 處理單一動畫項目 (Worker Function) - 保持不變
# ------------------------------------------------------
def worker_process_anime(item_html_str: str) -> Dict | None:
    # ... (保持不變)
    try:
        # 使用 lxml 解析，加快速度
        item = BeautifulSoup(item_html_str, "lxml").find("div", class_="CV-search")
        if not item:
            return None
        
        # 提取日期/時間
        premiere_date_elem = item.find("div", {"class": "time_today main_time"})
        premiere_date, premiere_time = "無首播日期", "無首播時間"
        if premiere_date_elem:
            text = premiere_date_elem.get_text(strip=True)
            week_match = re.search(r"每週([一二三四五六日天])", text)
            week_day = week_match.group(1) if week_match else None
            time_match = re.search(r"(\d{1,2})時(\d{1,2})分", text)
            if time_match:
                premiere_time = f"{int(time_match.group(1)):02d}:{int(time_match.group(2)):02d}"
            if week_day:
                premiere_date = week_day

        # 處理圖片上傳 (這裡會使用到共享鎖和共享快取)
        image_tag = item.find("div", {"class": "overflow-hidden anime_cover_image"})
        image_url = image_tag.img["src"] if image_tag and image_tag.img else "無圖片"
        anime_image_url = upload_to_cloudinary(image_url) 

        # 提取名稱和故事
        anime_name_elem = item.find("h3", {"class": "entity_localized_name"})
        anime_name = anime_name_elem.get_text(strip=True) if anime_name_elem else "無名稱"

        story_elem = item.find("div", {"class": "anime_story"})
        story = story_elem.get_text(strip=True) if story_elem else "無故事大綱"

        return {
            "bangumi_id": item.get("acgs-bangumi-data-id", "未知ID"),
            "anime_name": anime_name,
            "anime_image_url": anime_image_url,
            "premiere_date": premiere_date,
            "premiere_time": premiere_time,
            "story": story
        }
    except Exception as exc:
        logger.warning(f"單一動畫項目處理失敗: {exc}")
        return None

# ------------------------------------------------------
# 抓取整季動畫資料（使用多進程加速圖片處理）
# ------------------------------------------------------
def fetch_anime_data(year: str, season: str, cache: Cache = None) -> List[Dict]:
    if season not in SEASON_TO_MONTH:
        return [{"error": "季節無效，請輸入有效季節（冬、春、夏、秋）"}]

    # 1. 移除整季快取檢查 (因為檔案快取已移除)
    # full_cache_key = f"anime_{year}_{season}"
    # if full_cache_key in local_cache_file: ...

    url = f"https://acgsecrets.hk/bangumi/{year}{SEASON_TO_MONTH[season]:02d}/"
    
    # 使用 Manager 來創建可被多進程共享的對象 (鎖和圖片快取)
    with multiprocessing.Manager() as manager:
        try:
            # 1. 抓取 HTML 內容
            with requests.Session() as s:
                s.mount("http://", adapter)
                s.mount("https://", adapter)
                response = s.get(url, timeout=10) 
                response.raise_for_status()
            
            response.encoding = "utf-8"
            soup = BeautifulSoup(response.text, "lxml")

            anime_data = soup.find("div", id="acgs-anime-list")
            if not anime_data:
                logger.warning(f"未找到 {year} {season} 任何動畫資料")
                return []

            anime_items = anime_data.find_all("div", class_="CV-search")
            if not anime_items:
                logger.warning(f"{year} {season} 頁面結構異常或無資料")
                return []

            # 2. 將每個動畫項目的 HTML 轉換為字串，以便在進程間傳遞
            item_html_strings = [str(item) for item in anime_items]
            
            # 創建共享鎖和共享快取字典
            shared_lock = manager.Lock()
            # 優化點 1: 共享快取字典從空字典開始，只用於本次運行避免重複上傳
            shared_cache_dict = manager.dict() 
            
            # 3. 使用 multiprocessing.Pool 處理資料
            max_workers = os.cpu_count() or 1
            logger.info(f"啟動 {max_workers} 個進程處理 {len(item_html_strings)} 筆動畫資料")

            # 透過 initargs 將共享對象傳遞給每個 worker
            with multiprocessing.Pool(
                processes=max_workers, 
                initializer=init_worker,
                initargs=(shared_lock, shared_cache_dict)
            ) as pool:
                results = pool.map(worker_process_anime, item_html_strings)
                
            # 過濾掉處理失敗的 None 結果
            anime_list = [res for res in results if res is not None]

            # 4. 排序和快取
            sorted_list = sorted(anime_list, key=parse_date_time)
            
            # 5. 移除主線程處理最終快取 (優化點 1)
            # 因為所有的圖片 URL 都已經包含在 sorted_list 中，無需額外儲存圖片快取
            # if sorted_list:
            #     final_cache_data = dict(shared_cache_dict) 
            #     final_cache_data[full_cache_key] = sorted_list
            #     save_local_cache(final_cache_data)
            
            logger.info(f"成功爬取並處理 {year} {season} 共 {len(sorted_list)} 筆資料")
            return sorted_list

        except requests.RequestException as e:
            logger.error(f"爬取失敗 ({url}): {e}")
            return [{"error": "無法從網站獲取資料，請檢查網站是否正確"}]


# ------------------------------------------------------
# 依月份判斷季節 - 保持不變
# ------------------------------------------------------
def get_current_season(month: int) -> str:
    if 1 <= month <= 3:
        return "冬"
    if 4 <= month <= 6:
        return "春"
    if 7 <= month <= 9:
        return "夏"
    return "秋"