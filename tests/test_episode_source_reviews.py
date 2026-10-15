import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from models import Anime, EpisodeMetadata
from services.episode_metadata import (
    EpisodeEnricher,
    MetadataUnavailable,
    parse_episode_metadata,
)
from services.episode_source_reviews import reviewed_source, source_reviews

NOW = datetime.fromisoformat("2026-10-15T09:00:00+08:00")


def reviewed_anime(review, factory):
    return Anime.model_validate(
        {
            **factory(2142, name=review["anime_name"]),
            "bangumi_id": review["card_id"],
            "original_name": review["original_name"],
            "external_ids": {"bangumi": review["subject_id"]},
            "episodes": review["metadata"],
        }
    )


def test_snapshot_is_exactly_bound_and_not_rechecked(anime_record_factory):
    review = next(r for r in source_reviews() if r["subject_id"] == 425998)
    anime = reviewed_anime(review, anime_record_factory)
    client = Mock()
    result = EpisodeEnricher(client, retry_failed=True).enrich(
        anime, NOW + timedelta(days=40)
    )
    assert result.episodes.total_episodes == 16
    assert result.episodes.checked_at == anime.episodes.checked_at
    client.fetch.assert_not_called()
    assert (
        reviewed_source(anime.model_copy(update={"original_name": "another season"}))
        is None
    )
    assert (
        reviewed_source(
            anime.model_copy(
                update={
                    "external_ids": anime.external_ids.model_copy(update={"bangumi": 1})
                }
            )
        )
        is None
    )


@pytest.mark.parametrize(
    ("sid", "total", "aired"), [(568244, 12, 2), (584847, 11, 1), (418599, None, 135)]
)
def test_live_official_scope_excludes_provider_phantoms(
    anime_record_factory, sid, total, aired
):
    review = next(r for r in source_reviews() if r["subject_id"] == sid)
    anime = reviewed_anime(review, anime_record_factory)
    evidence = json.loads(
        Path("docs/episode-review-2026-10-15/provider-evidence.json").read_text()
    )
    evidence = next(e for e in evidence if e["subject_id"] == sid)
    subject = {
        "id": sid,
        "type": 2,
        "name": evidence["name"],
        "date": evidence["date"],
        "eps": evidence["eps"],
        "infobox": [{"key": "话数", "value": str(evidence["eps"])}],
    }
    entries = [
        {"id": i, "subject_id": sid, "sort": n, "airdate": d, "type": 0}
        for i, n, d in evidence["entries"]
    ]
    result = parse_episode_metadata(anime, subject, entries, NOW)
    assert (result.total_episodes, result.aired_episodes) == (total, aired)
    assert result.airing_status == "airing"
    assert result.fetch_state == "ok"
    assert result.source_urls[0] == review["metadata"]["source_urls"][0]
    with pytest.raises(MetadataUnavailable):
        parse_episode_metadata(anime, {**subject, "date": "2000-01-01"}, entries, NOW)


def test_only_reviewed_official_url_is_allowed():
    url = source_reviews()[0]["metadata"]["source_urls"][0]
    assert EpisodeMetadata(source_urls=[url]).source_urls == [url]
    for unsafe in (
        url + "?redirect=evil",
        url.replace("https:", "http:"),
        "javascript:alert(1)",
        "https://evil.example/",
    ):
        with pytest.raises(ValidationError):
            EpisodeMetadata(source_urls=[unsafe])


def test_uncertain_programme_scope_keeps_unknown_counts(anime_record_factory):
    review = next(r for r in source_reviews() if r["subject_id"] == 321387)
    result = EpisodeEnricher(Mock()).enrich(
        reviewed_anime(review, anime_record_factory), NOW
    )
    assert result.episodes.fetch_state == "conflict"
    assert result.episodes.total_episodes is None
    assert result.episodes.aired_episodes is None
