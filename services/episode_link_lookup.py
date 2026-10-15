"""Explicit manual linking by exact original title, anime type and quarter.

Search ranking is never an identity decision. Only one exact in-quarter anime
match is accepted, then its full subject is checked again before storing a link.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from models import Anime, ExternalIds
from services.episode_metadata import (
    API_ORIGIN,
    BangumiClient,
    MetadataUnavailable,
    RequestBudgetExceeded,
    _date,
    _name,
    parse_episode_metadata,
    subject_names,
)

logger = logging.getLogger(__name__)


def quarter_bounds(year: str, season: str) -> tuple[date, date]:
    month = {"冬": 1, "春": 4, "夏": 7, "秋": 10}[season]
    start = date(int(year), month, 1)
    end = date(int(year) + 1, 1, 1) if month == 10 else date(int(year), month + 3, 1)
    return start, end


def fetch_quarter_catalog(
    client: BangumiClient, *, year: str, season: str
) -> list[dict[str, Any]]:
    """Retrieve complete monthly pages before choosing any exact identity."""
    start, _ = quarter_bounds(year, season)
    catalog: dict[int, dict[str, Any]] = {}
    for month in range(start.month, start.month + 3):
        entries: list[dict[str, Any]] = []
        monthly_identities: set[int] = set()
        expected = None
        for _ in range(10):
            page = client._get(
                "/subjects",
                type=2,
                year=int(year),
                month=month,
                sort="date",
                limit=50,
                offset=len(entries),
            )
            total, items = page.get("total"), page.get("data")
            if type(total) is not int or total < 0 or not isinstance(items, list):
                raise MetadataUnavailable("Monthly anime catalog is invalid")
            if expected is not None and total != expected:
                raise MetadataUnavailable(
                    "Monthly anime catalog changed during pagination"
                )
            expected = total
            if not items and len(entries) < total:
                raise MetadataUnavailable("Monthly anime catalog ended early")
            for item in items:
                identity = item.get("id") if isinstance(item, dict) else None
                if type(identity) is not int or identity <= 0:
                    raise MetadataUnavailable(
                        "Monthly anime catalog identity is invalid"
                    )
                if identity in monthly_identities:
                    raise MetadataUnavailable(
                        "Monthly anime catalog repeated an identity"
                    )
                monthly_identities.add(identity)
            entries.extend(items)
            if len(entries) > total:
                raise MetadataUnavailable("Monthly anime catalog exceeded its count")
            if len(entries) == total:
                break
        else:
            raise MetadataUnavailable(
                "Monthly anime catalog exceeds the safe page limit"
            )
        for item in entries:
            if (
                not isinstance(item, dict)
                or type(item.get("id")) is not int
                or item["id"] <= 0
            ):
                raise MetadataUnavailable("Monthly anime catalog identity is invalid")
            identity = item["id"]
            if identity in catalog and catalog[identity] != item:
                raise MetadataUnavailable(
                    "Catalog duplicate identity has conflicting data"
                )
            catalog[identity] = item
    return list(catalog.values())


def exact_subject_id(
    anime: Anime, page: dict[str, Any], start: date, end: date
) -> int | None:
    items, total = page.get("data"), page.get("total")
    if (
        not isinstance(items, list)
        or type(total) is not int
        or total < 0
        or total != len(items)
    ):
        raise MetadataUnavailable("Search results are incomplete or invalid")
    if not anime.original_name or not _name(anime.original_name):
        return None
    matches = set()
    for item in items:
        if not isinstance(item, dict):
            raise MetadataUnavailable("Search subject is invalid")
        sid = item.get("id")
        aired = _date(item.get("date"))
        names = subject_names(item)
        if (
            type(sid) is int
            and sid > 0
            and item.get("type") == 2
            and aired is not None
            and start <= aired < end
            and _name(anime.original_name) in names
        ):
            matches.add(sid)
    return next(iter(matches)) if len(matches) == 1 else None


def propagate_verified_links(
    repository: Any, *, selected_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    """Share verified identity only for an identical source card and both titles."""
    datasets = [
        (path, repository.load_path(path)) for path in repository.validate_all()
    ]
    identities: dict[tuple[str, str, str], list[tuple[str, Anime]]] = defaultdict(list)
    for path, dataset in datasets:
        for anime in dataset.anime_list:
            if anime.external_ids.bangumi and anime.original_name:
                identities[
                    (anime.bangumi_id, anime.anime_name, anime.original_name)
                ].append((path.stem, anime))
    evidence = []
    for path, dataset in datasets:
        records = []
        changed = False
        for anime in dataset.anime_list:
            candidates = identities.get(
                (anime.bangumi_id, anime.anime_name, anime.original_name or ""), []
            )
            ids = {item.external_ids.bangumi for _, item in candidates}
            if (
                anime.external_ids.bangumi is None
                and len(ids) == 1
                and (selected_ids is None or anime.bangumi_id in selected_ids)
            ):
                successful = [
                    pair
                    for pair in candidates
                    if pair[1].episodes
                    and pair[1].episodes.fetch_state == "ok"
                    and pair[1].episodes.checked_at
                ]
                source_quarter, source = (
                    max(successful, key=lambda pair: pair[1].episodes.checked_at)
                    if successful
                    else candidates[0]
                )
                updates = {
                    "external_ids": ExternalIds(
                        bangumi=source.external_ids.bangumi,
                        anilist=anime.external_ids.anilist,
                    ),
                    "episodes": source.episodes if successful else None,
                }
                anime = anime.model_copy(update=updates)
                evidence.append(
                    {
                        "quarter": path.stem,
                        "source_quarter": source_quarter,
                        "card_id": anime.bangumi_id,
                        "original_name": anime.original_name,
                        "subject_id": anime.external_ids.bangumi,
                        "method": "identical-source-card+localized-title+original-title+unique-id",
                    }
                )
                changed = True
            records.append(anime)
        if changed:
            year, season = path.stem.split("_")
            repository.write_quarter(
                year=year,
                season=season,
                records=records,
                source_url=dataset.source_url,
                source_count=dataset.quality.source_count,
                parse_failure_count=dataset.quality.parse_failure_count,
            )
    return evidence


def resolve_missing_links(
    repository: Any,
    client: BangumiClient,
    now: datetime,
    *,
    year: str,
    season: str,
    catalog: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    start, end = quarter_bounds(year, season)
    dataset = repository.load_quarter(year, season)
    if dataset is None:
        return []
    records = []
    evidence = []
    stopped = False
    for anime in dataset.anime_list:
        if not stopped and anime.external_ids.bangumi is None and anime.original_name:
            try:
                page = (
                    {"data": catalog, "total": len(catalog)}
                    if catalog is not None
                    else client._request(
                        API_ORIGIN + "/search/subjects",
                        params={"limit": 25, "offset": 0},
                        payload={
                            "keyword": anime.original_name,
                            "filter": {
                                "type": [2],
                                "air_date": [
                                    f">={start.isoformat()}",
                                    f"<{end.isoformat()}",
                                ],
                            },
                        },
                    )
                )
                sid = exact_subject_id(anime, page, start, end)
                if sid is not None:
                    subject = client._get(f"/subjects/{sid}")
                    aired = _date(subject.get("date"))
                    linked = anime.model_copy(
                        update={
                            "external_ids": ExternalIds(
                                bangumi=sid, anilist=anime.external_ids.anilist
                            )
                        }
                    )
                    parse_episode_metadata(linked, subject, [], now)
                    if aired is None or not start <= aired < end:
                        raise MetadataUnavailable(
                            "Full subject date differs from the searched quarter"
                        )
                    anime = linked.model_copy(update={"episodes": None})
                    evidence.append(
                        {
                            "quarter": f"{year}_{season}",
                            "card_id": anime.bangumi_id,
                            "original_name": anime.original_name,
                            "subject_id": sid,
                            "air_date": aired.isoformat(),
                            "source_url": f"https://bgm.tv/subject/{sid}",
                            "method": "exact-original-title+anime-type+first-air-quarter+full-subject",
                        }
                    )
            except RequestBudgetExceeded:
                stopped = True
            except MetadataUnavailable as exc:
                logger.warning(
                    "Manual link lookup deferred for %s: %s", anime.bangumi_id, exc
                )
                stopped = client.unavailable
        records.append(anime)
    if evidence:
        repository.write_quarter(
            year=year,
            season=season,
            records=records,
            source_url=dataset.source_url,
            source_count=dataset.quality.source_count,
            parse_failure_count=dataset.quality.parse_failure_count,
        )
    return evidence
