"""Manual, reference-aware Cloudinary retention CLI.

Examples:
  python cloudinary_cleaner.py --manifest-output retention-plan.json
  # Wait for the configured grace period, review the manifest, then:
  python cloudinary_cleaner.py --execute --manifest-input retention-plan.json \
      --confirm DELETE_UNREFERENCED

This tool never deletes quarterly JSON files.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import subprocess
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
    RetentionPlan,
)
from services.settings import ProjectPaths

logger = logging.getLogger(__name__)
PROTECTED_EXECUTION_CONTEXT = "protected-github-environment"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plan or execute deletion of Cloudinary images that no current "
            "quarterly JSON references. The default is dry-run."
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
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute a previously reviewed and aged manifest",
    )
    parser.add_argument(
        "--manifest-input",
        type=Path,
        help="Manifest created by an earlier dry-run",
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


def manifest_payload(plan: RetentionPlan) -> dict:
    payload = {
        "schema_version": 2,
        "created_at": plan.created_at.isoformat(),
        "minimum_age_days": plan.minimum_age_days,
        "inventory_count": len(plan.cloud_resources),
        "referenced_count": len(plan.referenced),
        "delete_candidates": list(plan.delete_candidates),
    }
    payload["manifest_sha256"] = manifest_digest(payload)
    return payload


def manifest_digest(payload: dict) -> str:
    unsigned = {
        key: value for key, value in payload.items() if key != "manifest_sha256"
    }
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_manifest(path: Path) -> tuple[datetime, int, set[str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 2:
            raise ValueError("unsupported manifest schema_version")
        supplied_digest = raw.get("manifest_sha256")
        if not isinstance(supplied_digest, str) or not hmac.compare_digest(
            supplied_digest,
            manifest_digest(raw),
        ):
            raise ValueError("manifest_sha256 does not match the manifest contents")
        created_at = datetime.fromisoformat(raw["created_at"])
        if created_at.tzinfo is None:
            raise ValueError("manifest created_at must include a timezone")
        minimum_age_days = int(raw["minimum_age_days"])
        if minimum_age_days < MINIMUM_RESOURCE_AGE_DAYS:
            raise ValueError(
                f"minimum_age_days must be at least {MINIMUM_RESOURCE_AGE_DAYS}"
            )
        inventory_count = int(raw["inventory_count"])
        referenced_count = int(raw["referenced_count"])
        candidate_list = raw["delete_candidates"]
        if not isinstance(candidate_list, list) or not all(
            isinstance(candidate, str) and candidate.startswith("anime_covers/")
            for candidate in candidate_list
        ):
            raise ValueError("delete_candidates must contain strings")
        candidates = set(candidate_list)
        if len(candidates) != len(candidate_list):
            raise ValueError("delete_candidates may not contain duplicates")
        if inventory_count < 0 or referenced_count < 0:
            raise ValueError("manifest counts may not be negative")
        if referenced_count > inventory_count or len(candidates) > inventory_count:
            raise ValueError("manifest counts are inconsistent")
        return created_at, minimum_age_days, candidates
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RetentionError(f"Invalid retention manifest {path}: {exc}") from exc


def require_protected_execution_context() -> None:
    expected = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_REF": "refs/heads/main",
        "RETENTION_EXECUTION_CONTEXT": PROTECTED_EXECUTION_CONTEXT,
    }
    mismatches = [
        name for name, value in expected.items() if os.getenv(name, "") != value
    ]
    expected_sha = os.getenv("GITHUB_SHA", "").strip()
    if mismatches or not expected_sha:
        raise RetentionError(
            "Destructive retention is restricted to the protected main-branch "
            "GitHub Actions environment"
        )
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
            "Retention execution requires an unchanged dist/data tree at GITHUB_SHA"
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
    expected_sha = os.getenv("GITHUB_SHA", "").strip()
    if not repository or not expected_sha or not os.getenv("GH_TOKEN", "").strip():
        raise RetentionError(
            "Remote main verification requires GitHub repository, SHA, and token"
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


def main() -> int:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = parse_args()
    if args.execute:
        require_protected_execution_context()
    paths = ProjectPaths.from_environment()
    cache = CacheRepository(paths.cache_file)
    service = CloudinaryRetentionService(paths.data_dir, cache)

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

    if not args.execute:
        plan = service.plan(minimum_age_days=args.minimum_age_days)
        payload = manifest_payload(plan)
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

    if not args.manifest_input:
        raise RetentionError("--execute requires --manifest-input")
    manifest_created_at, minimum_age_days, manifest_candidates = load_manifest(
        args.manifest_input.resolve()
    )
    validate_trusted_manifest_time(manifest_created_at)
    current_plan = service.plan(minimum_age_days=minimum_age_days)
    removed = service.execute(
        current_plan,
        confirmation=args.confirm,
        manifest_candidates=manifest_candidates,
        manifest_created_at=manifest_created_at,
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
