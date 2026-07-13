from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import services.retention as retention_module
from services.errors import DataContractError, RetentionError
from services.retention import (
    CONFIRMATION_PHRASE,
    CloudinaryRetentionService,
    RetentionPlan,
    RetentionPlanner,
    referenced_public_ids,
)


def _write_references(path: Path, *image_urls: str) -> None:
    records = [
        {
            "bangumi_id": f"anime-{index}",
            "anime_name": f"動畫 {index}",
            "anime_image_url": image_url,
            "premiere_date": "一",
            "premiere_time": "12:00",
            "story": "測試簡介",
        }
        for index, image_url in enumerate(image_urls)
    ]
    payload = {
        "schema_version": 1,
        "anime_list": records,
        "generated_at": "2026-07-10T08:00:00+08:00",
        "source_url": "https://acgsecrets.hk/bangumi/202607/",
        "quality": {
            "source_count": len(records),
            "record_count": len(records),
            "parse_failure_count": 0,
            "fallback_id_count": 0,
            "missing_story_count": 0,
            "missing_date_count": 0,
            "missing_time_count": 0,
        },
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def test_shared_image_reference_is_deduplicated_across_quarters(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shared_webp = (
        "https://res.cloudinary.com/demo/image/upload/v1/anime_covers/shared-cover.webp"
    )
    shared_jpg = (
        "https://res.cloudinary.com/demo/image/upload/f_auto,q_auto/"
        "v2/anime_covers/shared-cover.jpg"
    )
    _write_references(data_dir / "2026_春.json", shared_webp)
    _write_references(data_dir / "2026_夏.json", shared_jpg)

    assert referenced_public_ids(data_dir) == {"anime_covers/shared-cover"}


def test_retention_plan_never_deletes_referenced_or_too_recent_resources(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shared_url = (
        "https://res.cloudinary.com/demo/image/upload/v1/anime_covers/shared-cover.webp"
    )
    _write_references(data_dir / "2026_夏.json", shared_url)
    referenced = referenced_public_ids(data_dir)
    now = datetime(2026, 7, 10, 8, 0, tzinfo=UTC)

    plan = RetentionPlanner().build(
        referenced=referenced,
        cloud_resources={
            "anime_covers/shared-cover": now - timedelta(days=365),
            "anime_covers/old-unreferenced": now - timedelta(days=31),
            "anime_covers/at-cutoff": now - timedelta(days=30),
            "anime_covers/recent-unreferenced": now - timedelta(days=29),
        },
        minimum_age_days=30,
        now=now,
    )

    assert plan.delete_candidates == (
        "anime_covers/at-cutoff",
        "anime_covers/old-unreferenced",
    )
    assert "anime_covers/shared-cover" not in plan.delete_candidates
    assert "anime_covers/recent-unreferenced" not in plan.delete_candidates


class _CacheSpy:
    def __init__(self) -> None:
        self.removed: set[str] | None = None
        self.saved = False

    def remove_urls_with_public_ids(self, public_ids: set[str]) -> int:
        self.removed = set(public_ids)
        return len(public_ids)

    def save_if_changed(self) -> bool:
        self.saved = True
        return True


def _service(cache: _CacheSpy) -> CloudinaryRetentionService:
    service = object.__new__(CloudinaryRetentionService)
    service.cache = cache
    service.data_dir = Path("unused")
    return service


def _plan(*candidates: str, inventory_size: int = 100) -> RetentionPlan:
    inventory = set(candidates)
    inventory.update(
        f"anime_covers/filler-{index}"
        for index in range(inventory_size - len(inventory))
    )
    return RetentionPlan(
        created_at=datetime.now(UTC),
        minimum_age_days=30,
        referenced=frozenset(),
        cloud_resources=frozenset(inventory),
        delete_candidates=tuple(candidates),
    )


def test_retention_rejects_permission_filtered_empty_resource_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(_CacheSpy())
    calls = 0

    def permission_filtered_page(**kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"resources": [], "next_cursor": "hidden-page"}

    monkeypatch.setattr(
        retention_module.cloudinary.api,
        "resources",
        permission_filtered_page,
    )

    with pytest.raises(RetentionError, match="API key.*read access"):
        service.list_cloud_resources()
    assert calls == 1


def test_retention_rejects_empty_cloudinary_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(_CacheSpy())
    monkeypatch.setattr(
        retention_module.cloudinary.api,
        "resources",
        lambda **kwargs: {"resources": []},
    )

    with pytest.raises(RetentionError, match="inventory is empty"):
        service.list_cloud_resources()


def test_retention_collects_normal_paginated_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(_CacheSpy())
    cursors: list[object] = []
    pages = iter(
        [
            {
                "resources": [
                    {
                        "public_id": "anime_covers/one",
                        "created_at": "2026-01-01T00:00:00Z",
                    }
                ],
                "next_cursor": "page-two",
            },
            {
                "resources": [
                    {
                        "public_id": "anime_covers/two",
                        "created_at": "2026-01-02T00:00:00Z",
                    }
                ]
            },
        ]
    )

    def next_page(**kwargs: object) -> dict[str, object]:
        cursors.append(kwargs["next_cursor"])
        return next(pages)

    monkeypatch.setattr(retention_module.cloudinary.api, "resources", next_page)

    inventory = service.list_cloud_resources()

    assert set(inventory) == {"anime_covers/one", "anime_covers/two"}
    assert cursors == [None, "page-two"]


def test_retention_rejects_repeated_continuation_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(_CacheSpy())
    monkeypatch.setattr(
        retention_module.cloudinary.api,
        "resources",
        lambda **kwargs: {
            "resources": [
                {
                    "public_id": "anime_covers/one",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
            "next_cursor": "repeated",
        },
    )

    with pytest.raises(RetentionError, match="repeated a continuation cursor"):
        service.list_cloud_resources()


def test_retention_rejects_empty_inventory_when_data_has_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(_CacheSpy())
    monkeypatch.setattr(
        retention_module,
        "referenced_public_ids",
        lambda data_dir: {"anime_covers/referenced"},
    )
    monkeypatch.setattr(service, "list_cloud_resources", lambda: {})

    with pytest.raises(RetentionError, match="inventory is empty"):
        service.plan()


def test_retention_rejects_resource_outside_requested_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(_CacheSpy())
    monkeypatch.setattr(
        retention_module.cloudinary.api,
        "resources",
        lambda **kwargs: {
            "resources": [
                {
                    "public_id": "unrelated/asset",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]
        },
    )

    with pytest.raises(RetentionError, match="outside the requested folder"):
        service.list_cloud_resources()


def test_retention_execute_requires_confirmation_and_aged_manifest() -> None:
    service = _service(_CacheSpy())
    plan = _plan("anime_covers/old")
    aged = datetime.now(UTC) - timedelta(days=31)

    with pytest.raises(RetentionError, match="requires --confirm"):
        service.execute(
            plan,
            confirmation="wrong",
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=aged,
        )

    with pytest.raises(RetentionError, match="must include a timezone"):
        service.execute(
            plan,
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=datetime(2026, 1, 1),
        )

    with pytest.raises(RetentionError, match="must age"):
        service.execute(
            plan,
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=datetime.now(UTC),
        )


def test_retention_execute_enforces_absolute_cap_before_deletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _CacheSpy()
    service = _service(cache)
    plan = _plan("anime_covers/one", "anime_covers/two")
    monkeypatch.setattr(
        retention_module.cloudinary.api,
        "delete_resources",
        lambda *args, **kwargs: pytest.fail("delete must not run"),
    )

    with pytest.raises(RetentionError, match="safety cap is 1"):
        service.execute(
            plan,
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates=set(plan.delete_candidates),
            manifest_created_at=datetime.now(UTC) - timedelta(days=31),
            max_delete=1,
            max_fraction=0.02,
        )

    assert cache.removed is None
    assert cache.saved is False


def test_retention_partial_delete_invalidates_candidate_cache_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _CacheSpy()
    service = _service(cache)
    plan = _plan("anime_covers/old")
    monkeypatch.setattr(
        retention_module.cloudinary.api,
        "delete_resources",
        lambda *args, **kwargs: {"deleted": {"anime_covers/old": "error"}},
    )

    with pytest.raises(RetentionError, match="did not confirm every deletion"):
        service.execute(
            plan,
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=datetime.now(UTC) - timedelta(days=31),
        )

    assert cache.removed == {"anime_covers/old"}
    assert cache.saved is True


def test_retention_invalidates_cache_and_returns_confirmed_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _CacheSpy()
    service = _service(cache)
    plan = _plan("anime_covers/one", "anime_covers/two")

    def delete_resources(
        batch: list[str],
        **kwargs: object,
    ) -> dict[str, dict[str, str]]:
        assert kwargs == {"resource_type": "image", "type": "upload"}
        return {
            "deleted": {
                batch[0]: "deleted",
                batch[1]: "not_found",
            }
        }

    monkeypatch.setattr(
        retention_module.cloudinary.api,
        "delete_resources",
        delete_resources,
    )

    removed = service.execute(
        plan,
        confirmation=CONFIRMATION_PHRASE,
        manifest_candidates=set(plan.delete_candidates),
        manifest_created_at=datetime.now(UTC) - timedelta(days=31),
    )

    assert removed == {"anime_covers/one", "anime_covers/two"}
    assert cache.removed == removed
    assert cache.saved is True


def test_retention_hard_limits_cannot_be_relaxed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(_CacheSpy())
    plan = _plan("anime_covers/old")
    aged = datetime.now(UTC) - timedelta(days=31)
    monkeypatch.setattr(
        retention_module.cloudinary.api,
        "delete_resources",
        lambda *args, **kwargs: pytest.fail("delete must not run"),
    )

    with pytest.raises(RetentionError, match="grace period"):
        service.execute(
            plan,
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=aged,
            grace_days=29,
        )
    with pytest.raises(RetentionError, match="between 1 and 50"):
        service.execute(
            plan,
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=aged,
            max_delete=51,
        )
    with pytest.raises(RetentionError, match="at most 0.02"):
        service.execute(
            plan,
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=aged,
            max_fraction=0.03,
        )


def test_retention_two_percent_cap_may_allow_zero_deletions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(_CacheSpy())
    plan = _plan("anime_covers/old", inventory_size=49)
    monkeypatch.setattr(
        retention_module.cloudinary.api,
        "delete_resources",
        lambda *args, **kwargs: pytest.fail("delete must not run"),
    )

    with pytest.raises(RetentionError, match="safety cap is 0"):
        service.execute(
            plan,
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=datetime.now(UTC) - timedelta(days=31),
        )


def test_retention_rejects_invalid_quarter_schema(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "2026_夏.json").write_text(
        json.dumps({"anime_list": [{"anime_image_url": "missing-contract"}]}),
        encoding="utf-8",
    )

    with pytest.raises(DataContractError, match="Invalid quarterly data"):
        referenced_public_ids(data_dir)


def test_retention_rejects_unexpected_cloudinary_delete_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _CacheSpy()
    service = _service(cache)
    plan = _plan("anime_covers/old")
    monkeypatch.setattr(
        retention_module.cloudinary.api,
        "delete_resources",
        lambda *args, **kwargs: {
            "deleted": {
                "anime_covers/old": "deleted",
                "anime_covers/unexpected": "deleted",
            }
        },
    )

    with pytest.raises(RetentionError, match="unexpected"):
        service.execute(
            plan,
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=datetime.now(UTC) - timedelta(days=31),
        )

    assert cache.removed == {"anime_covers/old"}
    assert cache.saved is True


def test_retention_executes_only_manifest_and_current_plan_intersection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _CacheSpy()
    service = _service(cache)
    plan = _plan("anime_covers/still-old", "anime_covers/new-candidate")
    deleted_batches: list[list[str]] = []

    def delete_resources(
        batch: list[str],
        **kwargs: object,
    ) -> dict[str, dict[str, str]]:
        deleted_batches.append(batch)
        return {"deleted": {public_id: "deleted" for public_id in batch}}

    monkeypatch.setattr(
        retention_module.cloudinary.api,
        "delete_resources",
        delete_resources,
    )

    removed = service.execute(
        plan,
        confirmation=CONFIRMATION_PHRASE,
        manifest_candidates={"anime_covers/still-old", "anime_covers/no-longer-old"},
        manifest_created_at=datetime.now(UTC) - timedelta(days=31),
    )

    assert removed == {"anime_covers/still-old"}
    assert deleted_batches == [["anime_covers/still-old"]]


def test_retention_pre_delete_check_stops_before_cache_or_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _CacheSpy()
    service = _service(cache)
    plan = _plan("anime_covers/old")
    monkeypatch.setattr(
        retention_module.cloudinary.api,
        "delete_resources",
        lambda *args, **kwargs: pytest.fail("delete must not run"),
    )

    def changed_main() -> None:
        raise RetentionError("remote main changed")

    with pytest.raises(RetentionError, match="remote main changed"):
        service.execute(
            plan,
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=datetime.now(UTC) - timedelta(days=31),
            pre_delete_check=changed_main,
        )

    assert cache.removed is None
    assert cache.saved is False
