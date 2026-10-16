from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from models import Anime, EpisodeMetadata
from services.episode_metadata import BangumiClient, EpisodeEnricher
from services.episode_refresh import automatically_refreshed, due, next_refresh_at
from services.episode_source_reviews import reviewed_source_url, source_reviews
from services.official_catalogue import (
    LIST_URL,
    NHKCatalogueClient,
    OfficialSourceUnavailable,
    parse_catalogue_page,
)

NOW = datetime.fromisoformat("2026-10-16T10:00:00+08:00")
PREFIX = LIST_URL.removesuffix("/list") + "/ep/"


@pytest.fixture
def anime(anime_record_factory):
    review = next(r for r in source_reviews() if r["card_id"] == "anime-663")
    return Anime(
        **{
            **anime_record_factory(663, name=review["anime_name"]),
            "bangumi_id": "anime-663",
            "original_name": review["original_name"],
            "external_ids": {"bangumi": 315772},
            "episodes": {**review["metadata"], "aired_episodes": 10},
        }
    )


def document(entries, total=13):
    return f"<h1>すべて({total})</h1>" + "".join(
        f'<a href="{PREFIX}{key}"><p>「同じタイトル」</p><span>初回放送日</span><time datetime="{stamp}"></time></a>'
        for key, stamp in entries
    )


def fixture_client(monkeypatch, *, mutated=None, future=False):
    entries = [(f"EP{i}", "2025-01-01T18:40:00+09:00") for i in range(13)]
    if future:
        entries[-3:] = [
            ("EP10", "2026-10-16T00:30:00+09:00"),
            ("EP11", "2026-10-17T18:40:00+09:00"),
            ("EP12", "2026-10-18T18:40:00+09:00"),
        ]
    pages = {
        LIST_URL: document(entries[:10]),
        LIST_URL + "?page=2": document(entries[10:]),
    }
    if mutated:
        mutated(pages)
    client = NHKCatalogueClient(
        baseline=[{"source_url": PREFIX + "EP0", "first_air_date": "2025-01-01"}]
    )
    monkeypatch.setattr(client, "_get", lambda url: pages[url])
    return client


def test_complete_catalogue_counts_urls_not_titles_without_inventing_final_total(
    monkeypatch, anime
):
    client = fixture_client(monkeypatch)
    info = client.fetch(anime, NOW)
    assert info.aired_episodes == 13
    assert info.total_episodes is None and info.airing_status == "unknown"
    assert info.source_kind == "official_catalogue"
    assert info.checked_at == NOW


def test_future_and_same_japanese_day_are_not_counted(monkeypatch, anime):
    info = fixture_client(monkeypatch, future=True).fetch(anime, NOW)
    assert info.aired_episodes == 10


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(
            {LIST_URL + "?page=2": p[LIST_URL + "?page=2"].replace("EP12", "EP0")}
        ),
        lambda p: p.update(
            {
                LIST_URL + "?page=2": p[LIST_URL + "?page=2"].replace(
                    "すべて(13)", "すべて(14)"
                )
            }
        ),
        lambda p: p.update({LIST_URL: p[LIST_URL].replace("2025-01-01", "2025-01-02")}),
        lambda p: p.update(
            {LIST_URL: p[LIST_URL].replace("<time datetime=", "<span datetime=")}
        ),
    ],
)
def test_changed_dates_duplicates_or_incomplete_contract_are_rejected(
    monkeypatch, anime, mutation
):
    with pytest.raises(OfficialSourceUnavailable):
        fixture_client(monkeypatch, mutated=mutation).fetch(anime, NOW)


def test_progress_regression_preserves_prior_value(monkeypatch, anime):
    anime = anime.model_copy(
        update={"episodes": anime.episodes.model_copy(update={"aired_episodes": 14})}
    )
    with pytest.raises(OfficialSourceUnavailable, match="regressed"):
        fixture_client(monkeypatch).fetch(anime, NOW)


@pytest.mark.parametrize(
    "html",
    [
        "<h1>すべて(0)</h1>",
        "<h1>すべて(5000)</h1>",
        "<h1>すべて(13)</h1>",
        "<h1>すべて(13) すべて(14)</h1>",
    ],
)
def test_bad_page_never_becomes_empty_progress(html):
    with pytest.raises(OfficialSourceUnavailable):
        parse_catalogue_page(html)


def test_adapter_only_accepts_the_reviewed_subject(monkeypatch, anime):
    with pytest.raises(OfficialSourceUnavailable, match="identity"):
        fixture_client(monkeypatch).fetch(
            anime.model_copy(update={"original_name": "another work"}), NOW
        )


def test_new_official_page_urls_are_narrowly_allowlisted():
    assert reviewed_source_url(LIST_URL + "?page=16")
    assert not reviewed_source_url(LIST_URL + "?page=41")
    assert not reviewed_source_url(LIST_URL + "?page=16&redirect=https://evil.example")
    assert not reviewed_source_url(
        LIST_URL.replace("zenitendo", "another") + "?page=16"
    )


def test_requests_have_independent_budget_and_reject_redirects():
    session = Mock()
    session.get.return_value.status_code = 302
    client = NHKCatalogueClient(session, request_budget=1)
    with pytest.raises(OfficialSourceUnavailable, match="HTTP 302"):
        client._get(LIST_URL)
    assert session.get.call_args.kwargs["allow_redirects"] is False
    session.get.return_value.close.assert_called_once()
    with pytest.raises(OfficialSourceUnavailable, match="budget"):
        client._get(LIST_URL)
    assert session.get.call_count == 1


def test_enricher_shares_official_fetch_and_retains_failure_time(anime):
    official = Mock()
    official.fetch.side_effect = OfficialSourceUnavailable("offline")
    bg = BangumiClient(request_budget=1)
    enricher = EpisodeEnricher(bg, official_client=official)
    assert automatically_refreshed(anime)
    assert due(anime, NOW)
    result = enricher.enrich(anime, NOW)
    second = enricher.enrich(anime, NOW)
    assert result.episodes == second.episodes
    assert result.episodes.checked_at == anime.episodes.checked_at
    assert result.episodes.aired_episodes == 10
    assert (
        result.episodes.attempted_at == NOW and result.episodes.fetch_state == "stale"
    )
    assert next_refresh_at(result, NOW) == NOW + timedelta(days=1)
    official.fetch.assert_called_once()
    assert bg.remaining == 1


def test_successful_official_verification_remains_cached_for_seven_days(anime):
    fresh = anime.model_copy(
        update={
            "episodes": EpisodeMetadata(
                aired_episodes=148,
                source_kind="official_catalogue",
                fetch_state="ok",
                checked_at=NOW,
                attempted_at=NOW,
            )
        }
    )
    official = Mock()
    assert (
        EpisodeEnricher(official_client=official).enrich(fresh, NOW + timedelta(days=1))
        == fresh
    )
    official.fetch.assert_not_called()
    assert next_refresh_at(fresh, NOW) == NOW + timedelta(days=7)
