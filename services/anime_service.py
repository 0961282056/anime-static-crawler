from typing import List, Dict, Tuple, Optional
from bs4 import BeautifulSoup
import requests, os, json, hashlib, time, random, re, logging
# 移除 flask_caching 引用，避免 Build Only 模式或 GitHub Actions 報錯
# from flask_caching import Cache 
from config import Config
import cloudinary, cloudinary.uploader, cloudinary.utils
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import multiprocessing

# ------------------------------------------------------
# 初始化與設定
# ------------------------------------------------------
load_dotenv()
logging.basicConfig(level=logging.INFO) 
logger = logging.getLogger(__name__)
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)

# --- 設定快取檔案路徑 ---
CACHE_FILE = os.path.join(os.getcwd(), 'cloudinary_cache.json')
# -------------------------

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
    
    # 重新配置 Cloudinary
    cloudinary_config_global = {
        'cloud_name': os.getenv("CLOUDINARY_CLOUD_NAME"),
        'api_key': os.getenv("CLOUDINARY_API_KEY"),
        'api_secret': os.getenv("CLOUDINARY_API_SECRET"),
        'long_url_signature': True,
        'secure': True
    }
    cloudinary.config(**cloudinary_config_global)
    cloudinary.config(http_client=session_global)
    
    # 初始化 logger
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)
    
    logger.info(f"[WORKER START] 進程 {multiprocessing.current_process().name} 已啟動並初始化資源。")


# ------------------------------------------------------
# 簡易快取（持久性 JSON 檔案讀寫）
# ------------------------------------------------------
def load_local_cache() -> Dict:
    """從 JSON 檔案載入 Cloudinary 內容快取。"""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logger.info(f"從 {CACHE_FILE} 載入 {len(data)} 筆 Cloudinary 快取記錄。")
                return data
        except Exception as e:
            logger.error(f"載入快取檔案失敗: {e}")
            return {}
    return {}

def save_local_cache(data: Dict):
    """將 Manager 快取字典的最終結果儲存到 JSON 檔案中。"""
    try:
        # 只儲存以 'cloudinary_' 開頭的 Content Hash 快取
        filtered_data = {k: v for k, v in data.items() if k.startswith('cloudinary_')}
        
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(filtered_data, f, ensure_ascii=False, indent=4) 
        logger.info(f"成功儲存 {len(filtered_data)} 筆 Cloudinary 內容快取至 {CACHE_FILE}")
    except Exception as e:
        logger.error(f"儲存快取檔案失敗: {e}")

# ------------------------------------------------------
# 日期與時間排序解析
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
# 上傳圖片至 Cloudinary
# ------------------------------------------------------
# 修正：移除了 cache 參數的型別提示 (Cache)
def upload_to_cloudinary(image_url: str, anime_name: str) -> str:
    """處理圖片上傳或快取命中，返回最終 URL。"""
    
    if image_url == "無圖片":
        return "無圖片"
    
    session = session_global
    local_cache = manager_cache_global 
    
    try:
        # 1. 下載圖片
        start_download_time = time.time()
        response = session.get(image_url, timeout=6)
        response.raise_for_status()
        
        content_hash = hashlib.md5(response.content).hexdigest()
        public_id = f"anime_covers/{content_hash}"
        
        # 2. 檢查 Cloudinary 內容快取
        cloudinary_key = f"cloudinary_{content_hash}"
        
        # *** 鎖定區塊 1: 檢查共享快取 ***
        with manager_lock_global:
            if cloudinary_key in local_cache:
                logger.info(f"[CACHE HIT/SKIP] {anime_name} - 圖片內容已存在於快取，跳過 Cloudinary 上傳。") 
                return local_cache[cloudinary_key]
        
        # 3. 上傳圖片
        start_upload_time = time.time() 
        
        upload_result = cloudinary.uploader.upload(
            response.content,
            public_id=public_id, overwrite=True, invalidate=True,
            transformation=[{"width": 300, "height": 300, "crop": "limit", "quality": 90}]
        )
        logger.info(f"[UPLOAD OK] {anime_name} - Cloudinary 上傳/覆蓋 ({public_id}) 耗時: {time.time() - start_upload_time:.2f} 秒")
            
        # 4. 生成 URL
        url, _ = cloudinary.utils.cloudinary_url(
            upload_result["public_id"],
            fetch_format="jpg", quality=90, width=300, height=300, crop="limit"
        )
        
        # *** 鎖定區塊 2: 寫入共享快取 ***
        with manager_lock_global:
            local_cache[cloudinary_key] = url
        
        logger.info(f"[UPLOAD SUCCESS] {anime_name} - 圖片已上傳並寫入快取。")
        return url

    except Exception as e:
        logger.error(f"[ERROR] {anime_name} - 上傳失敗: {image_url[:50]}..., 錯誤: {e}")
        return image_url

# ------------------------------------------------------
# 處理單一動畫項目 (Worker Function)
# ------------------------------------------------------
def worker_process_anime(item_html_str: str) -> Optional[Dict]:
    """由 Pool Worker 執行，處理單一動畫項目的解析和圖片上傳。"""
    
    start_worker_item_time = time.time() 
    anime_name = "未知動畫"
    try:
        item = BeautifulSoup(item_html_str, "lxml").find("div", class_="CV-search")
        if not item:
            return None
        
        # 提取名稱
        anime_name_elem = item.find("h3", {"class": "entity_localized_name"})
        anime_name = anime_name_elem.get_text(strip=True) if anime_name_elem else "無名稱"
        
        logger.info(f"[WORKER START] 進程 {multiprocessing.current_process().name} 開始處理: {anime_name}")

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

        # 處理圖片上傳
        image_tag = item.find("div", {"class": "overflow-hidden anime_cover_image"})
        image_url = image_tag.img["src"] if image_tag and image_tag.img else "無圖片"
        # 呼叫上傳 (不再傳遞 cache 參數)
        anime_image_url = upload_to_cloudinary(image_url, anime_name) 

        # 提取故事
        story_elem = item.find("div", {"class": "anime_story"})
        story = story_elem.get_text(strip=True) if story_elem else "無故事大綱"
        
        logger.info(f"[WORKER END] {anime_name} 處理完畢。總耗時: {time.time() - start_worker_item_time:.2f} 秒。")

        return {
            "bangumi_id": item.get("acgs-bangumi-data-id", "未知ID"),
            "anime_name": anime_name,
            "anime_image_url": anime_image_url,
            "premiere_date": premiere_date,
            "premiere_time": premiere_time,
            "story": story
        }
    except Exception as exc:
        logger.warning(f"單一動畫項目處理失敗: {anime_name}, 錯誤: {exc}")
        return None

# ------------------------------------------------------
# 抓取整季動畫資料
# ------------------------------------------------------
# 修正：移除了 cache 參數的型別提示
def fetch_anime_data(year: str, season: str, cache=None) -> List[Dict]:
    """主函式，協調 HTML 抓取、多進程處理和結果排序。"""
    
    if season not in SEASON_TO_MONTH:
        return [{"error": "季節無效，請輸入有效季節（冬、春、夏、秋）"}]

    url = f"https://acgsecrets.hk/bangumi/{year}{SEASON_TO_MONTH[season]:02d}/"
    
    with multiprocessing.Manager() as manager:
        start_total_time = time.time() 
        try:
            # 1. 抓取 HTML 內容
            start_fetch_time = time.time() 
            with requests.Session() as s:
                s.mount("http://", adapter)
                s.mount("https://", adapter)
                response = s.get(url, timeout=10) 
                response.raise_for_status()
            
            response.encoding = "utf-8"
            logger.info(f"[TIME] 步驟 1 (HTML 抓取) 耗時: {time.time() - start_fetch_time:.2f} 秒") 
            
            # 2. 解析 HTML 並準備多進程輸入
            start_parse_time = time.time() 
            soup = BeautifulSoup(response.text, "lxml")

            anime_data = soup.find("div", id="acgs-anime-list")
            if not anime_data:
                logger.warning(f"未找到 {year} {season} 任何動畫資料")
                return []

            anime_items = anime_data.find_all("div", class_="CV-search")
            if not anime_items:
                logger.warning(f"{year} {season} 頁面結構異常或無資料")
                return []

            item_html_strings = [str(item) for item in anime_items]
            
            logger.info(f"[TIME] 步驟 2 (HTML 解析及字串轉換, {len(item_html_strings)} 筆) 耗時: {time.time() - start_parse_time:.2f} 秒")
            
            # 創建共享鎖和共享快取字典
            shared_lock = manager.Lock()
            shared_cache_dict = manager.dict() 
            
            # 載入持久性快取
            initial_cache = load_local_cache()
            shared_cache_dict.update(initial_cache)
            logger.info(f"載入持久性快取完成，共 {len(shared_cache_dict)} 筆 Cloudinary 內容快取可供跳過。")
            
            # 3. 使用 multiprocessing.Pool 處理資料
            start_pool_time = time.time() 
            max_workers = os.cpu_count() or 1
            logger.info(f"啟動 {max_workers} 個進程處理 {len(item_html_strings)} 筆動畫資料")

            with multiprocessing.Pool(
                processes=max_workers, 
                initializer=init_worker,
                initargs=(shared_lock, shared_cache_dict)
            ) as pool:
                results = pool.map(worker_process_anime, item_html_strings)
                
            logger.info(f"[TIME] 步驟 3 (多進程處理/圖片上傳) 耗時: {time.time() - start_pool_time:.2f} 秒") 

            anime_list = [res for res in results if res is not None]

            # 4. 排序、快取儲存
            start_sort_time = time.time()
            sorted_list = sorted(anime_list, key=parse_date_time)
            logger.info(f"[TIME] 步驟 4 (結果排序) 耗時: {time.time() - start_sort_time:.2f} 秒")
            
            # 儲存快取
            final_cache_data = dict(shared_cache_dict)
            save_local_cache(final_cache_data)
            
            logger.info(f"成功爬取並處理 {year} {season} 共 {len(sorted_list)} 筆資料")
            logger.info(f"[TIME] 總執行時間 (含多進程): {time.time() - start_total_time:.2f} 秒")
            return sorted_list

        except requests.RequestException as e:
            logger.error(f"爬取失敗 ({url}): {e}")
            return [{"error": "無法從網站獲取資料，請檢查網站是否正確"}]


# ------------------------------------------------------
# 依月份判斷季節
# ------------------------------------------------------
def get_current_season(month: int) -> str:
    if 1 <= month <= 3:
        return "冬"
    if 4 <= month <= 6:
        return "春"
    if 7 <= month <= 9:
        return "夏"
    return "秋"
