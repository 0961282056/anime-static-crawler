"""Reference-aware Cloudinary retention.

This module never deletes quarterly JSON. Deletion is manual, dry-run by
default, and limited to Cloudinary resources that no current JSON references.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

import cloudinary
import cloudinary.api

from services.cache_repository import CacheRepository
from services.data_repository import DataQualityPolicy, DataRepository
from services.errors import DataContractError, RetentionError
from services.settings import required_cloudinary_credentials

logger = logging.getLogger(__name__)
CONFIRMATION_PHRASE = "DELETE_UNREFERENCED"
MINIMUM_RESOURCE_AGE_DAYS = 30
MINIMUM_MANIFEST_GRACE_DAYS = 30
MAXIMUM_DELETE_COUNT = 50
MAXIMUM_DELETE_FRACTION = 0.02
# The original uploader used an MD5 content digest (32 hex characters). New
# uploads use SHA-256 (64 hex characters). Both formats are project-managed
# assets; arbitrary names and nested paths remain outside the contract.
MANAGED_PUBLIC_ID_PATTERN = re.compile(r"anime_covers/[0-9a-f]{32}(?:[0-9a-f]{32})?\Z")
_CLOUDINARY_DELIVERY_PATH_PATTERN = re.compile(
    r"/[^/]+/image/upload/(?:[^/]+/)*"
    r"(?P<public_id>anime_covers/[0-9a-f]{32}(?:[0-9a-f]{32})?)"
    r"(?:\.[A-Za-z0-9]+)?\Z"
)


def is_managed_public_id(public_id: object) -> bool:
    """Return whether an ID is one this project's uploader can create."""
    return isinstance(public_id, str) and bool(
        MANAGED_PUBLIC_ID_PATTERN.fullmatch(public_id)
    )


def cloudinary_public_id_from_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        path = unquote(parsed.path)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname != "res.cloudinary.com"
        ):
            return None
    except ValueError:
        return None
    match = _CLOUDINARY_DELIVERY_PATH_PATTERN.fullmatch(path)
    return match.group("public_id") if match else None


def referenced_public_ids(data_dir: Path) -> set[str]:
    referenced: set[str] = set()
    repository = DataRepository(data_dir, DataQualityPolicy())
    paths = repository.validate_all()
    if not paths:
        raise DataContractError(
            f"Refusing retention because no canonical quarterly JSON exists in {data_dir}"
        )
    for path in paths:
        dataset = repository.load_path(path)
        for record in dataset.anime_list:
            public_id = cloudinary_public_id_from_url(record.anime_image_url)
            if public_id:
                referenced.add(public_id)
    return referenced


@dataclass(frozen=True)
class RetentionPlan:
    created_at: datetime
    minimum_age_days: int
    referenced: frozenset[str]
    cloud_resources: frozenset[str]
    delete_candidates: tuple[str, ...]


@dataclass(frozen=True)
class PreparedDeletion:
    """The exact deletion set approved during the reversible prepare stage."""

    minimum_age_days: int
    inventory_count: int
    delete_candidates: tuple[str, ...]


class RetentionPlanner:
    def build(
        self,
        *,
        referenced: set[str],
        cloud_resources: dict[str, datetime],
        minimum_age_days: int = 30,
        now: datetime | None = None,
    ) -> RetentionPlan:
        if minimum_age_days < MINIMUM_RESOURCE_AGE_DAYS:
            raise RetentionError(
                "Retention resources must be at least "
                f"{MINIMUM_RESOURCE_AGE_DAYS} days old"
            )
        plan_time = now or datetime.now(UTC)
        cutoff = plan_time - timedelta(days=minimum_age_days)
        candidates = tuple(
            sorted(
                public_id
                for public_id, created_at in cloud_resources.items()
                if public_id not in referenced and created_at <= cutoff
            )
        )
        return RetentionPlan(
            created_at=plan_time,
            minimum_age_days=minimum_age_days,
            referenced=frozenset(referenced),
            cloud_resources=frozenset(cloud_resources),
            delete_candidates=candidates,
        )


class CloudinaryRetentionService:
    def __init__(self, data_dir: Path, cache: CacheRepository) -> None:
        credentials = required_cloudinary_credentials()
        cloudinary.config(
            cloud_name=credentials["CLOUDINARY_CLOUD_NAME"],
            api_key=credentials["CLOUDINARY_API_KEY"],
            api_secret=credentials["CLOUDINARY_API_SECRET"],
            secure=True,
        )
        self.data_dir = data_dir
        self.cache = cache

    def list_cloud_resources(
        self, folder_prefix: str = "anime_covers/"
    ) -> dict[str, datetime]:
        resources: dict[str, datetime] = {}
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            try:
                result = cloudinary.api.resources(
                    type="upload",
                    prefix=folder_prefix,
                    max_results=500,
                    next_cursor=cursor,
                )
            except Exception as exc:
                raise RetentionError(
                    f"Unable to list Cloudinary resources: {exc}"
                ) from exc
            if not isinstance(result, dict):
                raise RetentionError("Cloudinary returned an invalid resource response")
            page_resources = result.get("resources")
            if not isinstance(page_resources, list):
                raise RetentionError(
                    "Cloudinary resource response omitted the resources list"
                )
            next_cursor = result.get("next_cursor")
            if not page_resources and next_cursor:
                raise RetentionError(
                    "Cloudinary returned an empty resource page with a continuation "
                    "cursor; verify that the API key has a product-environment role "
                    "with read access"
                )
            for resource in page_resources:
                public_id = resource.get("public_id")
                created_at = resource.get("created_at")
                if not public_id or not created_at:
                    continue
                if not isinstance(public_id, str) or not public_id.startswith(
                    folder_prefix
                ):
                    raise RetentionError(
                        "Cloudinary returned a resource outside the requested folder "
                        f"prefix: {public_id}"
                    )
                if not is_managed_public_id(public_id):
                    raise RetentionError(
                        "Cloudinary returned a resource that does not match the exact "
                        "managed public ID contract "
                        "anime_covers/<32 or 64 lowercase hex>: "
                        f"{public_id}"
                    )
                try:
                    parsed_created_at = datetime.fromisoformat(
                        str(created_at).replace("Z", "+00:00")
                    )
                except ValueError:
                    logger.warning(
                        "Skipping resource with invalid created_at: %s", public_id
                    )
                    continue
                if parsed_created_at.tzinfo is None:
                    parsed_created_at = parsed_created_at.replace(tzinfo=UTC)
                resources[public_id] = parsed_created_at.astimezone(UTC)
            if not next_cursor:
                break
            if not isinstance(next_cursor, str):
                raise RetentionError(
                    "Cloudinary returned an invalid continuation cursor"
                )
            if next_cursor in seen_cursors:
                raise RetentionError(
                    "Cloudinary repeated a continuation cursor; refusing an unsafe "
                    "or incomplete inventory"
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        if not resources:
            raise RetentionError(
                "Cloudinary inventory is empty; verify the cloud name and API key "
                "read permission before running retention"
            )
        return resources

    def plan(self, *, minimum_age_days: int = 30) -> RetentionPlan:
        referenced = referenced_public_ids(self.data_dir)
        cloud_resources = self.list_cloud_resources()
        if referenced and not cloud_resources:
            raise RetentionError(
                "Cloudinary inventory is empty while quarterly data still references "
                "Cloudinary assets; verify the cloud name and API key read permission"
            )
        return RetentionPlanner().build(
            referenced=referenced,
            cloud_resources=cloud_resources,
            minimum_age_days=minimum_age_days,
        )

    @staticmethod
    def _validate_execution_controls(
        plan: RetentionPlan,
        *,
        confirmation: str,
        manifest_created_at: datetime,
        grace_days: int,
        max_delete: int,
        max_fraction: float,
    ) -> None:
        if confirmation != CONFIRMATION_PHRASE:
            raise RetentionError(f"Execution requires --confirm {CONFIRMATION_PHRASE}")
        if plan.minimum_age_days < MINIMUM_RESOURCE_AGE_DAYS:
            raise RetentionError("Retention plan uses an unsafe resource age")
        if grace_days < MINIMUM_MANIFEST_GRACE_DAYS:
            raise RetentionError(
                "Manifest grace period may not be shorter than "
                f"{MINIMUM_MANIFEST_GRACE_DAYS} days"
            )
        if not 1 <= max_delete <= MAXIMUM_DELETE_COUNT:
            raise RetentionError(
                f"Deletion cap must be between 1 and {MAXIMUM_DELETE_COUNT}"
            )
        if not 0 < max_fraction <= MAXIMUM_DELETE_FRACTION:
            raise RetentionError(
                "Deletion fraction must be greater than 0 and at most "
                f"{MAXIMUM_DELETE_FRACTION}"
            )
        if manifest_created_at.tzinfo is None:
            raise RetentionError("Manifest created_at must include a timezone")
        if datetime.now(UTC) - manifest_created_at < timedelta(days=grace_days):
            raise RetentionError(
                f"Retention manifest must age for at least {grace_days} days"
            )

    @staticmethod
    def _enforce_candidate_cap(
        plan: RetentionPlan,
        candidates: tuple[str, ...],
        *,
        max_delete: int,
        max_fraction: float,
    ) -> None:
        if not candidates:
            return
        fractional_limit = math.floor(len(plan.cloud_resources) * max_fraction)
        effective_limit = min(max_delete, fractional_limit)
        if len(candidates) > effective_limit:
            raise RetentionError(
                f"Deletion plan contains {len(candidates)} resources; "
                f"the safety cap is {effective_limit}"
            )

    def prepare_deletion(
        self,
        plan: RetentionPlan,
        *,
        confirmation: str,
        manifest_candidates: set[str],
        manifest_created_at: datetime,
        grace_days: int = 30,
        max_delete: int = 50,
        max_fraction: float = 0.02,
        pre_prepare_check: Callable[[], None] | None = None,
    ) -> PreparedDeletion:
        """Validate an aged manifest and freeze its current safe intersection.

        This stage is deliberately reversible: it never calls Cloudinary's delete
        API. The caller can persist the returned candidate set and invalidate its
        cache references in a separately reviewed commit.
        """
        self._validate_execution_controls(
            plan,
            confirmation=confirmation,
            manifest_created_at=manifest_created_at,
            grace_days=grace_days,
            max_delete=max_delete,
            max_fraction=max_fraction,
        )
        candidates = tuple(
            sorted(set(plan.delete_candidates) & set(manifest_candidates))
        )
        self._enforce_candidate_cap(
            plan,
            candidates,
            max_delete=max_delete,
            max_fraction=max_fraction,
        )
        if pre_prepare_check:
            pre_prepare_check()
        return PreparedDeletion(
            minimum_age_days=plan.minimum_age_days,
            inventory_count=len(plan.cloud_resources),
            delete_candidates=candidates,
        )

    def invalidate_prepared_cache(self, prepared: PreparedDeletion) -> int:
        """Atomically persist cache invalidation for a prepared deletion set."""
        removed = self.cache.remove_urls_with_public_ids(prepared.delete_candidates)
        self.cache.save_if_changed()
        return removed

    def execute_prepared(
        self,
        plan: RetentionPlan,
        prepared: PreparedDeletion,
        *,
        confirmation: str,
        manifest_candidates: set[str],
        manifest_created_at: datetime,
        grace_days: int = 30,
        max_delete: int = 50,
        max_fraction: float = 0.02,
        pre_delete_check: Callable[[], None] | None = None,
    ) -> set[str]:
        """Delete only a prepared set after all safety checks are repeated."""
        self._validate_execution_controls(
            plan,
            confirmation=confirmation,
            manifest_created_at=manifest_created_at,
            grace_days=grace_days,
            max_delete=max_delete,
            max_fraction=max_fraction,
        )
        if prepared.minimum_age_days != plan.minimum_age_days:
            raise RetentionError(
                "Prepared receipt minimum age does not match the current plan"
            )
        candidates = tuple(prepared.delete_candidates)
        if tuple(sorted(set(candidates))) != candidates:
            raise RetentionError(
                "Prepared deletion candidates must be unique and sorted"
            )
        current_safe = set(plan.delete_candidates) & set(manifest_candidates)
        no_longer_safe = set(candidates) - current_safe
        if no_longer_safe:
            raise RetentionError(
                "Prepared candidates are no longer present in the current safe "
                f"intersection: {sorted(no_longer_safe)[:5]}"
            )
        self._enforce_candidate_cap(
            plan,
            candidates,
            max_delete=max_delete,
            max_fraction=max_fraction,
        )
        cached_urls = self.cache.urls_with_public_ids(candidates)
        if cached_urls:
            raise RetentionError(
                "Prepared candidate cache invalidation is not present; refusing "
                f"Cloudinary deletion ({len(cached_urls)} cached URLs remain)"
            )

        if not candidates:
            return set()

        confirmed_removed: set[str] = set()
        for start in range(0, len(candidates), 100):
            if pre_delete_check:
                pre_delete_check()
            batch = list(candidates[start : start + 100])
            try:
                result = cloudinary.api.delete_resources(
                    batch,
                    resource_type="image",
                    type="upload",
                )
            except Exception as exc:
                raise RetentionError(
                    "Cloudinary deletion failed after candidate cache invalidation; "
                    "quarterly JSON was left intact"
                ) from exc
            if not isinstance(result, dict):
                raise RetentionError(
                    "Cloudinary returned an invalid deletion response; candidate "
                    "cache remains invalidated for safety"
                )
            statuses = result.get("deleted", {})
            if not isinstance(statuses, dict):
                raise RetentionError(
                    "Cloudinary deletion response omitted a valid deleted map; "
                    "candidate cache remains invalidated for safety"
                )
            requested = set(batch)
            batch_removed = {
                public_id
                for public_id in requested
                if statuses.get(public_id) in {"deleted", "not_found"}
            }
            unexpected = set(statuses) - requested
            if batch_removed != requested or unexpected:
                missing = sorted(requested - batch_removed)
                raise RetentionError(
                    "Cloudinary did not confirm every deletion; candidate cache "
                    "remains invalidated for safety. "
                    f"missing={missing[:5]}, unexpected={sorted(unexpected)[:5]}"
                )
            confirmed_removed.update(batch_removed)

        logger.info(
            "Removed %s unreferenced Cloudinary resources", len(confirmed_removed)
        )
        return confirmed_removed

    def execute(
        self,
        plan: RetentionPlan,
        *,
        confirmation: str,
        manifest_candidates: set[str],
        manifest_created_at: datetime,
        grace_days: int = 30,
        max_delete: int = 50,
        max_fraction: float = 0.02,
        pre_delete_check: Callable[[], None] | None = None,
    ) -> set[str]:
        """Reject the retired one-step deletion path.

        Keeping this method as a fail-closed compatibility shim prevents an old
        caller from silently regaining direct deletion capability.
        """
        del (
            plan,
            confirmation,
            manifest_candidates,
            manifest_created_at,
            grace_days,
            max_delete,
            max_fraction,
            pre_delete_check,
        )
        raise RetentionError(
            "One-step retention execution is disabled; prepare and merge cache "
            "invalidation before executing the prepared receipt"
        )
