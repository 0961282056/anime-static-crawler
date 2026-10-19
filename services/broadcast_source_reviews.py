"""Exact, reviewed historical broadcast weekdays; never infer from a release date."""

import json
import re
from datetime import datetime
from functools import cache
from pathlib import Path
from urllib.parse import urlsplit


@cache
def broadcast_reviews() -> list[dict]:
    rows = json.loads(
        Path(__file__)
        .with_name("reviewed_broadcast_sources.json")
        .read_text(encoding="utf-8")
    )
    seen = set()
    for row in rows:
        parsed = urlsplit(row["source_url"])
        if (
            not re.fullmatch(r"anime-\d+", row["card_id"])
            or row["card_id"] in seen
            or type(row["subject_id"]) is not int
            or row["subject_id"] <= 0
            or row["weekday"] not in {"一", "二", "三", "四", "五", "六", "日"}
            or not row["anime_name"]
            or not row["original_name"]
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or datetime.fromisoformat(row["checked_at"]).tzinfo is None
            or not re.fullmatch(r"[a-f0-9]{64}", row["document_sha256"])
            or not row["excerpt"]
            or not row["scope_note"]
        ):
            raise ValueError("Invalid reviewed broadcast identity or evidence")
        seen.add(row["card_id"])
    return rows


def reviewed_broadcast(anime):
    return next(
        (
            row
            for row in broadcast_reviews()
            if (
                row["card_id"],
                row["anime_name"],
                row["original_name"],
                row["subject_id"],
            )
            == (
                anime.bangumi_id,
                anime.anime_name,
                anime.original_name,
                anime.external_ids.bangumi,
            )
        ),
        None,
    )
