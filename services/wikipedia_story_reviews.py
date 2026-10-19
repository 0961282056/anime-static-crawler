"""Reviewed Wikipedia excerpts with immutable revisions and reuse attribution."""

import json
from datetime import datetime
from functools import cache
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


@cache
def wikipedia_story_reviews() -> list[dict]:
    return json.loads(
        Path(__file__)
        .with_name("reviewed_wikipedia_stories.json")
        .read_text(encoding="utf-8")
    )


def reviewed_wikipedia_story(anime):
    return next(
        (
            row
            for row in wikipedia_story_reviews()
            if row["card_id"] == anime.bangumi_id
            and row["anime_name"] == anime.anime_name
            and row["original_name"] == anime.original_name
            and row["subject_id"] == anime.external_ids.bangumi
            and row["anilist_id"] == anime.external_ids.anilist
        ),
        None,
    )


def wikipedia_evidence_matches(source, row) -> bool:
    if source.wikipedia is None:
        return False
    actual = source.wikipedia.model_dump(mode="json")
    expected = row["attribution"]
    return (
        source.provider == "wikipedia"
        and source.method == "wikipedia_excerpt"
        and source.subject_id == row["subject_id"]
        and source.source_url == row["source_url"]
        and source.checked_at == datetime.fromisoformat(row["checked_at"])
        and source.source_snapshot_date is None
        and actual.keys() == expected.keys()
        and all(
            source.wikipedia.revision_at == datetime.fromisoformat(value)
            if key == "revision_at"
            else actual[key] == value
            for key, value in expected.items()
        )
    )


def public_wikipedia_stories() -> dict:
    result = {}
    for row in wikipedia_story_reviews():
        attribution = row["attribution"]
        source = urlsplit(row["source_url"])
        history = urlsplit(attribution["contributors_url"])
        if (
            row["card_id"] in result
            or any(
                url.scheme != "https"
                or url.hostname != "zh.wikipedia.org"
                or url.username is not None
                or url.password is not None
                or url.port not in (None, 443)
                or url.path != "/w/index.php"
                for url in (source, history)
            )
            or parse_qs(source.query)
            != {
                "title": [attribution["page_title"]],
                "oldid": [str(attribution["revision_id"])],
                "variant": ["zh-tw"],
            }
            or parse_qs(history.query)
            != {"title": [attribution["page_title"]], "action": ["history"]}
            or attribution["license"] != "CC BY-SA 4.0"
            or attribution["license_url"]
            != "https://creativecommons.org/licenses/by-sa/4.0/"
        ):
            raise ValueError("Invalid or duplicate reviewed Wikipedia binding")
        result[row["card_id"]] = row
    return result
