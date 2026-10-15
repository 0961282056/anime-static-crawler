"""Refresh cadence and observable age, without network or image side effects."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from models import Anime
from services.episode_source_reviews import reviewed_source

Identity = tuple[str, str, str | None, int | None]
Quarter = tuple[int, int]


def identity(anime: Anime) -> Identity:
    return (
        anime.bangumi_id,
        anime.anime_name,
        anime.original_name,
        anime.external_ids.bangumi,
    )


def quarter_context(rows: Iterable[tuple[str, Anime]]) -> dict[Identity, Quarter]:
    result: dict[Identity, Quarter] = {}
    for quarter, anime in rows:
        value = (int(quarter[:4]), "冬春夏秋".index(quarter[-1]))
        key = identity(anime)
        result[key] = max(result.get(key, value), value)
    return result


@dataclass(frozen=True)
class RefreshPolicy:
    days: int
    priority: bool


def refresh_policy(
    anime: Anime, now: datetime, latest_quarter: Quarter | None = None
) -> RefreshPolicy:
    now = now.astimezone(ZoneInfo("Asia/Tokyo"))
    info = anime.episodes
    current = (now.year, (now.month - 1) // 3)
    recent_end = bool(
        info and info.end_date and 0 <= (now.date() - info.end_date).days < 7
    )
    if (
        info is None
        or info.airing_status in {"airing", "not_yet_aired"}
        or recent_end
        or (
            info.airing_status != "finished"
            and (latest_quarter is None or latest_quarter >= current)
        )
    ):
        return RefreshPolicy(1, True)
    if info.fetch_state != "ok":
        return RefreshPolicy(7, False)
    if info.airing_status == "finished":
        return RefreshPolicy(90, False)
    return RefreshPolicy(30, False)


def next_refresh_at(
    anime: Anime, now: datetime, latest_quarter: Quarter | None = None
) -> datetime | None:
    if anime.episodes is None or anime.episodes.attempted_at is None:
        return None
    policy = refresh_policy(anime, now, latest_quarter)
    days = policy.days
    if not policy.priority and anime.episodes.fetch_state == "ok":
        # Assign a stable calendar phase. Bulk verification is spread across
        # the entire first cycle; subsequent successful cycles keep 30/90 days.
        digest = hashlib.sha256(repr(identity(anime)).encode()).digest()
        phase = int.from_bytes(digest[:4], "big") % days
        previous_day = (
            anime.episodes.attempted_at.astimezone(ZoneInfo("Asia/Tokyo"))
            .date()
            .toordinal()
        )
        days = (phase - previous_day) % days or days
    return anime.episodes.attempted_at + timedelta(days=days)


def automatically_refreshed(anime: Anime) -> bool:
    review = reviewed_source(anime)
    return anime.external_ids.bangumi is not None and not (
        review and review["mode"] == "snapshot"
    )


def due(anime: Anime, now: datetime, latest_quarter: Quarter | None = None) -> bool:
    if not automatically_refreshed(anime):
        return False
    target = next_refresh_at(anime, now, latest_quarter)
    return target is None or now >= target


def refresh_statistics(rows: list[tuple[str, Anime]], now: datetime) -> dict:
    context = quarter_context(rows)
    unique: dict[Identity, list[Anime]] = {}
    for _, anime in rows:
        if automatically_refreshed(anime):
            unique.setdefault(identity(anime), []).append(anime)
    result = {
        "automatic_identities": len(unique),
        "priority_identities": 0,
        "priority_overdue": 0,
        "background_overdue": 0,
        "priority_unverified": 0,
        "priority_older_than_48h": 0,
        "oldest_checked_at": None,
        "priority_oldest_checked_at": None,
    }
    checked = []
    priority_checked = []
    for key, copies in unique.items():
        priority = any(refresh_policy(a, now, context[key]).priority for a in copies)
        if any(due(a, now, context[key]) for a in copies):
            result["priority_overdue" if priority else "background_overdue"] += 1
        stamps = [
            a.episodes.checked_at
            for a in copies
            if a.episodes and a.episodes.checked_at
        ]
        if stamps:
            checked.append(min(stamps))
        if not priority:
            continue
        result["priority_identities"] += 1
        if len(stamps) != len(copies):
            result["priority_unverified"] += 1
        if stamps:
            oldest = min(stamps)
            priority_checked.append(oldest)
            if now - oldest > timedelta(hours=48):
                result["priority_older_than_48h"] += 1
    result["oldest_checked_at"] = min(checked).isoformat() if checked else None
    result["priority_oldest_checked_at"] = (
        min(priority_checked).isoformat() if priority_checked else None
    )
    return result
