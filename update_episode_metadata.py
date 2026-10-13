"""Backfill source database links and episode metadata without image uploads.

Default is a dry run in an isolated copy. Use --write to update quarterly JSON;
the normal automation publisher still handles production via a data PR.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from models import TAIPEI_TZ, ExternalIds
from services.data_repository import DataQualityPolicy, DataRepository
from services.diagnostics import operation, run_command
from services.episode_metadata import (
    BangumiClient,
    EpisodeEnricher,
    refresh_repository_episodes,
)
from services.errors import ItemParseError, SourceFetchError
from services.http_client import SourceClient
from services.parser import extract_item_html, parse_anime_item
from services.settings import CrawlerSettings, ProjectPaths

logger = logging.getLogger(__name__)


def backfill_source_links(
    repository: DataRepository,
    source: SourceClient,
    *,
    year: str | None = None,
    season: str | None = None,
    anime_id: str | None = None,
) -> int:
    updated = 0
    for path in repository.validate_all():
        file_year, file_season = path.stem.split("_")
        if year and file_year != year or season and file_season != season:
            continue
        dataset = repository.load_path(path)
        try:
            _, html = source.fetch_quarter_html(file_year, file_season)
            candidates = [parse_anime_item(card) for card in extract_item_html(html)]
        except (SourceFetchError, ItemParseError) as exc:
            logger.warning("Source link backfill skipped %s: %s", path.name, exc)
            continue
        by_id = {candidate.bangumi_id: candidate for candidate in candidates}
        records = []
        for anime in dataset.anime_list:
            candidate = by_id.get(anime.bangumi_id)
            if candidate is not None and (not anime_id or anime.bangumi_id == anime_id):
                # A temporarily missing source link is not a new identity.
                ids = ExternalIds(
                    bangumi=candidate.external_ids.bangumi
                    or anime.external_ids.bangumi,
                    anilist=candidate.external_ids.anilist
                    or anime.external_ids.anilist,
                )
                changed_identity = (
                    ids.bangumi is not None
                    and ids.bangumi != anime.external_ids.bangumi
                )
                anime = anime.model_copy(
                    update={
                        "external_ids": ids,
                        "original_name": candidate.original_name or anime.original_name,
                        "episodes": None if changed_identity else anime.episodes,
                    }
                )
                updated += 1
            records.append(anime)
        repository.write_quarter(
            year=file_year,
            season=file_season,
            records=records,
            source_url=dataset.source_url,
            source_count=dataset.quality.source_count,
            parse_failure_count=dataset.quality.parse_failure_count,
        )
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--year", help="Existing data year (YYYY)")
    group.add_argument(
        "--all", action="store_true", help="Backfill links for all stored quarters"
    )
    parser.add_argument("--season", choices=("冬", "春", "夏", "秋"))
    parser.add_argument("--anime-id", help="Only backfill this acgsecrets card ID")
    parser.add_argument(
        "--write", action="store_true", help="Write updates; otherwise dry run"
    )
    parser.add_argument(
        "--max-requests", type=int, default=240, help="Bangumi request budget (1–1000)"
    )
    args = parser.parse_args()
    if args.year and (len(args.year) != 4 or not args.year.isdigit()):
        parser.error("--year must use YYYY")
    if args.season and not args.year:
        parser.error("--season requires --year")
    if not 1 <= args.max_requests <= 1000:
        parser.error("--max-requests must be between 1 and 1000")
    paths = ProjectPaths.from_environment()
    with tempfile.TemporaryDirectory(prefix="anime-episode-backfill-") as temporary:
        data_dir = paths.data_dir
        if not args.write:
            data_dir = Path(temporary) / "data"
            shutil.copytree(paths.data_dir, data_dir)
        repository = DataRepository(data_dir, DataQualityPolicy())
        settings = CrawlerSettings.from_environment()
        with operation("episode-backfill"):
            count = backfill_source_links(
                repository,
                SourceClient(settings),
                year=args.year,
                season=args.season,
                anime_id=args.anime_id,
            )
            # Scope this manual command; normal crawling refreshes all linked
            # entries. Unselected records must not consume the request budget.
            selected_ids = {
                anime.bangumi_id
                for path in repository.validate_all()
                if (not args.year or path.stem.startswith(args.year + "_"))
                and (not args.season or path.stem.endswith("_" + args.season))
                for anime in repository.load_path(path).anime_list
                if not args.anime_id or anime.bangumi_id == args.anime_id
            }
            enricher = EpisodeEnricher(BangumiClient(request_budget=args.max_requests))
            changed = refresh_repository_episodes(
                repository,
                datetime.now(TAIPEI_TZ),
                enricher,
                selected_ids=selected_ids,
            )
            repository.validate_all()
        print(
            f"{'WRITE' if args.write else 'DRY RUN'}: examined {count} source matches; refreshed {len(changed)} quarter files"
        )
        for path in repository.validate_all():
            for anime in repository.load_path(path).anime_list:
                if anime.bangumi_id in selected_ids and anime.episodes:
                    info = anime.episodes
                    print(
                        f"{path.stem} {anime.anime_name}: {info.aired_episodes}/{info.total_episodes}, {info.airing_status}, {info.fetch_state}"
                    )


if __name__ == "__main__":
    raise SystemExit(run_command(main, "episode-backfill"))
