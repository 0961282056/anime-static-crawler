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
    PreparedDeletion,
    RetentionPlan,
    RetentionPlanner,
    cloudinary_public_id_from_url,
    is_managed_public_id,
    referenced_public_ids,
)

SHARED_DIGEST = "a" * 64
SHARED_PUBLIC_ID = f"anime_covers/{SHARED_DIGEST}"
LEGACY_DIGEST = "b" * 32
LEGACY_PUBLIC_ID = f"anime_covers/{LEGACY_DIGEST}"
INVENTORY_ONE = f"anime_covers/{'1' * 64}"
INVENTORY_TWO = f"anime_covers/{'2' * 32}"


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
        f"https://res.cloudinary.com/demo/image/upload/v1/{SHARED_PUBLIC_ID}.webp"
    )
    shared_jpg = (
        "https://res.cloudinary.com/demo/image/upload/f_auto,q_auto/"
        f"v2/{SHARED_PUBLIC_ID}.jpg"
    )
    _write_references(data_dir / "2026_春.json", shared_webp)
    _write_references(data_dir / "2026_夏.json", shared_jpg)

    assert referenced_public_ids(data_dir) == {SHARED_PUBLIC_ID}


@pytest.mark.parametrize("public_id", [SHARED_PUBLIC_ID, LEGACY_PUBLIC_ID])
def test_public_id_parser_accepts_exact_uploader_contract_after_upload_marker(
    public_id: str,
) -> None:
    url = (
        "https://res.cloudinary.com/anime_covers/image/upload/"
        f"f_auto,q_auto/v42/{public_id}.webp?cache-bust=1#cover"
    )

    assert cloudinary_public_id_from_url(url) == public_id


@pytest.mark.parametrize("public_id", [SHARED_PUBLIC_ID, LEGACY_PUBLIC_ID])
def test_managed_public_id_accepts_current_and_legacy_digests(public_id: str) -> None:
    assert is_managed_public_id(public_id) is True


@pytest.mark.parametrize(
    "url",
    [
        (
            "https://res.cloudinary.com/demo/image/upload/v1/"
            f"anime_covers/series/{SHARED_DIGEST}.webp"
        ),
        (
            "https://res.cloudinary.com/anime_covers/image/upload/v1/"
            f"unmanaged/{SHARED_DIGEST}.webp"
        ),
        (
            "https://res.cloudinary.com/demo/image/upload/anime_covers/v1/"
            f"unmanaged/{SHARED_DIGEST}.webp"
        ),
        (
            "https://res.cloudinary.com.evil.example/demo/image/upload/v1/"
            f"{SHARED_PUBLIC_ID}.webp"
        ),
        (
            "https://res.cloudinary.com/demo/image/upload/v1/anime_covers/"
            f"{'a' * 31}.webp"
        ),
        (
            "https://res.cloudinary.com/demo/image/upload/v1/anime_covers/"
            f"{'A' * 32}.webp"
        ),
    ],
)
def test_public_id_parser_rejects_nested_and_path_collision_urls(url: str) -> None:
    assert cloudinary_public_id_from_url(url) is None


def test_retention_plan_never_deletes_referenced_or_too_recent_resources(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shared_url = (
        f"https://res.cloudinary.com/demo/image/upload/v1/{SHARED_PUBLIC_ID}.webp"
    )
    _write_references(data_dir / "2026_夏.json", shared_url)
    referenced = referenced_public_ids(data_dir)
    now = datetime(2026, 7, 10, 8, 0, tzinfo=UTC)

    plan = RetentionPlanner().build(
        referenced=referenced,
        cloud_resources={
            SHARED_PUBLIC_ID: now - timedelta(days=365),
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
    assert SHARED_PUBLIC_ID not in plan.delete_candidates
    assert "anime_covers/recent-unreferenced" not in plan.delete_candidates


class _CacheSpy:
    def __init__(self, *cached_public_ids: str) -> None:
        self.removed: set[str] | None = None
        self.saved = False
        self.cached_public_ids = set(cached_public_ids)

    def urls_with_public_ids(self, public_ids: set[str] | tuple[str, ...]) -> set[str]:
        matches = self.cached_public_ids & set(public_ids)
        return {
            f"https://res.cloudinary.com/demo/image/upload/v1/{public_id}.webp"
            for public_id in matches
        }

    def remove_urls_with_public_ids(
        self, public_ids: set[str] | tuple[str, ...]
    ) -> int:
        self.removed = set(public_ids)
        matches = self.cached_public_ids & set(public_ids)
        self.cached_public_ids -= matches
        return len(matches)

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
                        "public_id": INVENTORY_ONE,
                        "created_at": "2026-01-01T00:00:00Z",
                    }
                ],
                "next_cursor": "page-two",
            },
            {
                "resources": [
                    {
                        "public_id": INVENTORY_TWO,
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

    assert set(inventory) == {INVENTORY_ONE, INVENTORY_TWO}
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
                    "public_id": INVENTORY_ONE,
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


def test_retention_rejects_nested_resource_inside_managed_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(_CacheSpy())
    monkeypatch.setattr(
        retention_module.cloudinary.api,
        "resources",
        lambda **kwargs: {
            "resources": [
                {
                    "public_id": f"anime_covers/series/{SHARED_DIGEST}",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]
        },
    )

    with pytest.raises(RetentionError, match="exact managed public ID contract"):
        service.list_cloud_resources()


def test_retention_prepare_requires_confirmation_and_aged_manifest() -> None:
    service = _service(_CacheSpy())
    plan = _plan("anime_covers/old")
    aged = datetime.now(UTC) - timedelta(days=31)

    with pytest.raises(RetentionError, match="requires --confirm"):
        service.prepare_deletion(
            plan,
            confirmation="wrong",
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=aged,
        )

    with pytest.raises(RetentionError, match="must include a timezone"):
        service.prepare_deletion(
            plan,
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=datetime(2026, 1, 1),
        )

    with pytest.raises(RetentionError, match="must age"):
        service.prepare_deletion(
            plan,
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=datetime.now(UTC),
        )


def test_retention_prepare_enforces_absolute_cap_without_deletion(
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
        service.prepare_deletion(
            plan,
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates=set(plan.delete_candidates),
            manifest_created_at=datetime.now(UTC) - timedelta(days=31),
            max_delete=1,
            max_fraction=0.02,
        )

    assert cache.removed is None
    assert cache.saved is False


def test_retention_prepare_never_deletes_and_invalidates_cache_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _CacheSpy("anime_covers/old")
    service = _service(cache)
    plan = _plan("anime_covers/old")
    monkeypatch.setattr(
        retention_module.cloudinary.api,
        "delete_resources",
        lambda *args, **kwargs: pytest.fail("prepare must never delete"),
    )

    prepared = service.prepare_deletion(
        plan,
        confirmation=CONFIRMATION_PHRASE,
        manifest_candidates={"anime_covers/old"},
        manifest_created_at=datetime.now(UTC) - timedelta(days=31),
    )

    assert prepared.delete_candidates == ("anime_covers/old",)
    assert cache.removed is None
    assert cache.saved is False

    removed_entries = service.invalidate_prepared_cache(prepared)

    assert removed_entries == 1
    assert cache.removed == {"anime_covers/old"}
    assert cache.saved is True


def test_retention_execute_prepared_returns_confirmed_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _CacheSpy("anime_covers/one", "anime_covers/two")
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

    prepared = service.prepare_deletion(
        plan,
        confirmation=CONFIRMATION_PHRASE,
        manifest_candidates=set(plan.delete_candidates),
        manifest_created_at=datetime.now(UTC) - timedelta(days=31),
    )
    assert service.invalidate_prepared_cache(prepared) == 2
    removed = service.execute_prepared(
        plan,
        prepared,
        confirmation=CONFIRMATION_PHRASE,
        manifest_candidates=set(plan.delete_candidates),
        manifest_created_at=datetime.now(UTC) - timedelta(days=31),
    )

    assert removed == {"anime_covers/one", "anime_covers/two"}
    assert cache.removed == removed
    assert cache.saved is True


def test_retention_prepare_hard_limits_cannot_be_relaxed(
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
        service.prepare_deletion(
            plan,
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=aged,
            grace_days=29,
        )
    with pytest.raises(RetentionError, match="between 1 and 50"):
        service.prepare_deletion(
            plan,
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=aged,
            max_delete=51,
        )
    with pytest.raises(RetentionError, match="at most 0.02"):
        service.prepare_deletion(
            plan,
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=aged,
            max_fraction=0.03,
        )


def test_retention_prepare_two_percent_cap_may_allow_zero_deletions(
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
        service.prepare_deletion(
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


def test_retention_execute_prepared_rejects_unexpected_delete_keys(
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
        service.execute_prepared(
            plan,
            PreparedDeletion(30, 100, ("anime_covers/old",)),
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=datetime.now(UTC) - timedelta(days=31),
        )

    assert cache.removed is None
    assert cache.saved is False


def test_retention_execute_prepared_rejects_invalid_delete_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(_CacheSpy())
    plan = _plan("anime_covers/old")
    monkeypatch.setattr(
        retention_module.cloudinary.api,
        "delete_resources",
        lambda *args, **kwargs: {"deleted": []},
    )

    with pytest.raises(RetentionError, match="valid deleted map"):
        service.execute_prepared(
            plan,
            PreparedDeletion(30, 100, ("anime_covers/old",)),
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=datetime.now(UTC) - timedelta(days=31),
        )


def test_retention_prepares_only_manifest_and_current_plan_intersection(
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

    prepared = service.prepare_deletion(
        plan,
        confirmation=CONFIRMATION_PHRASE,
        manifest_candidates={"anime_covers/still-old", "anime_covers/no-longer-old"},
        manifest_created_at=datetime.now(UTC) - timedelta(days=31),
    )
    service.invalidate_prepared_cache(prepared)
    removed = service.execute_prepared(
        plan,
        prepared,
        confirmation=CONFIRMATION_PHRASE,
        manifest_candidates={"anime_covers/still-old", "anime_covers/no-longer-old"},
        manifest_created_at=datetime.now(UTC) - timedelta(days=31),
    )

    assert prepared.delete_candidates == ("anime_covers/still-old",)
    assert removed == {"anime_covers/still-old"}
    assert deleted_batches == [["anime_covers/still-old"]]


def test_retention_pre_delete_check_stops_before_api(
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
        service.execute_prepared(
            plan,
            PreparedDeletion(30, 100, ("anime_covers/old",)),
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=datetime.now(UTC) - timedelta(days=31),
            pre_delete_check=changed_main,
        )

    assert cache.removed is None
    assert cache.saved is False


def test_retention_execute_prepared_rejects_remaining_candidate_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _CacheSpy("anime_covers/old")
    service = _service(cache)
    plan = _plan("anime_covers/old")
    monkeypatch.setattr(
        retention_module.cloudinary.api,
        "delete_resources",
        lambda *args, **kwargs: pytest.fail("delete must not run"),
    )

    with pytest.raises(RetentionError, match="cache invalidation is not present"):
        service.execute_prepared(
            plan,
            PreparedDeletion(30, 100, ("anime_covers/old",)),
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=datetime.now(UTC) - timedelta(days=31),
        )


def test_retention_execute_prepared_rejects_candidate_no_longer_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(_CacheSpy())
    current_plan = _plan("anime_covers/different")
    monkeypatch.setattr(
        retention_module.cloudinary.api,
        "delete_resources",
        lambda *args, **kwargs: pytest.fail("delete must not run"),
    )

    with pytest.raises(RetentionError, match="no longer present"):
        service.execute_prepared(
            current_plan,
            PreparedDeletion(30, 100, ("anime_covers/old",)),
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=datetime.now(UTC) - timedelta(days=31),
        )


def test_legacy_one_step_execute_is_fail_closed() -> None:
    service = _service(_CacheSpy())

    with pytest.raises(RetentionError, match="One-step retention execution"):
        service.execute(
            _plan("anime_covers/old"),
            confirmation=CONFIRMATION_PHRASE,
            manifest_candidates={"anime_covers/old"},
            manifest_created_at=datetime.now(UTC) - timedelta(days=31),
        )
