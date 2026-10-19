"""Independent collection, image resolution and episode-refresh stages.

Known source failures retain the complete previous quarter. Image failures may
reuse only an exactly matching work's existing managed image. No incomplete
quarter or synthetic image URL is ever persisted.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime

from pydantic import ValidationError

from models import Anime
from services.anime_service import parse_date_time
from services.atomic_io import atomic_write_json
from services.cache_repository import CacheRepository
from services.display_name_reviews import reviewed_display_rename
from services.episode_metadata import refresh_repository_episodes
from services.errors import (
    ConfigurationError,
    DataContractError,
    ImageStoreError,
    ItemParseError,
    SourceFetchError,
    SourceNotFoundError,
)
from services.http_client import SourceClient
from services.image_store import CloudinaryImageStore
from services.parser import extract_item_html, parse_anime_item
from services.settings import CrawlerSettings
from services.source_identity import conflicting_providers

logger = logging.getLogger(__name__)


def same_work(old, candidate):
    return bool(
        old
        and old.bangumi_id == candidate.bangumi_id
        and (
            old.anime_name == candidate.anime_name
            or reviewed_display_rename(old, candidate)
        )
        and (
            not old.original_name
            or not candidate.original_name
            or old.original_name == candidate.original_name
        )
        and not conflicting_providers(old.external_ids, candidate.external_ids)
    )


def run_update_pipeline(
    paths,
    repository,
    now: datetime,
    quarters,
    *,
    future,
    source=None,
    image_factory=None,
    refresh=None,
):
    settings = CrawlerSettings.from_environment()
    source = source or SourceClient(settings)
    cache = CacheRepository(paths.cache_file)
    image_factory = image_factory or (lambda: CloudinaryImageStore(settings, cache))
    refresh = refresh or refresh_repository_episodes
    report = {
        "status": "running",
        "collection": [],
        "images": [],
        "episodes": "not_started",
    }
    report_path = paths.root / "audit-output" / "update-pipeline.json"
    collected = []
    failures = 0
    parse_failures = 0
    changed = set()
    # No Cloudinary request or credential construction in the collection stage.
    for year, season in quarters:
        quarter = f"{year}_{season}"
        try:
            url, html = source.fetch_quarter_html(year, season)
            cards = extract_item_html(html)
            candidates = [parse_anime_item(card) for card in cards]
            ids = [a.bangumi_id for a in candidates]
            previous = repository.load_quarter(year, season)
            if len(set(ids)) != len(ids):
                raise DataContractError("Duplicate source cards")
            if previous:
                old_ids = {a.bangumi_id for a in previous.anime_list}
                if not old_ids <= set(ids):
                    raise DataContractError(
                        "Existing card disappeared; complete quarter retained"
                    )
            collected.append((year, season, url, candidates, previous))
            report["collection"].append(
                {"quarter": quarter, "status": "collected", "cards": len(candidates)}
            )
        except SourceNotFoundError:
            if future(int(year), season, now):
                report["collection"].append(
                    {"quarter": quarter, "status": "not_published"}
                )
                continue
            failures += 1
            report["collection"].append(
                {
                    "quarter": quarter,
                    "status": "retained",
                    "error": "SourceNotFoundError",
                }
            )
        except (
            SourceFetchError,
            ItemParseError,
            DataContractError,
            ValidationError,
        ) as exc:
            failures += 1
            parse_failures += isinstance(exc, (ItemParseError, ValidationError))
            report["collection"].append(
                {"quarter": quarter, "status": "retained", "error": type(exc).__name__}
            )
    atomic_write_json(report_path, report)

    store = None
    unavailable = False
    primary_error = None
    try:
        for year, season, url, candidates, previous in collected:
            old_by_id = (
                {a.bangumi_id: a for a in previous.anime_list} if previous else {}
            )
            records = []
            try:
                for candidate in candidates:
                    old = old_by_id.get(candidate.bangumi_id)
                    key = (
                        "source_"
                        + hashlib.sha256(
                            candidate.source_image_url.encode()
                        ).hexdigest()
                    )
                    image_url = cache.get(key)
                    if not image_url:
                        try:
                            if unavailable:
                                raise ImageStoreError(
                                    "Image service unavailable for this run"
                                )
                            if store is None:
                                try:
                                    store = image_factory()
                                    store.assert_quota_available()
                                except (ConfigurationError, ImageStoreError):
                                    unavailable = True
                                    raise
                            image_url = store.store(
                                candidate.source_image_url, candidate.anime_name
                            )
                        except (ConfigurationError, ImageStoreError):
                            failures += 1
                            report["images"].append(
                                {
                                    "quarter": f"{year}_{season}",
                                    "card_id": candidate.bangumi_id,
                                    "status": "deferred",
                                }
                            )
                            if not same_work(old, candidate):
                                raise ImageStoreError(
                                    "No verified existing image for this exact work"
                                ) from None
                            image_url = old.anime_image_url
                    records.append(
                        Anime(
                            bangumi_id=candidate.bangumi_id,
                            anime_name=candidate.anime_name,
                            anime_image_url=image_url,
                            premiere_date=candidate.premiere_date,
                            premiere_time=candidate.premiere_time,
                            story=candidate.story,
                            original_name=candidate.original_name,
                            external_ids=candidate.external_ids,
                        )
                    )
                records.sort(key=parse_date_time)
                result = repository.write_quarter(
                    year=year,
                    season=season,
                    records=records,
                    source_url=url,
                    source_count=len(candidates),
                    parse_failure_count=0,
                )
                if result.changed:
                    changed.add(f"{year}_{season}.json")
            except (ImageStoreError, DataContractError) as exc:
                failures += 1
                report["collection"].append(
                    {
                        "quarter": f"{year}_{season}",
                        "status": "retained",
                        "error": type(exc).__name__,
                    }
                )
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        # Filesystem/cache corruption is fatal, unlike an unavailable provider.
        try:
            cache.save_if_changed()
            atomic_write_json(report_path, report)
        except Exception as exc:
            if primary_error is None:
                raise
            primary_error.add_note(
                f"Cache/report persistence also failed: {type(exc).__name__}"
            )

    # This stage always gets its own opportunity after known source/image failures.
    # It operates only on complete validated datasets, including retained quarters.
    report["episodes"] = "running"
    atomic_write_json(report_path, report)
    changed.update(refresh(repository, now))
    all_records = [
        a for p in repository.validate_all() for a in repository.load_path(p).anime_list
    ]
    failed_metadata = {
        (a.bangumi_id, a.original_name, a.external_ids.bangumi)
        for a in all_records
        if a.episodes
        and a.episodes.attempted_at == now
        and a.episodes.fetch_state in {"stale", "conflict"}
    }
    failures += len(failed_metadata)
    report.update(
        status="partial" if failures else "completed",
        episodes="completed",
        metadata_failures=len(failed_metadata),
        failure_count=failures,
        changed_quarters=sorted(changed),
    )
    atomic_write_json(report_path, report)
    logger.info(
        "Independent update stages: %s failures, %s changed quarters",
        failures,
        len(changed),
    )
    return {
        "processed_quarters": len(collected),
        "changed_quarters": len(changed),
        "total_records": len(all_records),
        "parse_failures": parse_failures,
        "partial_failures": failures,
    }
