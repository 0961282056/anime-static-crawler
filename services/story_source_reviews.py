"""Manually verified official summary excerpts bound to exact card identities."""

import json
from functools import cache
from pathlib import Path


@cache
def story_reviews() -> list[dict]:
    return json.loads(
        Path(__file__)
        .with_name("reviewed_story_sources.json")
        .read_text(encoding="utf-8")
    )


def reviewed_story(anime):
    return next(
        (
            row
            for row in story_reviews()
            if row["card_id"] == anime.bangumi_id
            and row["anime_name"] == anime.anime_name
            and row["original_name"] == anime.original_name
            and row["subject_id"] == anime.external_ids.bangumi
        ),
        None,
    )
