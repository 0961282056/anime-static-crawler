"""Manual, reference-aware Cloudinary retention CLI.

The irreversible operation is deliberately split across two reviewed stages:

1. ``--prepare-execution`` validates an aged manifest, removes candidate URLs
   from the local cache, and writes a self-hashed execution receipt.
2. After that cache change is reviewed and merged, ``--execute-prepared``
   verifies the receipt and merged cache before calling Cloudinary deletion.

This tool never deletes quarterly JSON files.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from services.atomic_io import atomic_write_json
from services.cache_repository import CacheRepository
from services.errors import DataContractError, RetentionError
from services.retention import (
    CONFIRMATION_PHRASE,
    MAXIMUM_DELETE_COUNT,
    MAXIMUM_DELETE_FRACTION,
    MINIMUM_MANIFEST_GRACE_DAYS,
    MINIMUM_RESOURCE_AGE_DAYS,
    CloudinaryRetentionService,
    PreparedDeletion,
    RetentionPlan,
)
from services.settings import ProjectPaths, required_cloudinary_credentials

logger = logging.getLogger(__name__)
PROTECTED_EXECUTION_CONTEXT = "protected-github-environment"
EXECUTION_RECEIPT_SCHEMA_VERSION = 1
SHA1_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class ReviewedManifest:
    created_at: datetime
    cloud_name: str
    minimum_age_days: int
    candidates: frozenset[str]
    sha256: str


@dataclass(frozen=True)
class ExecutionReceipt:
    prepared_at: datetime
    base_sha: str
    cloud_name: str
    manifest_sha256: str
    minimum_age_days: int
    inventory_count: int
    delete_candidates: tuple[str, ...]
    cache_sha256: str
    receipt_sha256: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plan Cloudinary retention, prepare cache invalidation, or execute "
            "a previously prepared deletion receipt. The default is dry-run."
        )
    )
    parser.add_argument(
        "--minimum-age-days",
        type=int,
        default=30,
        help="Only plan resources older than this many days (default: 30)",
    )
    parser.add_argument(
        "--manifest-output",
        type=Path,
        help="Write the dry-run plan to this JSON file",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--prepare-execution",
        action="store_true",
        help="Invalidate candidate cache URLs and write an execution receipt",
    )
    modes.add_argument(
        "--execute-prepared",
        action="store_true",
        help="Execute an already prepared and merged execution receipt",
    )
    modes.add_argument(
        "--execute",
        action="store_true",
        help="Retired one-step execution flag; always rejected",
    )
    parser.add_argument(
        "--manifest-input",
        type=Path,
        help="Manifest created by an earlier dry-run",
    )
    parser.add_argument(
        "--execution-output",
        type=Path,
        help="Write the prepared execution receipt to this JSON file",
    )
    parser.add_argument(
        "--execution-input",
        type=Path,
        help="Prepared execution receipt to verify before deletion",
    )
    parser.add_argument(
        "--confirm",
        default="",
        help=f"Execution requires the exact phrase {CONFIRMATION_PHRASE}",
    )
    parser.add_argument(
        "--grace-days",
        type=int,
        default=30,
        help="Manifest review period before execution (default: 30)",
    )
    parser.add_argument(
        "--max-delete",
        type=int,
        default=50,
        help="Absolute per-run deletion cap (default: 50)",
    )
    parser.add_argument(
        "--max-fraction",
        type=float,
        default=0.02,
        help="Maximum inventory fraction per run (default: 0.02)",
    )
    return parser.parse_args()


def _canonical_digest(payload: dict, *, digest_key: str) -> str:
    unsigned = {key: value for key, value in payload.items() if key != digest_key}
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def manifest_payload(plan: RetentionPlan, *, cloud_name: str) -> dict:
    payload = {
        "schema_version": 3,
        "created_at": plan.created_at.isoformat(),
        "cloud_name": cloud_name,
        "minimum_age_days": plan.minimum_age_days,
        "inventory_count": len(plan.cloud_resources),
        "referenced_count": len(plan.referenced),
        "delete_candidates": list(plan.delete_candidates),
    }
    payload["manifest_sha256"] = manifest_digest(payload)
    return payload


def manifest_digest(payload: dict) -> str:
    return _canonical_digest(payload, digest_key="manifest_sha256")


def read_manifest(path: Path) -> ReviewedManifest:
    expected_keys = {
        "schema_version",
        "created_at",
        "cloud_name",
        "minimum_age_days",
        "inventory_count",
        "referenced_count",
        "delete_candidates",
        "manifest_sha256",
    }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(raw, dict)
            or type(raw.get("schema_version")) is not int
            or raw.get("schema_version") != 3
        ):
            raise ValueError("unsupported manifest schema_version")
        if set(raw) != expected_keys:
            raise ValueError("manifest fields do not match schema_version 3")
        supplied_digest = raw.get("manifest_sha256")
        if not isinstance(supplied_digest, str) or not hmac.compare_digest(
            supplied_digest,
            manifest_digest(raw),
        ):
            raise ValueError("manifest_sha256 does not match the manifest contents")
        created_at = datetime.fromisoformat(raw["created_at"])
        if created_at.tzinfo is None:
            raise ValueError("manifest created_at must include a timezone")
        cloud_name = raw["cloud_name"]
        if (
            not isinstance(cloud_name, str)
            or not cloud_name.strip()
            or cloud_name != cloud_name.strip()
        ):
            raise ValueError("manifest cloud_name may not be empty")
        if type(raw["minimum_age_days"]) is not int:
            raise ValueError("minimum_age_days must be an integer")
        minimum_age_days = raw["minimum_age_days"]
        if minimum_age_days < MINIMUM_RESOURCE_AGE_DAYS:
            raise ValueError(
                f"minimum_age_days must be at least {MINIMUM_RESOURCE_AGE_DAYS}"
            )
        if (
            type(raw["inventory_count"]) is not int
            or type(raw["referenced_count"]) is not int
        ):
            raise ValueError("manifest counts must be integers")
        inventory_count = raw["inventory_count"]
        referenced_count = raw["referenced_count"]
        candidate_list = raw["delete_candidates"]
        if not isinstance(candidate_list, list) or not all(
            isinstance(candidate, str)
            and candidate.startswith("anime_covers/")
            and len(candidate) > len("anime_covers/")
            for candidate in candidate_list
        ):
            raise ValueError("delete_candidates must contain strings")
        candidates = frozenset(candidate_list)
        if len(candidates) != len(candidate_list):
            raise ValueError("delete_candidates may not contain duplicates")
        if candidate_list != sorted(candidate_list):
            raise ValueError("delete_candidates must be sorted")
        if inventory_count < 0 or referenced_count < 0:
            raise ValueError("manifest counts may not be negative")
        if referenced_count > inventory_count or len(candidates) > inventory_count:
            raise ValueError("manifest counts are inconsistent")
        return ReviewedManifest(
            created_at=created_at,
            cloud_name=cloud_name,
            minimum_age_days=minimum_age_days,
            candidates=candidates,
            sha256=supplied_digest,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RetentionError(f"Invalid retention manifest {path}: {exc}") from exc


def load_manifest(path: Path) -> tuple[datetime, int, set[str]]:
    """Compatibility helper for callers that only need validated manifest data."""
    reviewed = read_manifest(path)
    return (
        reviewed.created_at,
        reviewed.minimum_age_days,
        set(reviewed.candidates),
    )


def receipt_digest(payload: dict) -> str:
    return _canonical_digest(payload, digest_key="receipt_sha256")


def execution_receipt_payload(
    prepared: PreparedDeletion,
    *,
    base_sha: str,
    cloud_name: str,
    manifest_sha256: str,
    cache_sha256: str,
    prepared_at: datetime | None = None,
) -> dict:
    payload = {
        "schema_version": EXECUTION_RECEIPT_SCHEMA_VERSION,
        "prepared_at": (prepared_at or datetime.now(UTC)).isoformat(),
        "base_sha": base_sha,
        "cloud_name": cloud_name,
        "manifest_sha256": manifest_sha256,
        "minimum_age_days": prepared.minimum_age_days,
        "inventory_count": prepared.inventory_count,
        "delete_candidates": list(prepared.delete_candidates),
        "cache_sha256": cache_sha256,
    }
    payload["receipt_sha256"] = receipt_digest(payload)
    return payload


def load_execution_receipt(path: Path) -> ExecutionReceipt:
    expected_keys = {
        "schema_version",
        "prepared_at",
        "base_sha",
        "cloud_name",
        "manifest_sha256",
        "minimum_age_days",
        "inventory_count",
        "delete_candidates",
        "cache_sha256",
        "receipt_sha256",
    }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(raw, dict)
            or type(raw.get("schema_version")) is not int
            or raw.get("schema_version") != EXECUTION_RECEIPT_SCHEMA_VERSION
        ):
            raise ValueError("unsupported execution receipt schema_version")
        if set(raw) != expected_keys:
            raise ValueError("receipt fields do not match schema_version 1")
        supplied_digest = raw.get("receipt_sha256")
        if not isinstance(supplied_digest, str) or not hmac.compare_digest(
            supplied_digest,
            receipt_digest(raw),
        ):
            raise ValueError("receipt_sha256 does not match the receipt contents")
        prepared_at = datetime.fromisoformat(raw["prepared_at"])
        if prepared_at.tzinfo is None:
            raise ValueError("receipt prepared_at must include a timezone")
        base_sha = raw["base_sha"]
        cloud_name = raw["cloud_name"]
        manifest_sha256 = raw["manifest_sha256"]
        cache_sha256 = raw["cache_sha256"]
        if not isinstance(base_sha, str) or not SHA1_PATTERN.fullmatch(base_sha):
            raise ValueError("receipt base_sha must be a 40-character Git SHA")
        if (
            not isinstance(cloud_name, str)
            or not cloud_name.strip()
            or cloud_name != cloud_name.strip()
        ):
            raise ValueError("receipt cloud_name may not be empty")
        if not isinstance(manifest_sha256, str) or not SHA256_PATTERN.fullmatch(
            manifest_sha256
        ):
            raise ValueError("receipt manifest_sha256 must be a SHA-256 digest")
        if not isinstance(cache_sha256, str) or not SHA256_PATTERN.fullmatch(
            cache_sha256
        ):
            raise ValueError("receipt cache_sha256 must be a SHA-256 digest")
        if (
            type(raw["minimum_age_days"]) is not int
            or type(raw["inventory_count"]) is not int
        ):
            raise ValueError("receipt age and inventory count must be integers")
        minimum_age_days = raw["minimum_age_days"]
        inventory_count = raw["inventory_count"]
        candidate_list = raw["delete_candidates"]
        if minimum_age_days < MINIMUM_RESOURCE_AGE_DAYS:
            raise ValueError("receipt minimum_age_days is unsafe")
        if inventory_count < 0:
            raise ValueError("receipt inventory_count may not be negative")
        if not isinstance(candidate_list, list) or not all(
            isinstance(candidate, str)
            and candidate.startswith("anime_covers/")
            and len(candidate) > len("anime_covers/")
            for candidate in candidate_list
        ):
            raise ValueError("receipt delete_candidates must contain strings")
        if candidate_list != sorted(set(candidate_list)):
            raise ValueError("receipt delete_candidates must be unique and sorted")
        if len(candidate_list) > inventory_count:
            raise ValueError("receipt candidate count exceeds its inventory count")
        return ExecutionReceipt(
            prepared_at=prepared_at,
            base_sha=base_sha,
            cloud_name=cloud_name,
            manifest_sha256=manifest_sha256,
            minimum_age_days=minimum_age_days,
            inventory_count=inventory_count,
            delete_candidates=tuple(candidate_list),
            cache_sha256=cache_sha256,
            receipt_sha256=supplied_digest,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RetentionError(f"Invalid execution receipt {path}: {exc}") from exc


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise RetentionError(f"Unable to hash required file {path}: {exc}") from exc


def validate_execution_receipt_bindings(
    receipt: ExecutionReceipt,
    manifest: ReviewedManifest,
    *,
    cloud_name: str,
    cache_path: Path,
) -> None:
    """Bind a valid receipt to the current cloud, manifest, and merged cache."""
    if receipt.cloud_name != manifest.cloud_name:
        raise RetentionError(
            "Execution receipt cloud_name does not match the reviewed retention "
            "manifest"
        )
    if receipt.cloud_name != cloud_name:
        raise RetentionError(
            "Execution receipt cloud_name does not match the configured Cloudinary "
            "product environment"
        )
    if receipt.manifest_sha256 != manifest.sha256:
        raise RetentionError(
            "Execution receipt does not match the reviewed retention manifest"
        )
    if receipt.minimum_age_days != manifest.minimum_age_days:
        raise RetentionError(
            "Execution receipt minimum age does not match the retention manifest"
        )
    current_cache_sha256 = file_sha256(cache_path)
    if not hmac.compare_digest(receipt.cache_sha256, current_cache_sha256):
        raise RetentionError(
            "Merged Cloudinary cache does not match the prepared execution receipt"
        )


def validate_manifest_cloud_binding(
    manifest: ReviewedManifest,
    configured_cloud_name: str,
) -> None:
    """Require the aged manifest to target the configured product environment."""
    if manifest.cloud_name != configured_cloud_name:
        raise RetentionError(
            "Retention manifest cloud_name does not match the configured Cloudinary "
            "product environment"
        )


def _required_expected_main_sha() -> str:
    expected_sha = os.getenv("RETENTION_EXPECTED_MAIN_SHA", "").strip()
    if not SHA1_PATTERN.fullmatch(expected_sha):
        raise RetentionError(
            "RETENTION_EXPECTED_MAIN_SHA must be the exact 40-character main SHA"
        )
    return expected_sha


def require_protected_execution_context() -> str:
    expected = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_REF": "refs/heads/main",
        "RETENTION_EXECUTION_CONTEXT": PROTECTED_EXECUTION_CONTEXT,
    }
    mismatches = [
        name for name, value in expected.items() if os.getenv(name, "") != value
    ]
    if mismatches:
        raise RetentionError(
            "Destructive retention is restricted to the protected main-branch "
            "GitHub Actions environment"
        )
    expected_sha = _required_expected_main_sha()
    try:
        current_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        data_status = subprocess.run(
            ["git", "status", "--porcelain", "--", "dist/data"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RetentionError(
            f"Unable to verify the protected Git state: {exc}"
        ) from exc
    if current_sha != expected_sha or data_status:
        raise RetentionError(
            "Retention execution requires an unchanged dist/data tree at "
            "RETENTION_EXPECTED_MAIN_SHA"
        )
    return expected_sha


def verify_prepared_base_transition(base_sha: str, expected_sha: str) -> None:
    """Allow only the prepared cache file to differ from the receipt base SHA."""
    if base_sha == expected_sha:
        return
    try:
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", base_sha, expected_sha],
            check=True,
            capture_output=True,
            text=True,
        )
        changed_output = subprocess.run(
            ["git", "diff", "--name-only", "-z", base_sha, expected_sha],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RetentionError(
            "Prepared receipt base SHA is not an ancestor of the approved main SHA"
        ) from exc
    changed_paths = [path for path in changed_output.split("\0") if path]
    if changed_paths != ["cloudinary_cache.json"]:
        raise RetentionError(
            "Only cloudinary_cache.json may change between the prepared receipt "
            f"base and approved main SHA; changed={changed_paths[:5]}"
        )


def validate_trusted_manifest_time(manifest_created_at: datetime) -> None:
    raw = os.getenv("RETENTION_TRUSTED_CREATED_AT", "").strip()
    try:
        trusted_created_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RetentionError(
            "RETENTION_TRUSTED_CREATED_AT must be a timezone-aware GitHub run time"
        ) from exc
    if trusted_created_at.tzinfo is None:
        raise RetentionError("RETENTION_TRUSTED_CREATED_AT must include a timezone")
    trusted_created_at = trusted_created_at.astimezone(UTC)
    manifest_created_at = manifest_created_at.astimezone(UTC)
    now = datetime.now(UTC)
    if now - trusted_created_at < timedelta(days=MINIMUM_MANIFEST_GRACE_DAYS):
        raise RetentionError(
            "The trusted GitHub dry-run is younger than the required 30 days"
        )
    manifest_delay = manifest_created_at - trusted_created_at
    if not timedelta(0) <= manifest_delay <= timedelta(minutes=30):
        raise RetentionError(
            "Manifest created_at is not consistent with its trusted GitHub run"
        )


def verify_remote_main_unchanged() -> None:
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    expected_sha = _required_expected_main_sha()
    if not repository or not os.getenv("GH_TOKEN", "").strip():
        raise RetentionError(
            "Remote main verification requires GitHub repository, expected SHA, "
            "and token"
        )
    try:
        remote_sha = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repository}/commits/main",
                "--jq",
                ".sha",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RetentionError(f"Unable to verify current remote main: {exc}") from exc
    if remote_sha != expected_sha:
        raise RetentionError(
            "Remote main changed after approval; re-run retention from fresh main"
        )


def _validate_cli_safety_limits(args: argparse.Namespace) -> None:
    if args.minimum_age_days < MINIMUM_RESOURCE_AGE_DAYS:
        raise RetentionError(
            f"--minimum-age-days must be at least {MINIMUM_RESOURCE_AGE_DAYS}"
        )
    if args.grace_days < MINIMUM_MANIFEST_GRACE_DAYS:
        raise RetentionError(
            f"--grace-days must be at least {MINIMUM_MANIFEST_GRACE_DAYS}"
        )
    if not 1 <= args.max_delete <= MAXIMUM_DELETE_COUNT:
        raise RetentionError(
            f"--max-delete must be between 1 and {MAXIMUM_DELETE_COUNT}"
        )
    if not 0 < args.max_fraction <= MAXIMUM_DELETE_FRACTION:
        raise RetentionError(
            "--max-fraction must be greater than 0 and at most "
            f"{MAXIMUM_DELETE_FRACTION}"
        )


def _validate_mode_paths(args: argparse.Namespace) -> None:
    if args.execute:
        raise RetentionError(
            "--execute is disabled; use --prepare-execution, merge its cache "
            "invalidation, then use --execute-prepared"
        )
    if args.prepare_execution:
        if not args.manifest_input or not args.execution_output:
            raise RetentionError(
                "--prepare-execution requires --manifest-input and --execution-output"
            )
        if args.execution_input:
            raise RetentionError(
                "--prepare-execution may not be combined with --execution-input"
            )
        if args.manifest_output:
            raise RetentionError(
                "--manifest-output is only valid for the default dry-run mode"
            )
    elif args.execute_prepared:
        if not args.manifest_input or not args.execution_input:
            raise RetentionError(
                "--execute-prepared requires --manifest-input and --execution-input"
            )
        if args.execution_output:
            raise RetentionError(
                "--execute-prepared may not be combined with --execution-output"
            )
        if args.manifest_output:
            raise RetentionError(
                "--manifest-output is only valid for the default dry-run mode"
            )
    elif args.manifest_input or args.execution_input or args.execution_output:
        raise RetentionError(
            "Input or execution receipt paths require an explicit retention mode"
        )


def main() -> int:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = parse_args()
    _validate_mode_paths(args)
    _validate_cli_safety_limits(args)

    protected_sha: str | None = None
    if args.prepare_execution or args.execute_prepared:
        protected_sha = require_protected_execution_context()

    paths = ProjectPaths.from_environment()
    cache = CacheRepository(paths.cache_file)
    credentials = required_cloudinary_credentials()
    service = CloudinaryRetentionService(paths.data_dir, cache)

    if not args.prepare_execution and not args.execute_prepared:
        plan = service.plan(minimum_age_days=args.minimum_age_days)
        payload = manifest_payload(
            plan,
            cloud_name=credentials["CLOUDINARY_CLOUD_NAME"],
        )
        logger.info(
            "DRY RUN: inventory=%s referenced=%s candidates=%s",
            payload["inventory_count"],
            payload["referenced_count"],
            len(payload["delete_candidates"]),
        )
        if args.manifest_output:
            atomic_write_json(args.manifest_output.resolve(), payload)
            logger.info("Retention manifest written to %s", args.manifest_output)
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    assert args.manifest_input is not None
    reviewed_manifest = read_manifest(args.manifest_input.resolve())
    validate_manifest_cloud_binding(
        reviewed_manifest,
        credentials["CLOUDINARY_CLOUD_NAME"],
    )
    validate_trusted_manifest_time(reviewed_manifest.created_at)
    current_plan = service.plan(minimum_age_days=reviewed_manifest.minimum_age_days)

    if args.prepare_execution:
        assert args.execution_output is not None
        assert protected_sha is not None
        prepared = service.prepare_deletion(
            current_plan,
            confirmation=args.confirm,
            manifest_candidates=set(reviewed_manifest.candidates),
            manifest_created_at=reviewed_manifest.created_at,
            grace_days=args.grace_days,
            max_delete=args.max_delete,
            max_fraction=args.max_fraction,
            pre_prepare_check=verify_remote_main_unchanged,
        )
        removed_cache_entries = service.invalidate_prepared_cache(prepared)
        if not paths.cache_file.exists():
            atomic_write_json(paths.cache_file, cache.snapshot())
        receipt = execution_receipt_payload(
            prepared,
            base_sha=protected_sha,
            cloud_name=reviewed_manifest.cloud_name,
            manifest_sha256=reviewed_manifest.sha256,
            cache_sha256=file_sha256(paths.cache_file),
        )
        atomic_write_json(args.execution_output.resolve(), receipt)
        logger.info(
            "Prepared %s deletion candidates; invalidated %s cache entries; "
            "no Cloudinary resources were deleted",
            len(prepared.delete_candidates),
            removed_cache_entries,
        )
        return 0

    assert args.execution_input is not None
    receipt = load_execution_receipt(args.execution_input.resolve())
    assert protected_sha is not None
    verify_prepared_base_transition(receipt.base_sha, protected_sha)
    validate_execution_receipt_bindings(
        receipt,
        reviewed_manifest,
        cloud_name=credentials["CLOUDINARY_CLOUD_NAME"],
        cache_path=paths.cache_file,
    )
    prepared = PreparedDeletion(
        minimum_age_days=receipt.minimum_age_days,
        inventory_count=receipt.inventory_count,
        delete_candidates=receipt.delete_candidates,
    )
    removed = service.execute_prepared(
        current_plan,
        prepared,
        confirmation=args.confirm,
        manifest_candidates=set(reviewed_manifest.candidates),
        manifest_created_at=reviewed_manifest.created_at,
        grace_days=args.grace_days,
        max_delete=args.max_delete,
        max_fraction=args.max_fraction,
        pre_delete_check=verify_remote_main_unchanged,
    )
    logger.info("Confirmed removal of %s unreferenced resources", len(removed))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DataContractError, RetentionError) as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc
