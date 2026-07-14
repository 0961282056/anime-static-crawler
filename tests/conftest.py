from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from services.settings import ProjectPaths


@pytest.fixture
def fixture_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def anime_record_factory() -> Callable[..., dict[str, str]]:
    def factory(
        index: int,
        *,
        name: str | None = None,
        story: str = "測試簡介",
    ) -> dict[str, str]:
        return {
            "bangumi_id": f"anime-{index:04d}",
            "anime_name": name or f"測試動畫 {index}",
            "anime_image_url": (
                "https://res.cloudinary.com/test-cloud/image/upload/"
                f"v1/anime_covers/{index:064x}.webp"
            ),
            "premiere_date": "一",
            "premiere_time": "12:00",
            "story": story,
        }

    return factory


@pytest.fixture
def project_paths(tmp_path: Path) -> ProjectPaths:
    root = tmp_path / "project"
    output_dir = root / "dist"
    root.mkdir()
    return ProjectPaths(
        root=root,
        output_dir=output_dir,
        data_dir=output_dir / "data",
        templates_dir=root / "templates",
        static_source_dir=root / "static",
        static_output_dir=output_dir / "static",
        cache_file=root / "cloudinary_cache.json",
        cloudflare_headers_file=root / "_headers",
    )
