"""Domain models shared by the crawler, repositories, and frontend data files."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

TAIPEI_TZ = ZoneInfo("Asia/Taipei")


class Anime(BaseModel):
    """Validated anime record written to the public quarterly JSON files."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    bangumi_id: str
    anime_name: str = Field(min_length=1)
    anime_image_url: str = Field(min_length=1)
    premiere_date: str = "無首播日期"
    premiere_time: str = "無首播時間"
    story: str = "暫無簡介"

    @field_validator("bangumi_id", mode="before")
    @classmethod
    def validate_bangumi_id(cls, value: object) -> str:
        text = str(value or "").strip()
        return text or "未知ID"

    @field_validator("anime_name", mode="before")
    @classmethod
    def validate_name(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("anime_name is required")
        return text

    @field_validator("anime_image_url", mode="before")
    @classmethod
    def validate_image_url(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text.startswith("https://res.cloudinary.com/"):
            raise ValueError("anime_image_url must be an HTTPS Cloudinary URL")
        return text

    @field_validator("story", mode="before")
    @classmethod
    def normalize_story(cls, value: object) -> str:
        return str(value).strip() if value else "暫無簡介"

    @field_validator("premiere_date", mode="before")
    @classmethod
    def normalize_date(cls, value: object) -> str:
        text = str(value or "").strip()
        if text == "天":
            return "日"
        return text or "無首播日期"

    @field_validator("premiere_time", mode="before")
    @classmethod
    def normalize_time(cls, value: object) -> str:
        return str(value).strip() if value else "無首播時間"


class AnimeCandidate(BaseModel):
    """Parsed source record before its image is stored in Cloudinary."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    bangumi_id: str
    anime_name: str = Field(min_length=1)
    source_image_url: str = Field(min_length=1)
    premiere_date: str = "無首播日期"
    premiere_time: str = "無首播時間"
    story: str = "暫無簡介"


class DataQuality(BaseModel):
    """Machine-readable quality summary embedded in every new data file."""

    model_config = ConfigDict(extra="forbid")

    source_count: int = Field(ge=0)
    record_count: int = Field(ge=0)
    parse_failure_count: int = Field(ge=0)
    fallback_id_count: int = Field(ge=0)
    missing_story_count: int = Field(ge=0)
    missing_date_count: int = Field(ge=0)
    missing_time_count: int = Field(ge=0)

    @classmethod
    def from_records(
        cls,
        records: list[Anime],
        *,
        source_count: int,
        parse_failure_count: int,
    ) -> DataQuality:
        return cls(
            source_count=source_count,
            record_count=len(records),
            parse_failure_count=parse_failure_count,
            fallback_id_count=sum(
                record.bangumi_id.startswith("fallback-") for record in records
            ),
            missing_story_count=sum(record.story == "暫無簡介" for record in records),
            missing_date_count=sum(
                record.premiere_date == "無首播日期" for record in records
            ),
            missing_time_count=sum(
                record.premiere_time == "無首播時間" for record in records
            ),
        )


class QuarterDataset(BaseModel):
    """Versioned data envelope.

    The optional defaults keep historical two-field JSON files readable. Every
    new write includes all fields.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    anime_list: list[Anime]
    generated_at: datetime
    source_url: str | None = None
    quality: DataQuality | None = None

    @field_validator("generated_at")
    @classmethod
    def ensure_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=TAIPEI_TZ)
        return value.astimezone(TAIPEI_TZ)
