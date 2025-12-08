from pydantic import BaseModel, Field, field_validator
from typing import Optional

class Anime(BaseModel):
    """
    定義動畫資料結構
    使用 Pydantic 進行自動驗證與預設值填充
    """
    bangumi_id: str = Field(default="未知ID")
    anime_name: str
    anime_image_url: str
    
    # 定義欄位預設值
    premiere_date: str = Field(default="無首播日期")
    premiere_time: str = Field(default="無首播時間")
    story: str = Field(default="暫無簡介")

    # --- 資料清洗邏輯 (Validators) ---
    
    # 1. 處理簡介：如果是 None 或空字串，轉為預設值
    @field_validator('story', mode='before')
    def ensure_story_string(cls, v):
        return v if v else "暫無簡介"

    # 2. 處理名稱：確保有名稱
    @field_validator('anime_name', mode='before')
    def ensure_name_string(cls, v):
        return v if v else "無名稱"

    # 3. 【關鍵修正】處理日期：攔截 None，轉為預設值
    @field_validator('premiere_date', mode='before')
    def ensure_date_string(cls, v):
        return v if v else "無首播日期"

    # 4. 【關鍵修正】處理時間：攔截 None，轉為預設值
    @field_validator('premiere_time', mode='before')
    def ensure_time_string(cls, v):
        return v if v else "無首播時間"