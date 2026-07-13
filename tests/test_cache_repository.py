from __future__ import annotations

import json
from pathlib import Path

import pytest

import services.cache_repository as cache_repository_module
from services.cache_repository import CacheRepository
from services.errors import DataContractError

SHARED_PUBLIC_ID = f"anime_covers/{'a' * 64}"
OTHER_PUBLIC_ID = f"anime_covers/{'b' * 64}"


@pytest.mark.parametrize(
    "content",
    [
        "{not-json",
        "[]",
        '{"valid-key": 123}',
    ],
)
def test_cache_rejects_invalid_json_or_non_string_mapping(
    tmp_path: Path,
    content: str,
) -> None:
    path = tmp_path / "cache.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(DataContractError, match="Invalid cache|must be"):
        CacheRepository(path)


def test_cache_saves_only_changes_and_returns_defensive_snapshot(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cache.json"
    cache = CacheRepository(path)
    assert cache.save_if_changed() is False

    cache.set("source-a", "https://res.cloudinary.com/demo/a.webp")
    snapshot = cache.snapshot()
    snapshot["source-a"] = "mutated outside repository"

    assert cache.get("source-a") == "https://res.cloudinary.com/demo/a.webp"
    assert cache.save_if_changed() is True
    assert cache.save_if_changed() is False
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "source-a": "https://res.cloudinary.com/demo/a.webp"
    }


def test_cache_atomic_write_failure_keeps_existing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "cache.json"
    path.write_text('{"source-a": "old"}', encoding="utf-8")
    cache = CacheRepository(path)
    cache.set("source-a", "new")

    def fail_write(target: Path, payload: object) -> None:
        raise OSError("simulated cache write failure")

    monkeypatch.setattr(
        cache_repository_module,
        "atomic_write_json",
        fail_write,
    )

    with pytest.raises(OSError, match="simulated cache write failure"):
        cache.save_if_changed()

    assert path.read_text(encoding="utf-8") == '{"source-a": "old"}'


def test_cache_removes_only_urls_matching_confirmed_public_ids(
    tmp_path: Path,
) -> None:
    cache = CacheRepository(tmp_path / "cache.json")
    shared = f"https://res.cloudinary.com/demo/image/upload/v1/{SHARED_PUBLIC_ID}.webp"
    cache.set("source-a", shared)
    cache.set("content-a", shared)
    cache.set(
        "other",
        f"https://res.cloudinary.com/demo/image/upload/v1/{OTHER_PUBLIC_ID}.webp",
    )

    removed = cache.remove_urls_with_public_ids({SHARED_PUBLIC_ID})

    assert removed == 2
    assert cache.snapshot() == {
        "other": (
            f"https://res.cloudinary.com/demo/image/upload/v1/{OTHER_PUBLIC_ID}.webp"
        )
    }


def test_cache_reports_candidate_urls_without_mutating_data(tmp_path: Path) -> None:
    cache = CacheRepository(tmp_path / "cache.json")
    shared = f"https://res.cloudinary.com/demo/image/upload/v1/{SHARED_PUBLIC_ID}.webp"
    other = f"https://res.cloudinary.com/demo/image/upload/v1/{OTHER_PUBLIC_ID}.webp"
    cache.set("source-a", shared)
    cache.set("content-a", shared)
    cache.set("other", other)

    matches = cache.urls_with_public_ids({SHARED_PUBLIC_ID})

    assert matches == {shared}
    assert cache.snapshot() == {
        "source-a": shared,
        "content-a": shared,
        "other": other,
    }


def test_cache_does_not_treat_nested_or_path_collision_urls_as_managed(
    tmp_path: Path,
) -> None:
    cache = CacheRepository(tmp_path / "cache.json")
    valid = f"https://res.cloudinary.com/demo/image/upload/v1/{SHARED_PUBLIC_ID}.webp"
    nested = (
        "https://res.cloudinary.com/demo/image/upload/v1/anime_covers/series/"
        f"{'a' * 64}.webp"
    )
    collision = (
        "https://res.cloudinary.com/anime_covers/image/upload/v1/unmanaged/"
        f"{'a' * 64}.webp"
    )
    cache.set("valid", valid)
    cache.set("nested", nested)
    cache.set("collision", collision)

    removed = cache.remove_urls_with_public_ids({SHARED_PUBLIC_ID})

    assert removed == 1
    assert cache.snapshot() == {"nested": nested, "collision": collision}


def test_cache_rejects_public_ids_outside_exact_uploader_contract(
    tmp_path: Path,
) -> None:
    cache = CacheRepository(tmp_path / "cache.json")

    with pytest.raises(DataContractError, match="exact managed Cloudinary public IDs"):
        cache.urls_with_public_ids({"anime_covers/nested/asset"})
