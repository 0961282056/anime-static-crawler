"""Bounded, optional episode enrichment using explicit Bangumi source links.

Only main episodes are counted. A schedule entry is not proof of broadcast:
same-day entries remain uncounted until their source date has passed in Japan.
Finished additionally requires an explicit past end date and a complete list.
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from models import Anime, EpisodeMetadata
from services import episode_refresh
from services.episode_source_reviews import reviewed_source
from services.response_limits import bounded_response

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
    # Providers sometimes append invisible copy/paste marks to valid dates.
    # Trim boundary marks only; annotations and malformed dates still fail.
    value = re.sub(r"^[\s\u200b\ufeff]+|[\s\u200b\ufeff]+$", "", value)
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
        if not character.isspace()
    )


def _infobox_items(subject: dict[str, Any]) -> list[dict[str, Any]]:
    items = subject.get("infobox", [])
    if not isinstance(items, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("key"), str)
        for item in items
    ):
        raise MetadataUnavailable("Anime infobox is invalid")
    return items


def _infobox(subject: dict[str, Any], keys: set[str]) -> list[str]:
    return [
        item["value"]
        for item in _infobox_items(subject)
        if item.get("key") in keys and isinstance(item.get("value"), str)
    ]


def subject_names(subject: dict[str, Any]) -> set[str]:
    """Canonical names and explicit provider aliases, normalized identically."""
    names = [subject.get("name", ""), subject.get("name_cn", "")]
    for item in _infobox_items(subject):
        if item.get("key") == "别名":
            value = item.get("value")
            if isinstance(value, str):
                names.append(value)
            elif isinstance(value, list):
                names.extend(
                    alias.get("v", "") for alias in value if isinstance(alias, dict)
                )
    return {_name(name) for name in names if isinstance(name, str) and _name(name)}


@cache
def reviewed_identities() -> list[dict[str, Any]]:
    return json.loads(
        Path(__file__)
        .with_name("reviewed_episode_identities.json")
        .read_text(encoding="utf-8")
    )


def reviewed_source_subject_id(
    card_id: str, anime_name: str, original_name: str | None, source_id: int | None
) -> int | None:
    """Correct only a reviewed, exact source-card tuple containing a wrong ID."""
    for review in reviewed_identities():
        if (
            review.get("source_subject_id") == source_id
            and source_id is not None
            and review["card_id"] == card_id
            and review["anime_name"] == anime_name
            and review["source_original_name"] == original_name
        ):
            return review["subject_id"]
    return source_id


def reviewed_identity(anime: Anime, subject: dict[str, Any]) -> dict[str, Any] | None:
    """Return only the exact reviewed card/title/ID/first-date binding."""
    if subject.get("id") != anime.external_ids.bangumi or subject.get("type") != 2:
        return None
    return next(
        (
            review
            for review in reviewed_identities()
            if review["card_id"] == anime.bangumi_id
            and review["anime_name"] == anime.anime_name
            and review["source_original_name"] == anime.original_name
            and review["subject_id"] == anime.external_ids.bangumi
            and review["subject_name"] == subject.get("name")
            and _date(review["first_air_date"]) == _date(subject.get("date"))
        ),
        None,
    )


def subject_identity_matches(anime: Anime, subject: dict[str, Any]) -> bool:
    """Exact provider names, or a manually reviewed card/title/ID/date tuple."""
    if subject.get("id") != anime.external_ids.bangumi or subject.get("type") != 2:
        return False
    names = subject_names(subject)
    if not anime.original_name or _name(anime.original_name) in names:
        return True
    return reviewed_identity(anime, subject) is not None


def parse_episode_metadata(
    anime: Anime,
    subject: dict[str, Any],
    entries: list[dict[str, Any]],
    now: datetime,
) -> EpisodeMetadata:
    subject_id = anime.external_ids.bangumi
    if subject.get("id") != subject_id or subject.get("type") != 2:
        raise MetadataUnavailable("Linked subject is not the expected anime")
    infobox = _infobox_items(subject)
    review = reviewed_source(anime)
    scoped = bool(
        review
        and review["mode"] == "live"
        and review["subject_name"] == subject.get("name")
        and review["first_air_date"] == subject.get("date")
    )
    if not scoped and not subject_identity_matches(anime, subject):
        raise MetadataUnavailable(
            "Linked anime title differs from the source original title"
        )

    if review and review["mode"] == "live" and not scoped:
        raise MetadataUnavailable("Reviewed provider identity changed; recheck scope")
    if scoped:
        if any(
            not isinstance(entry, dict) or entry.get("subject_id") != subject_id
            for entry in entries
        ):
            raise MetadataUnavailable("Episode belongs to a different subject")
        # The official range overrides only this exact reviewed card/provider tuple.
        subject = {
            **subject,
            "eps": review["total_override"],
            "infobox": [
                item for item in infobox if item.get("key") not in {"话数", "話數"}
            ],
        }
        bounds = review["main_range"]
        if bounds:
            entries = [
                entry
                for entry in entries
                if (
                    entry.get("type") != 0
                    or type(entry.get("sort")) in (int, float)
                    and bounds[0] <= entry["sort"] <= bounds[1]
                )
            ]
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
    recent_open = (
        scoped
        and total is None
        and any(
            value is not None and today - timedelta(days=14) <= value < today
            for value in dated
        )
    )
    if aired and (known_pending or recent_partial or recent_open):
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
    invalid_end = bool(start and any(value < start for value in ends))
    if any(value < today for value in ends):
        # A past end date with incomplete evidence is not a current airing
        # signal. It remains unknown unless the complete checks below pass.
        status = "unknown"
    conflict = (
        invalid
        or has_gap
        or bool(start and start > today and aired)
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
            source_kind="bangumi_api",
            aired_episodes=progress,
            airing_status="unknown",
            fetch_state="conflict",
            source_urls=[
                f"https://bgm.tv/subject/{subject_id}",
                f"https://bgm.tv/subject/{subject_id}/ep",
            ],
            # Contradictory evidence is an attempt, never a new success.
            # The enricher restores a prior success separately when available.
            checked_at=None,
            attempted_at=now,
            note="來源話數、正篇集目、首播或結束日期不一致，總集數待確認。",
        )
    if complete:
        status = "finished"
    return EpisodeMetadata(
        source_kind="bangumi_api",
        total_episodes=total,
        aired_episodes=progress,
        airing_status=status,
        episode_count_status="confirmed"
        if complete
        else "planned"
        if total
        else "unknown",
        fetch_state="ok",
        source_urls=(
            review["metadata"]["source_urls"]
            + [f"https://bgm.tv/subject/{subject_id}/ep"]
        )
        if scoped
        else [
            f"https://bgm.tv/subject/{subject_id}",
            f"https://bgm.tv/subject/{subject_id}/ep",
        ],
        checked_at=now,
        attempted_at=now,
        end_date=next(iter(ends)) if len(ends) == 1 and not invalid_end else None,
        note=("依已核對官方範圍自動更新；" + review["scope_note"])
        if scoped
        else "來源結束日期早於首播，未採用；播出狀態待確認。"
        if invalid_end
        else "依已連結條目的首播日期核對正篇；當日集目暫不計入，不代表平台上架進度。",
    )


class BangumiClient:
    """Fixed host, finite pages / timeouts / per-run requests, circuit breaker."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        request_budget: int = 240,
        interval_seconds: float = 0.5,
        time_budget_seconds: float = 600,
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
        self.deadline = time.monotonic() + time_budget_seconds
        self.subjects: dict[int, dict] = {}

    def _get(self, path: str, **params: object) -> dict[str, Any]:
        return self._request(API_ORIGIN + path, params=params)

    def _get_page(self, path: str) -> str:
        return self._request(PAGE_ORIGIN + path, html=True)

    def _request(
        self,
        url: str,
        *,
        html: bool = False,
        params: dict[str, object] | None = None,
        payload: dict[str, Any] | None = None,
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
            request = self.session.get if payload is None else self.session.post
            response = request(
                url,
                params=params,
                timeout=(
                    min(5, available_seconds / 2),
                    min(15, available_seconds / 2),
                ),
                allow_redirects=False,
                stream=True,
                hooks={"response": bounded_response(2 * 1024 * 1024)},
                headers={"Accept": "text/html"} if html else {},
                **({"json": payload} if payload is not None else {}),
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
            update={
                "source_kind": "bangumi_html",
                "note": "Bangumi 頁面備援（bangumi.tv）；" + info.note,
            }
        )

    def _fetch_api(self, anime: Anime, now: datetime) -> EpisodeMetadata:
        subject_id = anime.external_ids.bangumi
        subject = self._get(f"/subjects/{subject_id}")
        # Reject a wrong or malformed linked subject before fetching pages.
        parse_episode_metadata(anime, subject, [], now)
        self.subjects[subject_id] = subject
        entries: list[dict[str, Any]] = []
        identities: set[int] = set()
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
            for item in items:
                identity = item.get("id") if isinstance(item, dict) else None
                if type(identity) is not int or identity <= 0:
                    raise MetadataUnavailable("Episode identity is invalid")
                if identity in identities:
                    raise MetadataUnavailable("Episode pagination repeated an identity")
                identities.add(identity)
            entries.extend(items)
            if len(entries) > total:
                raise MetadataUnavailable(
                    "Episode pagination exceeded the advertised count"
                )
            if len(entries) >= total:
                return parse_episode_metadata(anime, subject, entries, now)
        raise MetadataUnavailable("Episode pagination exceeds the safe limit")


class EpisodeEnricher:
    def __init__(
        self,
        client: BangumiClient | None = None,
        *,
        retry_failed: bool = False,
        official_client: Any = None,
    ) -> None:
        self.client = client or BangumiClient()
        self.retry_failed = retry_failed
        self.official_client = official_client
        self.quarters: dict[episode_refresh.Identity, episode_refresh.Quarter] = {}
        self.deferred: set[tuple[str, str, str | None, int | None]] = set()
        self.snapshots: dict[
            tuple[str, str, str | None, int | None], EpisodeMetadata | None
        ] = {}

    @staticmethod
    def next_refresh_at(
        anime: Anime,
        now: datetime,
        latest_quarter: episode_refresh.Quarter | None = None,
    ) -> datetime | None:
        return episode_refresh.next_refresh_at(anime, now, latest_quarter)

    @staticmethod
    def due(
        anime: Anime,
        now: datetime,
        latest_quarter: episode_refresh.Quarter | None = None,
    ) -> bool:
        return episode_refresh.due(anime, now, latest_quarter)

    def enrich(self, anime: Anime, now: datetime) -> Anime:
        review = reviewed_source(anime)
        if review and review.get("official_adapter") == "nhk_zenitendo":
            from services.official_catalogue import (
                NHKCatalogueClient,
                OfficialSourceUnavailable,
            )

            previous = anime.episodes or EpisodeMetadata.model_validate(
                {**review["metadata"], "source_kind": "manual_official"}
            )
            record = anime.model_copy(update={"episodes": previous})
            if not (
                self.retry_failed and previous.fetch_state != "ok"
            ) and not self.due(
                record, now, self.quarters.get(episode_refresh.identity(record))
            ):
                return record
            key = episode_refresh.identity(record)
            if key not in self.snapshots:
                if self.official_client is None:
                    self.official_client = NHKCatalogueClient()
                try:
                    self.snapshots[key] = self.official_client.fetch(record, now)
                except OfficialSourceUnavailable:
                    self.snapshots[key] = None
                    logger.warning(
                        "NHK official verification failed; previous evidence retained"
                    )
            info = self.snapshots[key]
            if info is None:
                info = previous.model_copy(
                    update={
                        "fetch_state": "stale",
                        "attempted_at": now,
                        "note": "官方目錄本次查證未成功，保留上次已確認節目數與查證時間。",
                    }
                )
            return record.model_copy(update={"episodes": info})
        if review and review["mode"] == "snapshot":
            # Reviewed facts and unresolved scopes keep their verification time.
            return anime.model_copy(
                update={
                    "episodes": EpisodeMetadata.model_validate(
                        {**review["metadata"], "source_kind": "manual_official"}
                    )
                }
            )
        subject_id = anime.external_ids.bangumi
        retry = (
            self.retry_failed
            and anime.episodes is not None
            and anime.episodes.fetch_state != "ok"
            and subject_id is not None
        )
        key = episode_refresh.identity(anime)
        if not retry and not self.due(anime, now, self.quarters.get(key)):
            return anime
        # Share provider evidence only across the same reviewed source identity.
        # Per-record fallback values must never become another quarter's cache.
        if key not in self.snapshots:
            try:
                self.snapshots[key] = self.client.fetch(anime, now)
                self.deferred.discard(key)
            except RequestBudgetExceeded:
                self.deferred.add(key)
                return anime
            except MetadataUnavailable as exc:
                self.deferred.discard(key)
                logger.warning(
                    "Episode metadata retained for %s: %s", anime.bangumi_id, exc
                )
                self.snapshots[key] = None
        info = self.snapshots[key]
        if info is None:
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
        result = anime.model_copy(update={"episodes": info})
        if isinstance(self.client, BangumiClient) and (
            subject := self.client.subjects.get(subject_id)
        ):
            from services.story_metadata import enrich_story

            result = enrich_story(result, subject, now, method="bangumi_api")
        return result


def refresh_repository_episodes(
    repository: Any,
    now: datetime,
    enricher: EpisodeEnricher | None = None,
    *,
    selected_ids: set[str] | None = None,
) -> set[str]:
    """Reserve capacity for both daily priority and staggered background work.

    Metadata itself is the persistent cache. No image requests or uploads occur.
    Previously linked long-running anime remain eligible outside crawl windows.
    """
    enricher = enricher or EpisodeEnricher()
    changed: set[str] = set()
    datasets = []
    pending = []
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
        records = list(dataset.anime_list)
        datasets.append((path, dataset, records))
        for index, anime in enumerate(records):
            if selected_ids is not None and anime.bangumi_id not in selected_ids:
                continue
            pending.append((records, index, anime))
    rows = [
        (path.stem, anime)
        for path, dataset, _ in datasets
        for anime in dataset.anime_list
    ]
    enricher.quarters = episode_refresh.quarter_context(rows)
    groups: dict[bool, list] = {True: [], False: []}
    for records, index, anime in pending:
        quarter = enricher.quarters[episode_refresh.identity(anime)]
        target = enricher.next_refresh_at(anime, now, quarter)
        order = target.timestamp() if target else float("-inf")
        groups[episode_refresh.refresh_policy(anime, now, quarter).priority].append(
            (order, records, index, anime)
        )
    for group in groups.values():
        group.sort(key=lambda item: item[0])

    def process(group: list) -> None:
        for _, records, index, anime in group:
            records[index] = enricher.enrich(anime, now)

    client = enricher.client
    background_due = any(
        enricher.due(anime, now, enricher.quarters[episode_refresh.identity(anime)])
        for _, _, _, anime in groups[False]
    )
    if isinstance(client, BangumiClient) and background_due:
        # Keep at least one quarter of the run's time and up to 64 requests
        # for history, even if the priority group grows or the provider slows.
        reserve = min(64, client.remaining // 3)
        deadline = client.deadline
        client.remaining -= reserve
        client.deadline -= max(0, deadline - time.monotonic()) / 4
        try:
            process(groups[True])
        finally:
            client.remaining += reserve
            client.deadline = deadline
    else:
        process(groups[True])
    process(groups[False])
    # Unused background capacity can finish priority work deferred by its
    # reserved phase. Cached provider evidence still uses per-record guards.
    process(
        [
            item
            for item in groups[True]
            if episode_refresh.identity(item[3]) in enricher.deferred
        ]
    )
    if enricher.deferred:
        logger.warning(
            "Episode request/time budget exhausted: %s identities deferred; "
            "their values and verification times were retained",
            len(enricher.deferred),
        )
    for path, dataset, records in datasets:
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
    stats = episode_refresh.refresh_statistics(
        [(path.stem, anime) for path, _, records in datasets for anime in records], now
    )
    logger.info("Episode refresh health: %s", stats)
    if stats["priority_older_than_48h"] or stats["priority_unverified"]:
        logger.warning("Priority episode verification is overdue or missing: %s", stats)
    return changed
