"""Offline, reviewed AniList references from a pinned community data snapshot.

These links do not establish live AniList availability or episode-count scope.
Only an exact reviewed quarter, source card, both titles and Bangumi ID match.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import Any

from models import TAIPEI_TZ


@cache
def external_link_reviews() -> dict[tuple, dict]:
    document = json.loads(
        Path(__file__)
        .with_name("reviewed_external_links.json")
        .read_text(encoding="utf-8")
    )
    source = document["source"]
    revision = source["revision"]
    if (
        document["schema_version"] != 1
        or source["provider"] != "bangumi_data"
        or not re.fullmatch(r"[a-f0-9]{40}", revision)
        or source["source_url"]
        != f"https://github.com/bangumi-data/bangumi-data/blob/{revision}/dist/data.json"
        or not re.fullmatch(r"[a-f0-9]{64}", source["document_sha256"])
        or source["license"] != "CC BY 4.0"
        or source["license_url"] != "https://creativecommons.org/licenses/by/4.0/"
        or datetime.fromisoformat(source["checked_at"]).tzinfo is None
        or datetime.fromisoformat(source["published_at"]).tzinfo is None
    ):
        raise ValueError("Invalid reviewed external-link source evidence")

    indexed = {}
    bangumi_pairs: dict[int, int] = {}
    anilist_pairs: dict[int, int] = {}
    for row in document["entries"]:
        if (
            not re.fullmatch(r"anime-[0-9]+", row["card_id"])
            or not row["anime_name"]
            or not row["original_name"]
            or type(row["subject_id"]) is not int
            or row["subject_id"] <= 0
            or type(row["anilist_id"]) is not int
            or row["anilist_id"] <= 0
            or type(row["source_item_index"]) is not int
            or row["source_item_index"] < 0
            or row["source_type"] not in {"tv", "web"}
            or not row["quarters"]
        ):
            raise ValueError("Invalid reviewed external-link identity")
        begin = datetime.fromisoformat(row["source_begin"])
        if begin.tzinfo is None:
            raise ValueError("External-link source dates require a timezone")
        subject_id, anilist_id = row["subject_id"], row["anilist_id"]
        if (
            bangumi_pairs.setdefault(subject_id, anilist_id) != anilist_id
            or anilist_pairs.setdefault(anilist_id, subject_id) != subject_id
        ):
            raise ValueError("Reviewed external-link pairs must be one-to-one")
        for quarter in row["quarters"]:
            if not re.fullmatch(r"[0-9]{4}_[冬春夏秋]", quarter):
                raise ValueError("Invalid reviewed external-link quarter")
            year, season = quarter.split("_")
            month = {"冬": 1, "春": 4, "夏": 7, "秋": 10}[season]
            end = (
                date(int(year) + 1, 1, 1)
                if month == 10
                else date(int(year), month + 3, 1)
            )
            if begin.astimezone(TAIPEI_TZ).date() >= end:
                raise ValueError("External-link source begins after reviewed quarter")
            key = (
                quarter,
                row["card_id"],
                row["anime_name"],
                row["original_name"],
                subject_id,
            )
            if key in indexed:
                raise ValueError("Duplicate reviewed external-link identity")
            indexed[key] = row
    return indexed


def reviewed_anilist_link(quarter: str, anime: Any) -> dict | None:
    return external_link_reviews().get(
        (
            quarter,
            anime.bangumi_id,
            anime.anime_name,
            anime.original_name,
            anime.external_ids.bangumi,
        )
    )
