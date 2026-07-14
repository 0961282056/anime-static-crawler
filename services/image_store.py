"""Cloudinary image storage with quota checks and bounded source downloads."""

from __future__ import annotations

import hashlib
import io
import threading

import cloudinary
import cloudinary.api
import cloudinary.uploader
import cloudinary.utils
from PIL import Image, UnidentifiedImageError

from services.cache_repository import CacheRepository
from services.errors import ImageStoreError, QuotaExceededError
from services.http_client import SafeImageDownloader
from services.settings import CrawlerSettings, required_cloudinary_credentials


class CloudinaryImageStore:
    def __init__(
        self,
        settings: CrawlerSettings,
        cache: CacheRepository,
        downloader: SafeImageDownloader | None = None,
    ) -> None:
        credentials = required_cloudinary_credentials()
        cloudinary.config(
            cloud_name=credentials["CLOUDINARY_CLOUD_NAME"],
            api_key=credentials["CLOUDINARY_API_KEY"],
            api_secret=credentials["CLOUDINARY_API_SECRET"],
            secure=True,
            long_url_signature=True,
        )
        self.settings = settings
        self.cache = cache
        self.downloader = downloader or SafeImageDownloader(settings)
        self._key_locks: dict[str, threading.Lock] = {}
        self._key_locks_guard = threading.Lock()

    def assert_quota_available(self) -> None:
        try:
            usage_data = cloudinary.api.usage()
        except Exception as exc:
            raise ImageStoreError(
                f"Unable to verify Cloudinary quota safely: {exc}"
            ) from exc

        used_percent = usage_data.get("credits", {}).get("used_percent")
        if used_percent is None:
            raise ImageStoreError(
                "Cloudinary usage response omitted credits.used_percent"
            )
        if float(used_percent) >= self.settings.cloudinary_quota_limit_percent:
            raise QuotaExceededError(
                "Cloudinary quota is at "
                f"{float(used_percent):.2f}%; automatic deletion is disabled. "
                "Run the manual retention dry-run after reviewing references."
            )

    def _verify_image(self, content: bytes) -> None:
        try:
            with Image.open(io.BytesIO(content)) as image:
                width, height = image.size
                pixel_count = width * height
                if pixel_count > self.settings.image_max_pixels:
                    raise ImageStoreError(
                        "Downloaded image dimensions "
                        f"{width}x{height} ({pixel_count} pixels) exceed the "
                        f"{self.settings.image_max_pixels} pixel safety limit"
                    )
                image.verify()
        except ImageStoreError:
            raise
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
            raise ImageStoreError("Downloaded content is not a valid image") from exc

    def _lock_for(self, key: str) -> threading.Lock:
        with self._key_locks_guard:
            return self._key_locks.setdefault(key, threading.Lock())

    def store(self, source_url: str, anime_name: str) -> str:
        source_key = "source_" + hashlib.sha256(source_url.encode("utf-8")).hexdigest()
        cached_source = self.cache.get(source_key)
        if cached_source:
            return cached_source

        downloaded = self.downloader.download(source_url)
        self._verify_image(downloaded.content)

        legacy_md5_key = (
            "cloudinary_"
            + hashlib.md5(downloaded.content, usedforsecurity=False).hexdigest()
        )
        sha256_digest = hashlib.sha256(downloaded.content).hexdigest()
        content_key = f"cloudinary_sha256_{sha256_digest}"

        with self._lock_for(content_key):
            cached = self.cache.get(content_key) or self.cache.get(legacy_md5_key)
            if cached:
                self.cache.set(source_key, cached)
                return cached

            public_id = f"anime_covers/{sha256_digest}"
            try:
                result = cloudinary.uploader.upload(
                    downloaded.content,
                    public_id=public_id,
                    overwrite=False,
                    resource_type="image",
                    type="upload",
                )
                if not result.get("public_id"):
                    raise ImageStoreError(
                        "Cloudinary upload response omitted public_id"
                    )
                url, _ = cloudinary.utils.cloudinary_url(
                    result["public_id"],
                    secure=True,
                    fetch_format="auto",
                    quality="auto:best",
                )
            except ImageStoreError:
                raise
            except Exception as exc:
                raise ImageStoreError(
                    f"{anime_name}: Cloudinary upload failed: {exc}"
                ) from exc

            if not url.startswith("https://res.cloudinary.com/"):
                raise ImageStoreError(
                    f"{anime_name}: Cloudinary produced an unexpected URL"
                )
            self.cache.set(content_key, url)
            self.cache.set(source_key, url)
            return url
