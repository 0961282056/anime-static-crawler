"""Exact source display-name changes reviewed without rebinding a work."""

import json
from functools import cache
from pathlib import Path
from typing import Any


@cache
def display_name_reviews() -> list[dict]:
    return json.loads(
        Path(__file__)
        .with_name("reviewed_display_names.json")
        .read_text(encoding="utf-8")
    )


def reviewed_display_rename(previous: Any, incoming: Any) -> bool:
    return any(
        previous.bangumi_id == incoming.bangumi_id == row["card_id"]
        and previous.original_name == incoming.original_name == row["original_name"]
        and previous.external_ids.bangumi
        == incoming.external_ids.bangumi
        == row["subject_id"]
        and previous.external_ids.anilist == row["anilist_id"]
        and incoming.external_ids.anilist in (None, row["anilist_id"])
        and previous.anime_name in row["names"]
        and incoming.anime_name in row["names"]
        for row in display_name_reviews()
    )
