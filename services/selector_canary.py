"""Read-only live checks for the source HTML and parser contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from models import TAIPEI_TZ
from services.errors import ItemParseError, SelectorCanaryError
from services.http_client import SourceClient
from services.parser import extract_item_html, parse_anime_item
from services.settings import CrawlerSettings


@dataclass(frozen=True)
class SelectorCanaryResult:
    year: str
    season: str
    source_url: str
    card_count: int


class QuarterSource(Protocol):
    def fetch_quarter_html(self, year: str, season: str) -> tuple[str, str]: ...


def _season_for_month(month: int) -> str:
    if 1 <= month <= 3:
        return "冬"
    if 4 <= month <= 6:
        return "春"
    if 7 <= month <= 9:
        return "夏"
    return "秋"


def run_selector_canary(
    *,
    now: datetime | None = None,
    settings: CrawlerSettings | None = None,
    source_client: QuarterSource | None = None,
) -> SelectorCanaryResult:
    """Fetch and parse the current quarter without images or repository writes."""

    observed_at = (now or datetime.now(TAIPEI_TZ)).astimezone(TAIPEI_TZ)
    year = str(observed_at.year)
    season = _season_for_month(observed_at.month)
    runtime_settings = settings or CrawlerSettings.from_environment()
    client = source_client or SourceClient(runtime_settings)

    source_url, document_html = client.fetch_quarter_html(year, season)
    try:
        item_html_list = extract_item_html(document_html)
    except ItemParseError as exc:
        raise SelectorCanaryError(
            "Source card selector matched no anime cards"
        ) from exc
    bangumi_ids: set[str] = set()

    for index, item_html in enumerate(item_html_list):
        try:
            candidate = parse_anime_item(item_html)
        except ItemParseError as exc:
            raise SelectorCanaryError(
                f"Source selector contract failed at card {index}: {exc}"
            ) from exc
        if candidate.bangumi_id in bangumi_ids:
            raise SelectorCanaryError(
                f"Source selector contract returned a duplicate bangumi_id at card {index}"
            )
        bangumi_ids.add(candidate.bangumi_id)

    return SelectorCanaryResult(
        year=year,
        season=season,
        source_url=source_url,
        card_count=len(item_html_list),
    )
