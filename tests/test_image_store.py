from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image

import services.image_store as image_store_module
from services.cache_repository import CacheRepository
from services.errors import ImageStoreError, QuotaExceededError
from services.http_client import DownloadedImage
from services.image_store import CloudinaryImageStore
from services.settings import CrawlerSettings


def _settings(**overrides: object) -> CrawlerSettings:
    values: dict[str, object] = {
        "source_base_url": "https://acgsecrets.hk/bangumi",
        "source_user_agent": "crawler-tests/1.0",
        "max_workers": 1,
        "request_timeout_seconds": 3,
        "image_timeout_seconds": 3,
        "image_max_bytes": 1024 * 1024,
        "image_max_pixels": 1_000_000,
        "image_allowed_hosts": ("static.acgsecrets.hk",),
        "minimum_count_ratio": 0.7,
        "maximum_parse_failure_ratio": 0.0,
        "maximum_fallback_id_ratio": 0.0,
        "cloudinary_quota_limit_percent": 90.0,
    }
    values.update(overrides)
    return CrawlerSettings(**values)  # type: ignore[arg-type]


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), (255, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


class _Downloader:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls: list[str] = []

    def download(self, source_url: str) -> DownloadedImage:
        self.calls.append(source_url)
        return DownloadedImage(
            content=self.content,
            content_type="image/png",
            final_url=source_url,
        )


def _store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    downloader: _Downloader | None = None,
    settings: CrawlerSettings | None = None,
) -> CloudinaryImageStore:
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "test-cloud")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "test-key")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "test-secret")
    monkeypatch.setattr(image_store_module.cloudinary, "config", lambda **kwargs: None)
    return CloudinaryImageStore(
        settings or _settings(),
        CacheRepository(tmp_path / "cache.json"),
        downloader=downloader or _Downloader(_png_bytes()),
    )


def test_quota_check_allows_safe_usage_and_rejects_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        image_store_module.cloudinary.api,
        "usage",
        lambda: {"credits": {"used_percent": 89.9}},
    )
    store.assert_quota_available()

    monkeypatch.setattr(
        image_store_module.cloudinary.api,
        "usage",
        lambda: {"credits": {"used_percent": 90}},
    )
    with pytest.raises(QuotaExceededError, match="automatic deletion is disabled"):
        store.assert_quota_available()


def test_quota_check_fails_closed_on_api_or_schema_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        image_store_module.cloudinary.api,
        "usage",
        lambda: {},
    )
    with pytest.raises(ImageStoreError, match="omitted credits.used_percent"):
        store.assert_quota_available()

    def fail_usage() -> dict[str, object]:
        raise RuntimeError("cloud unavailable")

    monkeypatch.setattr(image_store_module.cloudinary.api, "usage", fail_usage)
    with pytest.raises(ImageStoreError, match="verify Cloudinary quota"):
        store.assert_quota_available()


def test_source_url_cache_hit_skips_download_and_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_url = "https://static.acgsecrets.hk/a.png"
    downloader = _Downloader(_png_bytes())
    store = _store(tmp_path, monkeypatch, downloader=downloader)
    source_key = "source_" + hashlib.sha256(source_url.encode()).hexdigest()
    cached_url = "https://res.cloudinary.com/test-cloud/cached.webp"
    store.cache.set(source_key, cached_url)
    monkeypatch.setattr(
        image_store_module.cloudinary.uploader,
        "upload",
        lambda *args, **kwargs: pytest.fail("upload must not run"),
    )

    assert store.store(source_url, "Cache Hit") == cached_url
    assert downloader.calls == []


def test_content_cache_hit_links_new_source_without_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_url = "https://static.acgsecrets.hk/a.png"
    content = _png_bytes()
    downloader = _Downloader(content)
    store = _store(tmp_path, monkeypatch, downloader=downloader)
    content_key = "cloudinary_sha256_" + hashlib.sha256(content).hexdigest()
    cached_url = "https://res.cloudinary.com/test-cloud/content.webp"
    store.cache.set(content_key, cached_url)
    monkeypatch.setattr(
        image_store_module.cloudinary.uploader,
        "upload",
        lambda *args, **kwargs: pytest.fail("upload must not run"),
    )

    assert store.store(source_url, "Content Hit") == cached_url
    source_key = "source_" + hashlib.sha256(source_url.encode()).hexdigest()
    assert store.cache.get(source_key) == cached_url


def test_upload_success_caches_content_and_source_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_url = "https://static.acgsecrets.hk/a.png"
    content = _png_bytes()
    downloader = _Downloader(content)
    store = _store(tmp_path, monkeypatch, downloader=downloader)
    captured: dict[str, object] = {}

    def upload(payload: bytes, **kwargs: object) -> dict[str, str]:
        captured["payload"] = payload
        captured.update(kwargs)
        return {"public_id": str(kwargs["public_id"])}

    cloud_url = "https://res.cloudinary.com/test-cloud/image/upload/cover.webp"
    monkeypatch.setattr(image_store_module.cloudinary.uploader, "upload", upload)
    monkeypatch.setattr(
        image_store_module.cloudinary.utils,
        "cloudinary_url",
        lambda public_id, **kwargs: (cloud_url, {}),
    )

    assert store.store(source_url, "Upload") == cloud_url
    digest = hashlib.sha256(content).hexdigest()
    assert captured["public_id"] == f"anime_covers/{digest}"
    assert captured["overwrite"] is False
    assert store.cache.get(f"cloudinary_sha256_{digest}") == cloud_url
    source_key = "source_" + hashlib.sha256(source_url.encode()).hexdigest()
    assert store.cache.get(source_key) == cloud_url


def test_upload_failure_is_raised_without_cache_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path, monkeypatch)

    def fail_upload(*args: object, **kwargs: object) -> dict[str, str]:
        raise RuntimeError("upload offline")

    monkeypatch.setattr(
        image_store_module.cloudinary.uploader,
        "upload",
        fail_upload,
    )

    with pytest.raises(ImageStoreError, match="Anime A: Cloudinary upload failed"):
        store.store("https://static.acgsecrets.hk/a.png", "Anime A")

    assert store.cache.snapshot() == {}


def test_invalid_downloaded_image_never_reaches_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(
        tmp_path,
        monkeypatch,
        downloader=_Downloader(b"not an image"),
    )
    monkeypatch.setattr(
        image_store_module.cloudinary.uploader,
        "upload",
        lambda *args, **kwargs: pytest.fail("upload must not run"),
    )

    with pytest.raises(ImageStoreError, match="not a valid image"):
        store.store("https://static.acgsecrets.hk/a.png", "Invalid")


def test_oversized_pixel_dimensions_never_reach_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (11, 10), (255, 0, 0)).save(buffer, format="PNG")
    store = _store(
        tmp_path,
        monkeypatch,
        downloader=_Downloader(buffer.getvalue()),
        settings=_settings(image_max_pixels=100),
    )
    monkeypatch.setattr(
        image_store_module.cloudinary.uploader,
        "upload",
        lambda *args, **kwargs: pytest.fail("upload must not run"),
    )

    with pytest.raises(ImageStoreError, match="110 pixels.*100 pixel safety limit"):
        store.store("https://static.acgsecrets.hk/oversized.png", "Oversized")
