"""Exact reviewed source scope, independent of models to avoid import cycles."""

import json
import re
from functools import cache
from pathlib import Path
from typing import Any


@cache
def source_reviews() -> list[dict[str, Any]]:
    return json.loads(
        Path(__file__)
        .with_name("reviewed_episode_sources.json")
        .read_text(encoding="utf-8")
    )


def reviewed_source(anime: Any) -> dict[str, Any] | None:
    return next(
        (
            review
            for review in source_reviews()
            if review["card_id"] == anime.bangumi_id
            and review["anime_name"] == anime.anime_name
            and review["original_name"] == anime.original_name
            and review["subject_id"] == anime.external_ids.bangumi
        ),
        None,
    )


def reviewed_source_url(value: str) -> bool:
    if re.fullmatch(
        r"https://www\.web\.nhk/tv/an/zenitendo/pl/series-tep-4NX2MRW758/list(?:\?page=(?:[1-9]|[1-3][0-9]|40))?",
        value,
    ):
        return True
    return any(
        value in review["metadata"]["source_urls"] for review in source_reviews()
    )
