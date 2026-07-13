"""Crawler orchestration.

Network fetch, pure parsing, image storage, cache persistence, and data writing
live in separate modules. This module coordinates them and never converts a
system failure into a public data record.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from dotenv import load_dotenv

from config import Config
from models import Anime
from services.cache_repository import CacheRepository
from services.errors import CrawlerError
from services.http_client import SourceClient
from services.image_store import CloudinaryImageStore
from services.parser import extract_item_html, parse_anime_item
from services.settings import CrawlerSettings, ProjectPaths

load_dotenv()
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ItemFailure:
    index: int
    error_type: str
    message: str


@dataclass(frozen=True)
class CrawlResult:
    year: str
    season: str
    source_url: str
    source_count: int
    anime_list: list[Anime]
    failures: tuple[ItemFailure, ...]

    @property
    def parse_failure_count(self) -> int:
        return len(self.failures)


def parse_date_time(anime: Anime) -> tuple[int, float, str, str]:
    if anime.premiere_date == "無首播日期":
        return 8, float("inf"), anime.bangumi_id.casefold(), anime.anime_name.casefold()
    weekday = Config.WEEKDAY_MAP.get(anime.premiere_date, 7)
    if anime.premiere_time == "無首播時間":
        return weekday, 0.0, anime.bangumi_id.casefold(), anime.anime_name.casefold()
    match = re.match(r"(\d{1,2}):(\d{2})", anime.premiere_time)
    if not match:
        return (
            weekday,
            float("inf"),
            anime.bangumi_id.casefold(),
            anime.anime_name.casefold(),
        )
    hour, minute = int(match.group(1)), int(match.group(2))
    return (
        weekday,
        hour + minute / 60.0,
        anime.bangumi_id.casefold(),
        anime.anime_name.casefold(),
    )


class AnimeCrawlerService:
    def __init__(
        self,
        *,
        settings: CrawlerSettings,
        source_client: SourceClient,
        image_store: CloudinaryImageStore,
        cache: CacheRepository,
    ) -> None:
        self.settings = settings
        self.source_client = source_client
        self.image_store = image_store
        self.cache = cache

    @classmethod
    def from_environment(cls) -> AnimeCrawlerService:
        settings = CrawlerSettings.from_environment()
        paths = ProjectPaths.from_environment()
        cache = CacheRepository(paths.cache_file)
        return cls(
            settings=settings,
            source_client=SourceClient(settings),
            image_store=CloudinaryImageStore(settings, cache),
            cache=cache,
        )

    def _process_item(self, item_html: str) -> Anime:
        candidate = parse_anime_item(item_html)
        image_url = self.image_store.store(
            candidate.source_image_url,
            candidate.anime_name,
        )
        return Anime(
            bangumi_id=candidate.bangumi_id,
            anime_name=candidate.anime_name,
            anime_image_url=image_url,
            premiere_date=candidate.premiere_date,
            premiere_time=candidate.premiere_time,
            story=candidate.story,
        )

    def fetch_quarter(self, year: str, season: str) -> CrawlResult:
        self.image_store.assert_quota_available()
        source_url, document_html = self.source_client.fetch_quarter_html(year, season)
        item_html_list = extract_item_html(document_html)
        records: list[Anime] = []
        failures: list[ItemFailure] = []

        try:
            with ThreadPoolExecutor(
                max_workers=self.settings.max_workers,
                thread_name_prefix="anime-worker",
            ) as executor:
                futures = {
                    executor.submit(self._process_item, item_html): index
                    for index, item_html in enumerate(item_html_list)
                }
                for future in as_completed(futures):
                    index = futures[future]
                    try:
                        records.append(future.result())
                    except Exception as exc:
                        failures.append(
                            ItemFailure(
                                index=index,
                                error_type=type(exc).__name__,
                                message=str(exc)[:500],
                            )
                        )
                        logger.exception(
                            "Anime item %s failed for %s %s",
                            index,
                            year,
                            season,
                        )
        finally:
            self.cache.save_if_changed()

        if not records:
            summary = failures[0].message if failures else "no cards were parsed"
            raise CrawlerError(f"{year} {season} produced no valid records: {summary}")

        records.sort(key=parse_date_time)
        return CrawlResult(
            year=str(year),
            season=season,
            source_url=source_url,
            source_count=len(item_html_list),
            anime_list=records,
            failures=tuple(sorted(failures, key=lambda failure: failure.index)),
        )


def fetch_anime_data(
    year: str,
    season: str,
    cache: object | None = None,
) -> list[dict]:
    """Backward-compatible public function.

    The legacy cache argument is ignored. Failures now raise CrawlerError
    instead of returning an error dictionary inside anime_list.
    """

    del cache
    result = AnimeCrawlerService.from_environment().fetch_quarter(year, season)
    return [anime.model_dump(mode="json") for anime in result.anime_list]


def get_current_season(month: int) -> str:
    if 1 <= month <= 3:
        return "冬"
    if 4 <= month <= 6:
        return "春"
    if 7 <= month <= 9:
        return "夏"
    return "秋"
