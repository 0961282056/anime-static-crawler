from __future__ import annotations

from pathlib import Path

import pytest

import services.settings as settings_module
from services.errors import ConfigurationError
from services.settings import (
    CrawlerSettings,
    ProjectPaths,
    required_cloudinary_credentials,
)

ENV_NAMES = (
    "ANIME_SOURCE_BASE_URL",
    "CRAWLER_USER_AGENT",
    "CRAWLER_MAX_WORKERS",
    "REQUEST_TIMEOUT_SECONDS",
    "IMAGE_TIMEOUT_SECONDS",
    "IMAGE_MAX_BYTES",
    "IMAGE_MAX_PIXELS",
    "IMAGE_ALLOWED_HOSTS",
    "QUALITY_MIN_COUNT_RATIO",
    "QUALITY_MAX_PARSE_FAILURE_RATIO",
    "QUALITY_MAX_FALLBACK_ID_RATIO",
    "CLOUDINARY_QUOTA_LIMIT_PERCENT",
)


def _clear_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_crawler_settings_defaults_are_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings(monkeypatch)

    settings = CrawlerSettings.from_environment()

    assert settings.max_workers == 4
    assert settings.image_allowed_hosts == ("static.acgsecrets.hk",)
    assert settings.image_max_pixels == 40_000_000
    assert settings.maximum_parse_failure_ratio == 0
    assert settings.maximum_fallback_id_ratio == 0
    assert settings.cloudinary_quota_limit_percent == 90


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("CRAWLER_MAX_WORKERS", "not-int", "must be an integer"),
        ("QUALITY_MIN_COUNT_RATIO", "not-float", "must be a number"),
        ("CRAWLER_MAX_WORKERS", "0", "between 1 and 8"),
        ("QUALITY_MIN_COUNT_RATIO", "0", "greater than 0"),
        ("QUALITY_MAX_PARSE_FAILURE_RATIO", "1", "below 1"),
        ("QUALITY_MAX_FALLBACK_ID_RATIO", "-0.1", "at least 0"),
        ("IMAGE_MAX_PIXELS", "0", "between 1 and 100000000"),
        ("IMAGE_MAX_PIXELS", "100000001", "between 1 and 100000000"),
        ("IMAGE_ALLOWED_HOSTS", " , ", "may not be empty"),
    ],
)
def test_crawler_settings_reject_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    message: str,
) -> None:
    _clear_settings(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(ConfigurationError, match=message):
        CrawlerSettings.from_environment()


def test_cloudinary_credentials_are_required_and_trimmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = (
        "CLOUDINARY_CLOUD_NAME",
        "CLOUDINARY_API_KEY",
        "CLOUDINARY_API_SECRET",
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ConfigurationError, match="Missing required"):
        required_cloudinary_credentials()

    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", " cloud ")
    monkeypatch.setenv("CLOUDINARY_API_KEY", " key ")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", " secret ")
    assert required_cloudinary_credentials() == {
        "CLOUDINARY_CLOUD_NAME": "cloud",
        "CLOUDINARY_API_KEY": "key",
        "CLOUDINARY_API_SECRET": "secret",
    }


def test_project_paths_derive_every_output_from_configured_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "root"
    output = tmp_path / "published"
    monkeypatch.setattr(settings_module, "PROJECT_ROOT", project_root)
    monkeypatch.setenv("OUTPUT_DIR", str(output))

    paths = ProjectPaths.from_environment()

    assert paths.root == project_root
    assert paths.output_dir == output.resolve()
    assert paths.data_dir == output.resolve() / "data"
    assert paths.static_source_dir == project_root / "static"
    assert paths.static_output_dir == output.resolve() / "static"
