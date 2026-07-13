"""Safely backfill legacy 未知ID values from current source pages.

Dry-run is the default. Every quarter is all-or-nothing: if any legacy record
cannot be matched exactly by name, no file is written.
"""

from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass

from models import Anime, DataQuality
from services.data_repository import (
    QUARTER_FILE_PATTERN,
    DataQualityPolicy,
    DataRepository,
)
from services.errors import DataContractError
from services.http_client import SourceClient
from services.parser import extract_item_html, parse_anime_item
from services.settings import CrawlerSettings, ProjectPaths

CONFIRMATION_PHRASE = "BACKFILL_IDS"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackfillPlan:
    year: str
    season: str
    source_url: str
    source_count: int
    records: list[Anime]
    changed_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dry-run or execute all-or-nothing legacy ID backfill"
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=1.0,
        help="Polite delay between source quarter requests (default: 1.0)",
    )
    return parser.parse_args()


def create_plans(
    repository: DataRepository,
    source_client: SourceClient,
    *,
    delay_seconds: float,
) -> list[BackfillPlan]:
    plans: list[BackfillPlan] = []
    errors: list[str] = []

    for path in repository.validate_all(allow_legacy=True):
        match = QUARTER_FILE_PATTERN.fullmatch(path.name)
        if not match:
            continue
        year, season = match.groups()
        dataset = repository.load_path(path)
        if all(record.bangumi_id != "未知ID" for record in dataset.anime_list):
            continue

        source_url, html = source_client.fetch_quarter_html(year, season)
        candidates = [
            parse_anime_item(item_html) for item_html in extract_item_html(html)
        ]
        by_name: dict[str, str] = {}
        duplicate_names: set[str] = set()
        for candidate in candidates:
            key = candidate.anime_name.casefold()
            if key in by_name:
                duplicate_names.add(key)
            by_name[key] = candidate.bangumi_id
        if duplicate_names:
            errors.append(f"{year}_{season}: duplicate source names")
            continue

        changed = 0
        updated: list[Anime] = []
        unmatched: list[str] = []
        for record in dataset.anime_list:
            if record.bangumi_id != "未知ID":
                updated.append(record)
                continue
            bangumi_id = by_name.get(record.anime_name.casefold())
            if not bangumi_id:
                unmatched.append(record.anime_name)
                continue
            updated.append(
                Anime.model_validate(
                    {
                        **record.model_dump(mode="json"),
                        "bangumi_id": bangumi_id,
                    }
                )
            )
            changed += 1

        if unmatched:
            errors.append(
                f"{year}_{season}: {len(unmatched)} unmatched names "
                f"({', '.join(unmatched[:3])})"
            )
            continue
        repository.policy.validate(
            updated,
            DataQuality.from_records(
                updated,
                source_count=len(updated),
                parse_failure_count=0,
            ),
            dataset,
        )
        plans.append(
            BackfillPlan(
                year=year,
                season=season,
                source_url=source_url,
                source_count=len(updated),
                records=updated,
                changed_count=changed,
            )
        )
        if delay_seconds:
            time.sleep(delay_seconds)

    if errors:
        raise DataContractError(
            "Backfill dry-run found unresolved quarters; no files were written:\n"
            + "\n".join(errors)
        )
    return plans


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = parse_args()
    if args.delay_seconds < 0:
        raise DataContractError("--delay-seconds may not be negative")
    settings = CrawlerSettings.from_environment()
    paths = ProjectPaths.from_environment()
    repository = DataRepository(
        paths.data_dir,
        DataQualityPolicy(
            minimum_count_ratio=settings.minimum_count_ratio,
            maximum_parse_failure_ratio=0,
            maximum_fallback_id_ratio=0,
        ),
    )
    plans = create_plans(
        repository,
        SourceClient(settings),
        delay_seconds=args.delay_seconds,
    )
    changed_records = sum(plan.changed_count for plan in plans)
    logger.info(
        "Backfill plan is valid: %s quarters, %s legacy IDs",
        len(plans),
        changed_records,
    )

    if not args.execute:
        logger.info(
            "DRY RUN ONLY. Re-run with --execute --confirm %s after review.",
            CONFIRMATION_PHRASE,
        )
        return 0
    if args.confirm != CONFIRMATION_PHRASE:
        raise DataContractError(f"Execution requires --confirm {CONFIRMATION_PHRASE}")

    for plan in plans:
        repository.write_quarter(
            year=plan.year,
            season=plan.season,
            records=plan.records,
            source_url=plan.source_url,
            source_count=plan.source_count,
            parse_failure_count=0,
        )
    logger.info("Backfilled %s legacy IDs atomically by quarter", changed_records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
