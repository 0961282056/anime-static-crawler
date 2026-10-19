"""Exact, reviewed historical broadcast weekdays; never infer from a release date."""

import json
import re
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import get_args
from urllib.parse import urlsplit

from models import BroadcastDay


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
            or row["weekday"] not in set(get_args(BroadcastDay)) - {"無首播日期"}
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
            or (
                "display_note" in row
                and (
                    not isinstance(row["display_note"], str)
                    or not row["display_note"].strip()
                    or len(row["display_note"]) > 160
                )
            )
        ):
            raise ValueError("Invalid reviewed broadcast identity or evidence")
        seen.add(row["card_id"])
    return rows


def public_broadcast_sources() -> dict[str, dict]:
    """Use the same reviewed identity and note in the generated browser config."""
    fields = (
        "anime_name",
        "original_name",
        "subject_id",
        "weekday",
        "display_note",
        "source_url",
    )
    return {
        row["card_id"]: {field: row[field] for field in fields}
        for row in broadcast_reviews()
        if row.get("display_note")
    }


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
