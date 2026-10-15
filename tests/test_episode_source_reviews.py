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


@pytest.mark.parametrize(
    ("card_id", "subject_id", "other_card_id"),
    [("anime-273", 303878, "anime-782"), ("anime-782", 529232, "anime-273")],
)
def test_same_source_title_keeps_oshiri_series_separate(
    anime_record_factory, card_id, subject_id, other_card_id
):
    from services.episode_metadata import subject_identity_matches

    evidence = [
        json.loads(line)
        for line in Path(
            "docs/episode-followup-2026-10-15/archive-provider-evidence.jsonl"
        )
        .read_text()
        .splitlines()
    ]
    subject = next(
        row["subject"] for row in evidence if row["subject_id"] == subject_id
    )
    anime = Anime.model_validate(
        {
            **anime_record_factory(273, name="屁屁偵探"),
            "bangumi_id": card_id,
            "original_name": "おしりたんてい",
            "external_ids": {"bangumi": subject_id},
        }
    )
    assert subject_identity_matches(anime, subject)
    assert not subject_identity_matches(
        anime.model_copy(update={"bangumi_id": other_card_id}), subject
    )
    assert not subject_identity_matches(anime, {**subject, "date": "2018-05-03"})


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


def test_reviewed_mewkle_main_scope_survives_provider_mixed_total(anime_record_factory):
    review = next(r for r in source_reviews() if r["subject_id"] == 321387)
    client = Mock()
    anime = reviewed_anime(review, anime_record_factory)
    result = EpisodeEnricher(client, retry_failed=True).enrich(
        anime, NOW + timedelta(days=60)
    )
    assert result.episodes.fetch_state == "ok"
    assert result.episodes.airing_status == "finished"
    assert result.episodes.total_episodes == result.episodes.aired_episodes == 37
    assert result.episodes.end_date.isoformat() == "2022-03-27"
    assert result.episodes.checked_at == anime.episodes.checked_at
    assert "50次" in result.episodes.note
    assert "特別節目13次" in result.episodes.note
    assert "第50話回顧最終回" in result.episodes.note
    client.fetch.assert_not_called()


def test_mewkle_evidence_accounts_for_every_official_programme():
    evidence = json.loads(
        Path("docs/episode-review-2026-10-15/mewkle-scope-evidence.json").read_text()
    )
    rows = evidence["episodes"]
    assert len(rows) == 50
    assert {row["number"] for row in rows} == set(range(1, 51))
    main = {row["number"] for row in rows if row["counted_as_main"]}
    specials = {row["number"] for row in rows if not row["counted_as_main"]}
    assert len(main) == 37
    assert specials == {4, 8, 12, 16, 18, 20, 25, 30, 34, 38, 40, 42, 46}
    assert main.isdisjoint(specials) and main | specials == set(range(1, 51))
    assert all(
        row["provider_type"] == (0 if row["counted_as_main"] else 1) for row in rows
    )
    assert len({row["provider_episode_id"] for row in rows}) == 50
    assert 50 in main
    assert max(row["airdate"] for row in rows) == evidence["end_date"]
    review = next(r for r in source_reviews() if r["subject_id"] == 321387)
    assert len(main) == review["metadata"]["total_episodes"]


def test_2025_godzilla_uses_new_series_scope(anime_record_factory):
    review = next(r for r in source_reviews() if r["subject_id"] == 561787)
    anime = reviewed_anime(review, anime_record_factory)
    evidence = json.loads(
        Path("docs/episode-links-2025-2026-10-15/provider-evidence.json").read_text()
    )
    source = next(s for s in evidence if s["subject_id"] == 561787)
    subject = {
        **{k: source[k] for k in ("name", "date", "eps", "type", "infobox")},
        "id": 561787,
    }
    result = parse_episode_metadata(anime, subject, source["entries"], NOW)
    assert result.total_episodes is None
    assert result.aired_episodes == 54
    assert result.airing_status == "airing"
    assert result.fetch_state == "ok"
    assert min(e["sort"] for e in source["entries"]) == 66
    assert max(e["sort"] for e in source["entries"]) == 119
    with pytest.raises(MetadataUnavailable):
        parse_episode_metadata(
            anime, {**subject, "date": "2024-04-03"}, source["entries"], NOW
        )


def test_lazarus_combined_source_title_requires_reviewed_card(anime_record_factory):
    from services.episode_metadata import reviewed_identities, subject_identity_matches

    review = next(r for r in reviewed_identities() if r["card_id"] == "anime-1775")
    anime = Anime.model_validate(
        {
            **anime_record_factory(1775, name=review["anime_name"]),
            "original_name": review["source_original_name"],
            "external_ids": {"bangumi": review["subject_id"]},
        }
    )
    evidence = json.loads(
        Path("docs/episode-links-2025-2026-10-15/provider-evidence.json").read_text()
    )
    source = next(s for s in evidence if s["subject_id"] == 446296)
    subject = {**source, "id": source["subject_id"]}
    assert subject_identity_matches(anime, subject)
    assert not subject_identity_matches(
        anime.model_copy(update={"bangumi_id": "anime-99999"}), subject
    )


def test_2025_official_finished_sources_preserve_counts_when_api_unavailable(
    anime_record_factory,
):
    for sid, count in [(519958, 5), (499140, 8), (516604, 8)]:
        review = next(r for r in source_reviews() if r["subject_id"] == sid)
        client = Mock()
        result = EpisodeEnricher(client, retry_failed=True).enrich(
            reviewed_anime(review, anime_record_factory), NOW + timedelta(days=60)
        )
        assert result.episodes.fetch_state == "ok"
        assert result.episodes.airing_status == "finished"
        assert result.episodes.aired_episodes == result.episodes.total_episodes == count
        client.fetch.assert_not_called()
