"""Fill missing summaries only from an exactly bound, attributed provider entry."""

from __future__ import annotations

from datetime import date, datetime

from models import Anime, StorySource
from services.episode_metadata import _name, reviewed_identity


def enrich_story(
    anime: Anime,
    subject: dict,
    now: datetime,
    *,
    method: str,
    snapshot_date: date | None = None,
) -> Anime:
    if anime.story != "暫無簡介":
        return anime
    if subject.get("id") != anime.external_ids.bangumi or subject.get("type") != 2:
        return anime
    if not anime.original_name or not isinstance(subject.get("name"), str):
        return anime
    if _name(anime.original_name) != _name(subject["name"]):
        review = reviewed_identity(anime, subject)
        # Episode-only aliases can describe a component of a parent entry.
        # Summary reuse requires separate proof of the complete subject scope.
        if not review or review.get("summary_scope") != "full_linked_subject":
            return anime
    summary = subject.get("summary")
    if not isinstance(summary, str) or not 20 <= len(summary.strip()) <= 10_000:
        return anime
    if summary.strip() in {"暂无简介", "暫無簡介", "暂无介绍", "暫無介紹"}:
        return anime
    evidence = StorySource(
        subject_id=subject["id"],
        checked_at=now,
        method=method,
        source_snapshot_date=snapshot_date,
    )
    return Anime.model_validate(
        {**anime.model_dump(), "story": summary.strip(), "story_source": evidence}
    )
