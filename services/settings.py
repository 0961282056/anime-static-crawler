"""Environment and path settings with safe defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from services.errors import ConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    output_dir: Path
    data_dir: Path
    templates_dir: Path
    static_source_dir: Path
    static_output_dir: Path
    cache_file: Path
    cloudflare_headers_file: Path

    @classmethod
    def from_environment(cls) -> ProjectPaths:
        root = PROJECT_ROOT
        configured_output = os.getenv("OUTPUT_DIR")
        output_dir = (
            Path(configured_output).expanduser().resolve()
            if configured_output
            else root / "dist"
        )
        return cls(
            root=root,
            output_dir=output_dir,
            data_dir=output_dir / "data",
            templates_dir=root / "templates",
            static_source_dir=root / "static",
            static_output_dir=output_dir / "static",
            cache_file=root / "cloudinary_cache.json",
            cloudflare_headers_file=root / "_headers",
        )


@dataclass(frozen=True)
class CrawlerSettings:
    source_base_url: str
    source_user_agent: str
    max_workers: int
    request_timeout_seconds: int
    image_timeout_seconds: int
    image_max_bytes: int
    image_max_pixels: int
    image_allowed_hosts: tuple[str, ...]
    minimum_count_ratio: float
    maximum_parse_failure_ratio: float
    maximum_fallback_id_ratio: float
    cloudinary_quota_limit_percent: float

    @classmethod
    def from_environment(cls) -> CrawlerSettings:
        allowed_hosts = tuple(
            host.strip().lower()
            for host in os.getenv("IMAGE_ALLOWED_HOSTS", "static.acgsecrets.hk").split(
                ","
            )
            if host.strip()
        )
        settings = cls(
            source_base_url=os.getenv(
                "ANIME_SOURCE_BASE_URL", "https://acgsecrets.hk/bangumi"
            ).rstrip("/"),
            source_user_agent=(
                os.getenv("CRAWLER_USER_AGENT", "").strip()
                or "anime-static-crawler/2.0 "
                "(+https://github.com/0961282056/anime-static-crawler)"
            ),
            max_workers=_env_int("CRAWLER_MAX_WORKERS", 4),
            request_timeout_seconds=_env_int("REQUEST_TIMEOUT_SECONDS", 15),
            image_timeout_seconds=_env_int("IMAGE_TIMEOUT_SECONDS", 15),
            image_max_bytes=_env_int("IMAGE_MAX_BYTES", 10 * 1024 * 1024),
            image_max_pixels=_env_int("IMAGE_MAX_PIXELS", 40_000_000),
            image_allowed_hosts=allowed_hosts,
            minimum_count_ratio=_env_float("QUALITY_MIN_COUNT_RATIO", 0.70),
            maximum_parse_failure_ratio=_env_float(
                "QUALITY_MAX_PARSE_FAILURE_RATIO", 0.0
            ),
            maximum_fallback_id_ratio=_env_float("QUALITY_MAX_FALLBACK_ID_RATIO", 0.0),
            cloudinary_quota_limit_percent=_env_float(
                "CLOUDINARY_QUOTA_LIMIT_PERCENT", 90.0
            ),
        )
        if settings.max_workers < 1 or settings.max_workers > 8:
            raise ConfigurationError("CRAWLER_MAX_WORKERS must be between 1 and 8")
        if not 0 < settings.minimum_count_ratio <= 1:
            raise ConfigurationError(
                "QUALITY_MIN_COUNT_RATIO must be greater than 0 and at most 1"
            )
        if not 0 <= settings.maximum_parse_failure_ratio < 1:
            raise ConfigurationError(
                "QUALITY_MAX_PARSE_FAILURE_RATIO must be at least 0 and below 1"
            )
        if not 0 <= settings.maximum_fallback_id_ratio < 1:
            raise ConfigurationError(
                "QUALITY_MAX_FALLBACK_ID_RATIO must be at least 0 and below 1"
            )
        if not settings.image_allowed_hosts:
            raise ConfigurationError("IMAGE_ALLOWED_HOSTS may not be empty")
        if not 1 <= settings.image_max_pixels <= 100_000_000:
            raise ConfigurationError("IMAGE_MAX_PIXELS must be between 1 and 100000000")
        return settings


def required_cloudinary_credentials() -> dict[str, str]:
    names = (
        "CLOUDINARY_CLOUD_NAME",
        "CLOUDINARY_API_KEY",
        "CLOUDINARY_API_SECRET",
    )
    values = {name: os.getenv(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ConfigurationError(
            "Missing required Cloudinary settings: " + ", ".join(missing)
        )
    return values
