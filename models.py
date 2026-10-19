"""Domain models shared by the crawler, repositories, and frontend data files."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
BANGUMI_ID_PATTERN = re.compile(r"(?:anime-[0-9]+|fallback-[0-9a-f]{64})\Z")
BROADCAST_TIME_PATTERN = re.compile(r"(?:[0-2][0-9]:[0-5][0-9]|無首播時間)\Z")
SOURCE_QUARTER_URL_PATTERN = re.compile(
    r"https://acgsecrets\.hk/bangumi/[0-9]{4}(?:01|04|07|10)/\Z"
)
CLOUDINARY_DELIVERY_PATH_PATTERN = re.compile(
    r"/[a-z0-9_-]+/image/upload/(?:[^/]+/)*"
    r"anime_covers/[0-9a-f]{32}(?:[0-9a-f]{32})?"
    r"(?:\.[A-Za-z0-9]+)?\Z"
)
BroadcastDay = Literal[
    "一", "二", "三", "四", "五", "六", "日", "一至五", "不定期公開", "無首播日期"
]


class ExternalIds(BaseModel):
    """Database IDs from explicit source links; never the acgsecrets card ID."""

    model_config = ConfigDict(extra="forbid")
    bangumi: int | None = Field(default=None, gt=0, strict=True)
    anilist: int | None = Field(default=None, gt=0, strict=True)


class EpisodeMetadata(BaseModel):
    """Main episodes of one linked anime entry, not an entire franchise."""

    model_config = ConfigDict(extra="forbid")
    total_episodes: int | None = Field(default=None, gt=0, strict=True)
    aired_episodes: int | None = Field(default=None, ge=0, strict=True)
    airing_status: Literal[
        "unknown", "not_yet_aired", "airing", "finished", "paused", "cancelled"
    ] = "unknown"
    episode_count_status: Literal["unknown", "planned", "confirmed"] = "unknown"
    fetch_state: Literal["ok", "unmatched", "stale", "conflict"] = "unmatched"
    source_urls: list[str] = Field(default_factory=list)
    checked_at: datetime | None = None
    attempted_at: datetime | None = None
    end_date: date | None = None
    note: str = ""
    source_kind: Literal[
        "unknown",
        "bangumi_api",
        "bangumi_html",
        "bangumi_archive",
        "manual_official",
        "official_catalogue",
    ] = Field(default="unknown", exclude_if=lambda value: value == "unknown")
    source_snapshot_date: date | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @field_validator("checked_at", "attempted_at")
    @classmethod
    def aware_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("episode timestamps must include a timezone")
        return value.astimezone(TAIPEI_TZ) if value else None

    @field_validator("source_urls")
    @classmethod
    def safe_sources(cls, values: list[str]) -> list[str]:
        for value in values:
            parsed = urlsplit(value)
            if not (
                parsed.scheme == "https"
                and parsed.netloc == "bgm.tv"
                and re.fullmatch(r"/subject/[1-9][0-9]*(?:/ep)?", parsed.path)
                and not parsed.query
                and not parsed.fragment
            ):
                from services.episode_source_reviews import reviewed_source_url

                if not reviewed_source_url(value):
                    raise ValueError(
                        "episode sources must be canonical Bangumi or reviewed official URLs"
                    )
        return values

    @model_validator(mode="after")
    def consistent_counts(self) -> EpisodeMetadata:
        if (
            self.total_episodes is not None
            and self.aired_episodes is not None
            and self.aired_episodes > self.total_episodes
        ):
            raise ValueError("aired episodes may not exceed total episodes")
        if self.episode_count_status != "unknown" and self.total_episodes is None:
            raise ValueError("a known episode count status requires a total")
        if self.airing_status == "finished" and (
            self.total_episodes is None
            or self.aired_episodes != self.total_episodes
            or self.episode_count_status != "confirmed"
        ):
            raise ValueError(
                "finished requires a confirmed, complete main episode count"
            )
        if self.airing_status == "not_yet_aired" and self.aired_episodes not in (
            None,
            0,
        ):
            raise ValueError("not_yet_aired may not have aired episodes")
        return self


def _validated_bangumi_id(value: object, *, allow_legacy: bool) -> str:
    text = str(value or "").strip()
    if allow_legacy and text == "未知ID":
        return text
    if not BANGUMI_ID_PATTERN.fullmatch(text):
        raise ValueError(
            "bangumi_id must be anime-<digits> or fallback-<64 lowercase hex>"
        )
    return text


def _validated_broadcast_day(value: object) -> str:
    text = str(value or "").strip()
    if text == "天":
        return "日"
    return text or "無首播日期"


def _validated_broadcast_time(value: object) -> str:
    text = str(value or "").strip() or "無首播時間"
    if not BROADCAST_TIME_PATTERN.fullmatch(text):
        raise ValueError(
            "premiere_time must use HH:MM with hour 00-29 and minute 00-59"
        )
    return text


class StorySource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["bangumi", "official"] = "bangumi"
    subject_id: int | None = Field(gt=0, strict=True)
    checked_at: datetime
    method: Literal["bangumi_api", "bangumi_archive", "manual_official"]
    source_url: str | None = Field(default=None, exclude_if=lambda value: value is None)
    source_snapshot_date: date | None = None

    @field_validator("checked_at")
    @classmethod
    def aware_checked_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("summary verification time must include timezone")
        return value.astimezone(TAIPEI_TZ)

    @model_validator(mode="after")
    def archive_date_required(self) -> StorySource:
        if self.provider == "official":
            from services.story_source_reviews import story_reviews

            if (
                self.method != "manual_official"
                or self.source_snapshot_date is not None
                or not any(r["source_url"] == self.source_url for r in story_reviews())
            ):
                raise ValueError("official summaries require a reviewed source URL")
        elif (
            self.subject_id is None
            or self.method == "manual_official"
            or self.source_url is not None
        ):
            raise ValueError("Bangumi summaries require a Bangumi method")
        if self.method == "bangumi_archive" and self.source_snapshot_date is None:
            raise ValueError("archive summaries require a snapshot date")
        if (
            self.source_snapshot_date
            and self.source_snapshot_date > self.checked_at.date()
        ):
            raise ValueError("summary snapshot date may not be after verification")
        return self


class Anime(BaseModel):
    """Validated anime record written to the public quarterly JSON files."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    bangumi_id: str
    anime_name: str = Field(min_length=1)
    anime_image_url: str = Field(min_length=1)
    premiere_date: BroadcastDay = "無首播日期"
    premiere_time: str = "無首播時間"
    story: str = "暫無簡介"
    original_name: str | None = None
    external_ids: ExternalIds = Field(default_factory=ExternalIds)
    episodes: EpisodeMetadata | None = None
    story_source: StorySource | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def consistent_story_identity(self) -> Anime:
        if (
            self.story_source
            and self.story_source.subject_id != self.external_ids.bangumi
        ):
            raise ValueError("summary evidence must match the linked Bangumi subject")
        if self.story_source and self.story_source.provider == "official":
            from services.story_source_reviews import reviewed_story

            review = reviewed_story(self)
            if (
                not review
                or review["source_url"] != self.story_source.source_url
                or review["story"] != self.story
            ):
                raise ValueError(
                    "official summary must match its reviewed card and text"
                )
        return self

    @field_validator("bangumi_id", mode="before")
    @classmethod
    def validate_bangumi_id(cls, value: object) -> str:
        return _validated_bangumi_id(value, allow_legacy=True)

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
        try:
            parsed = urlsplit(text)
        except ValueError as exc:
            raise ValueError("anime_image_url must be a valid URL") from exc
        if (
            parsed.scheme != "https"
            or parsed.netloc != "res.cloudinary.com"
            or parsed.query
            or parsed.fragment
            or not CLOUDINARY_DELIVERY_PATH_PATTERN.fullmatch(parsed.path)
        ):
            raise ValueError(
                "anime_image_url must be an HTTPS Cloudinary image URL under "
                "anime_covers/<32 or 64 lowercase hex>"
            )
        return text

    @field_validator("story", mode="before")
    @classmethod
    def normalize_story(cls, value: object) -> str:
        return str(value).strip() if value else "暫無簡介"

    @field_validator("premiere_date", mode="before")
    @classmethod
    def normalize_date(cls, value: object) -> str:
        return _validated_broadcast_day(value)

    @field_validator("premiere_time", mode="before")
    @classmethod
    def normalize_time(cls, value: object) -> str:
        return _validated_broadcast_time(value)


class AnimeCandidate(BaseModel):
    """Parsed source record before its image is stored in Cloudinary."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    bangumi_id: str
    anime_name: str = Field(min_length=1)
    source_image_url: str = Field(min_length=1)
    premiere_date: BroadcastDay = "無首播日期"
    premiere_time: str = "無首播時間"
    story: str = "暫無簡介"
    original_name: str | None = None
    external_ids: ExternalIds = Field(default_factory=ExternalIds)

    @field_validator("bangumi_id", mode="before")
    @classmethod
    def validate_bangumi_id(cls, value: object) -> str:
        return _validated_bangumi_id(value, allow_legacy=False)

    @field_validator("premiere_date", mode="before")
    @classmethod
    def normalize_date(cls, value: object) -> str:
        return _validated_broadcast_day(value)

    @field_validator("premiere_time", mode="before")
    @classmethod
    def normalize_time(cls, value: object) -> str:
        return _validated_broadcast_time(value)


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

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not SOURCE_QUARTER_URL_PATTERN.fullmatch(value):
            raise ValueError("source_url must be an HTTPS acgsecrets.hk quarterly URL")
        return value
