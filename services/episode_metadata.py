"""Bounded, optional episode enrichment using explicit Bangumi source links.

Only main episodes are counted. A schedule entry is not proof of broadcast:
same-day entries remain uncounted until their source date has passed in Japan.
Finished additionally requires an explicit past end date and a complete list.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests

from models import Anime, EpisodeMetadata

logger = logging.getLogger(__name__)
TOKYO_TZ = ZoneInfo("Asia/Tokyo")
API_ORIGIN = "https://api.bgm.tv/v0"
PAGE_ORIGIN = "https://bangumi.tv"


class MetadataUnavailable(Exception):
    """A supplementary provider failed; preserve the last valid snapshot."""


class RequestBudgetExceeded(Exception):
    """Leave remaining records unchanged and resume on the next run."""


def _date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        match = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", value)
        if match:
            return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None
    return None


def _name(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", value).casefold()
        if character.isalnum()
    )


def _infobox(subject: dict[str, Any], keys: set[str]) -> list[str]:
    return [
        item["value"]
        for item in subject.get("infobox", [])
        if isinstance(item, dict)
        and item.get("key") in keys
        and isinstance(item.get("value"), str)
    ]


def parse_episode_metadata(
    anime: Anime,
    subject: dict[str, Any],
    entries: list[dict[str, Any]],
    now: datetime,
) -> EpisodeMetadata:
    subject_id = anime.external_ids.bangumi
    if subject.get("id") != subject_id or subject.get("type") != 2:
        raise MetadataUnavailable("Linked subject is not the expected anime")
    if not isinstance(subject.get("infobox", []), list):
        raise MetadataUnavailable("Anime infobox is invalid")
    names = [subject.get("name", ""), subject.get("name_cn", "")]
    for item in subject.get("infobox", []):
        if isinstance(item, dict) and item.get("key") == "别名":
            value = item.get("value")
            if isinstance(value, str):
                names.append(value)
            elif isinstance(value, list):
                names.extend(
                    alias.get("v", "") for alias in value if isinstance(alias, dict)
                )
    if anime.original_name and _name(anime.original_name) not in {
        _name(name) for name in names if isinstance(name, str)
    }:
        raise MetadataUnavailable(
            "Linked anime title differs from the source original title"
        )

    today = now.astimezone(TOKYO_TZ).date()
    raw_total = subject.get("eps")
    total = raw_total if type(raw_total) is int and raw_total > 0 else None
    main: dict[int, dict[str, Any]] = {}
    numbers: set[float] = set()
    invalid = False
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("subject_id") != subject_id:
            raise MetadataUnavailable("Episode belongs to a different subject")
        if entry.get("type") != 0:
            continue
        number = entry.get("sort")
        # Recaps numbered 0 / 0.5 / 12.5 are excluded even if tagged as main.
        if type(number) not in (int, float) or number <= 0 or number % 1:
            continue
        identity = entry.get("id")
        if type(identity) is not int or identity <= 0:
            raise MetadataUnavailable("Episode identity is invalid")
        if identity in main:
            if main[identity] != entry:
                raise MetadataUnavailable("Duplicate episode ID has conflicting data")
            continue
        if number in numbers:
            invalid = True
        numbers.add(number)
        main[identity] = entry

    dated = [_date(entry.get("airdate")) for entry in main.values()]
    aired = sum(value is not None and value < today for value in dated)
    # Missing dates could hide additional already-aired episodes. Do not
    # present an incomplete lower bound as an exact progress count.
    progress = aired if main and all(value is not None for value in dated) else None
    has_gap = bool(numbers) and max(numbers) - min(numbers) + 1 != len(numbers)
    if has_gap:
        progress = None
    start = _date(subject.get("date"))
    if start and start > today and not aired:
        progress = 0
    status = "not_yet_aired" if start and start > today else "unknown"
    known_pending = any(value is not None and value >= today for value in dated)
    recent_partial = (
        total is not None
        and aired < total
        and any(
            value is not None and today - timedelta(days=14) <= value < today
            for value in dated
        )
    )
    if aired and (known_pending or recent_partial):
        status = "airing"
    ends = {
        parsed
        for value in _infobox(subject, {"播放结束", "放送終了", "播放結束"})
        if (parsed := _date(value)) is not None
    }
    count_values = {
        int(value)
        for value in _infobox(subject, {"话数", "話數"})
        if re.fullmatch(r"[1-9][0-9]*", value.strip())
    }
    if any(value < today for value in ends):
        # A past end date with incomplete evidence is not a current airing
        # signal. It remains unknown unless the complete checks below pass.
        status = "unknown"
    conflict = (
        invalid
        or has_gap
        or len(ends) > 1
        or any(value != total for value in count_values)
    )
    conflict = conflict or (total is not None and len(main) > total)
    complete = (
        total is not None
        and len(main) == total
        and progress == total
        and len(ends) == 1
        and next(iter(ends)) < today
        and (start is None or next(iter(ends)) >= start)
        and all(value <= next(iter(ends)) for value in dated if value is not None)
    )
    if conflict:
        return EpisodeMetadata(
            aired_episodes=progress,
            airing_status="unknown",
            fetch_state="conflict",
            source_urls=[
                f"https://bgm.tv/subject/{subject_id}",
                f"https://bgm.tv/subject/{subject_id}/ep",
            ],
            checked_at=now,
            attempted_at=now,
            note="來源話數、正篇集目或結束日期不一致，總集數待確認。",
        )
    if complete:
        status = "finished"
    return EpisodeMetadata(
        total_episodes=total,
        aired_episodes=progress,
        airing_status=status,
        episode_count_status="confirmed"
        if complete
        else "planned"
        if total
        else "unknown",
        fetch_state="ok",
        source_urls=[
            f"https://bgm.tv/subject/{subject_id}",
            f"https://bgm.tv/subject/{subject_id}/ep",
        ],
        checked_at=now,
        attempted_at=now,
        end_date=next(iter(ends)) if len(ends) == 1 else None,
        note="依日本首播日期核對正篇；當日集目暫不計入，不代表平台上架進度。",
    )


class BangumiClient:
    """Fixed host, finite pages / timeouts / per-run requests, circuit breaker."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        request_budget: int = 240,
        interval_seconds: float = 0.5,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "anime-static-crawler/2.0 (+https://github.com/0961282056/anime-static-crawler)",
                "Accept": "application/json",
            }
        )
        self.remaining = request_budget
        self.interval_seconds = interval_seconds
        self.next_request = 0.0
        self.unavailable = False
        self.pages_unavailable = False
        self.deadline = time.monotonic() + 300

    def _get(self, path: str, **params: object) -> dict[str, Any]:
        return self._request(API_ORIGIN + path, params=params)

    def _get_page(self, path: str) -> str:
        return self._request(PAGE_ORIGIN + path, html=True)

    def _request(
        self, url: str, *, html: bool = False, params: dict[str, object] | None = None
    ) -> Any:
        circuit = "pages_unavailable" if html else "unavailable"
        if getattr(self, circuit):
            raise MetadataUnavailable("Provider unavailable for this run")
        if self.remaining <= 0 or time.monotonic() >= self.deadline:
            raise RequestBudgetExceeded
        delay = max(0.0, self.next_request - time.monotonic())
        if time.monotonic() + delay >= self.deadline:
            raise RequestBudgetExceeded
        if delay:
            time.sleep(delay)
        available_seconds = self.deadline - time.monotonic()
        if available_seconds <= 0:
            raise RequestBudgetExceeded
        self.remaining -= 1
        response = None
        try:
            response = self.session.get(
                url,
                params=params,
                timeout=(
                    min(5, available_seconds / 2),
                    min(15, available_seconds / 2),
                ),
                allow_redirects=False,
                headers={"Accept": "text/html"} if html else {},
            )
            if response.status_code == 429 or response.status_code >= 500:
                setattr(self, circuit, True)
            if response.status_code != 200:
                raise MetadataUnavailable(
                    f"Bangumi returned HTTP {response.status_code}"
                )
            if len(response.content) > 2 * 1024 * 1024:
                raise MetadataUnavailable("Bangumi response exceeds size limit")
            if html:
                response.encoding = "utf-8"
                return response.text
            data = response.json()
            if not isinstance(data, dict):
                raise MetadataUnavailable("Bangumi response is not an object")
            return data
        except requests.RequestException as exc:
            setattr(self, circuit, True)
            raise MetadataUnavailable("Bangumi network request failed") from exc
        except ValueError as exc:
            raise MetadataUnavailable("Bangumi response is not valid JSON") from exc
        finally:
            self.next_request = time.monotonic() + self.interval_seconds
            if response is not None:
                response.close()

    def fetch(self, anime: Anime, now: datetime) -> EpisodeMetadata:
        try:
            return self._fetch_api(anime, now)
        except MetadataUnavailable:
            return self._fetch_pages(anime, now)

    def _fetch_pages(self, anime: Anime, now: datetime) -> EpisodeMetadata:
        # Import only when crawling: BUILD_ONLY uses the minimal dependency set.
        from services.bangumi_pages import parse_episode_page, parse_subject_page

        subject_id = anime.external_ids.bangumi
        if subject_id is None:
            raise MetadataUnavailable("Anime has no linked Bangumi subject")
        try:
            subject = parse_subject_page(
                self._get_page(f"/subject/{subject_id}"), subject_id
            )
            parse_episode_metadata(anime, subject, [], now)
            entries = parse_episode_page(
                self._get_page(f"/subject/{subject_id}/ep"), subject_id
            )
        except ValueError as exc:
            raise MetadataUnavailable("Bangumi page structure is invalid") from exc
        info = parse_episode_metadata(anime, subject, entries, now)
        return info.model_copy(
            update={"note": "Bangumi 頁面備援（bangumi.tv）；" + info.note}
        )

    def _fetch_api(self, anime: Anime, now: datetime) -> EpisodeMetadata:
        subject_id = anime.external_ids.bangumi
        subject = self._get(f"/subjects/{subject_id}")
        # Reject a wrong or malformed linked subject before fetching pages.
        parse_episode_metadata(anime, subject, [], now)
        entries: list[dict[str, Any]] = []
        expected_total: int | None = None
        for _ in range(20):
            page = self._get(
                "/episodes",
                subject_id=subject_id,
                type=0,
                limit=200,
                offset=len(entries),
            )
            total = page.get("total")
            items = page.get("data")
            if type(total) is not int or total < 0 or not isinstance(items, list):
                raise MetadataUnavailable("Episode pagination is invalid")
            if expected_total is not None and expected_total != total:
                raise MetadataUnavailable(
                    "Episode pagination changed during the request"
                )
            expected_total = total
            if not items and len(entries) < total:
                raise MetadataUnavailable("Episode pagination ended early")
            entries.extend(items)
            if len(entries) > total:
                raise MetadataUnavailable(
                    "Episode pagination exceeded the advertised count"
                )
            if len(entries) >= total:
                return parse_episode_metadata(anime, subject, entries, now)
        raise MetadataUnavailable("Episode pagination exceeds the safe limit")


class EpisodeEnricher:
    def __init__(self, client: BangumiClient | None = None) -> None:
        self.client = client or BangumiClient()
        self.snapshots: dict[int, EpisodeMetadata] = {}

    @staticmethod
    def due(anime: Anime, now: datetime) -> bool:
        info = anime.episodes
        if anime.external_ids.bangumi is None:
            return False
        if info is None or info.attempted_at is None:
            return True
        days = (
            30 if info.airing_status == "finished" and info.fetch_state == "ok" else 1
        )
        if (
            info.airing_status == "finished"
            and info.end_date
            and now.astimezone(TOKYO_TZ).date() - info.end_date < timedelta(days=7)
        ):
            days = 1
        if info.airing_status == "not_yet_aired":
            days = 1
        return now - info.attempted_at >= timedelta(days=days)

    def enrich(self, anime: Anime, now: datetime) -> Anime:
        subject_id = anime.external_ids.bangumi
        if subject_id in self.snapshots:
            return anime.model_copy(update={"episodes": self.snapshots[subject_id]})
        if not self.due(anime, now):
            if subject_id is not None and anime.episodes is not None:
                self.snapshots[subject_id] = anime.episodes
            return anime
        try:
            info = self.client.fetch(anime, now)
        except RequestBudgetExceeded:
            return anime
        except MetadataUnavailable as exc:
            logger.warning(
                "Episode metadata retained for %s: %s", anime.bangumi_id, exc
            )
            info = EpisodeMetadata.model_validate(
                {
                    **(anime.episodes.model_dump() if anime.episodes else {}),
                    "fetch_state": "stale",
                    "attempted_at": now,
                    "note": "更新未成功，保留上次確認資料。"
                    if anime.episodes and anime.episodes.checked_at
                    else "尚未取得可靠集數，之後重新查詢。",
                }
            )
        previous = anime.episodes
        if previous and info.fetch_state == "conflict":
            info = EpisodeMetadata.model_validate(
                {
                    **previous.model_dump(),
                    "fetch_state": "conflict",
                    "airing_status": "unknown",
                    "attempted_at": now,
                    "note": info.note + "保留上次確認資料。",
                }
            )
        if previous and info.fetch_state == "ok":
            missing_known_value = (
                info.total_episodes is None and previous.total_episodes is not None
            ) or (info.aired_episodes is None and previous.aired_episodes is not None)
            regressed = (
                info.aired_episodes is not None
                and previous.aired_episodes is not None
                and info.aired_episodes < previous.aired_episodes
            )
            if missing_known_value or regressed:
                info = EpisodeMetadata.model_validate(
                    {
                        **previous.model_dump(),
                        "fetch_state": "conflict" if regressed else "stale",
                        "airing_status": "unknown"
                        if regressed
                        else previous.airing_status,
                        "attempted_at": now,
                        "note": "來源進度減少，保留上次確認資料並等待核對。"
                        if regressed
                        else "來源資料不完整，保留上次確認資料。",
                    }
                )
        if subject_id is not None:
            self.snapshots[subject_id] = info
        return anime.model_copy(update={"episodes": info})


def refresh_repository_episodes(
    repository: Any,
    now: datetime,
    enricher: EpisodeEnricher | None = None,
    *,
    selected_ids: set[str] | None = None,
) -> set[str]:
    """Refresh mapped entries across all quarters, prioritizing newest first.

    Metadata itself is the persistent cache. No image requests or uploads occur.
    Previously linked long-running anime remain eligible outside crawl windows.
    """
    enricher = enricher or EpisodeEnricher()
    changed: set[str] = set()
    for path in sorted(
        repository.data_dir.glob("*.json"),
        key=lambda path: (
            path.stem.split("_")[0],
            "冬春夏秋".find(path.stem.split("_")[-1]),
        ),
        reverse=True,
    ):
        if not re.fullmatch(r"\d{4}_(冬|春|夏|秋)\.json", path.name):
            continue
        dataset = repository.load_path(path)
        records = [
            enricher.enrich(anime, now)
            if selected_ids is None or anime.bangumi_id in selected_ids
            else anime
            for anime in dataset.anime_list
        ]
        if records == dataset.anime_list:
            continue
        year, season = path.stem.split("_")
        result = repository.write_quarter(
            year=year,
            season=season,
            records=records,
            source_url=dataset.source_url,
            source_count=dataset.quality.source_count,
            parse_failure_count=dataset.quality.parse_failure_count,
            generated_at=now,
        )
        if result.changed:
            changed.add(path.name)
    return changed
