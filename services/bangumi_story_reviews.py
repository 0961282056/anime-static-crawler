"""Exact, reviewed excerpts from otherwise unsuitable Bangumi summaries."""

import json
from functools import cache
from pathlib import Path


@cache
def bangumi_story_reviews() -> list[dict]:
    return json.loads(
        Path(__file__)
        .with_name("reviewed_bangumi_story_excerpts.json")
        .read_text(encoding="utf-8")
    )


def reviewed_bangumi_story(anime):
    return next(
        (
            row
            for row in bangumi_story_reviews()
            if row["card_id"] == anime.bangumi_id
            and row["anime_name"] == anime.anime_name
            and row["original_name"] == anime.original_name
            and row["subject_id"] == anime.external_ids.bangumi
        ),
        None,
    )


def public_bangumi_story_reviews() -> dict:
    return {row["card_id"]: row for row in bangumi_story_reviews()}
