"""Manually verified official summary excerpts bound to exact card identities."""

import json
import re
from functools import cache
from pathlib import Path
from urllib.parse import urlsplit


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


def public_story_sources() -> dict[str, list]:
    """Generate the browser's exact allowlist from the reviewed registry."""
    result = {}
    for row in story_reviews():
        card, subject, url = row["card_id"], row["subject_id"], row["source_url"]
        parsed = urlsplit(url)
        if (
            not re.fullmatch(r"anime-\d+", card)
            or card in result
            or type(subject) is not int
            or subject <= 0
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
        ):
            raise ValueError("Invalid or duplicate reviewed official summary binding")
        result[card] = [subject, url]
    return result
