from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
import requests
from pydantic import ValidationError

from models import TAIPEI_TZ, Anime, EpisodeMetadata, ExternalIds
from services.data_repository import DataQualityPolicy, DataRepository
from services.episode_metadata import (
    BangumiClient,
    EpisodeEnricher,
    MetadataUnavailable,
    RequestBudgetExceeded,
    parse_episode_metadata,
    refresh_repository_episodes,
)
from services.parser import parse_anime_item
from update_episode_metadata import backfill_source_links

NOW = datetime(2026, 10, 13, 18, tzinfo=TAIPEI_TZ)


def anime(factory, **updates):
    return Anime.model_validate(
        {
            **factory(2142, name="LIAR GAME"),
            "original_name": "LIAR GAME",
            "external_ids": {"bangumi": 580133, "anilist": 197754},
            **updates,
        }
    )


def subject(total=12, end=None):
    return {
        "id": 580133,
        "type": 2,
        "name": "LIAR GAME",
        "name_cn": "欺诈游戏",
        "date": "2026-04-06",
        "eps": total,
        "total_episodes": 999,
        "infobox": [{"key": "播放结束", "value": end}] if end else [],
    }


def episode(number, airdate="2026-10-01", **updates):
    return {
        "id": 1000 + number,
        "subject_id": 580133,
        "type": 0,
        "sort": number,
        "airdate": airdate,
        **updates,
    }


def test_eight_of_twelve_is_airing_not_finished(anime_record_factory):
    entries = [episode(n) for n in range(1, 9)] + [
        episode(n, "2026-10-20") for n in range(9, 13)
    ]
    info = parse_episode_metadata(anime(anime_record_factory), subject(), entries, NOW)
    assert (info.aired_episodes, info.total_episodes) == (8, 12)
    assert info.airing_status == "airing"
    assert info.episode_count_status == "planned"


def test_database_entry_count_is_not_total_and_no_weekly_estimate(anime_record_factory):
    info = parse_episode_metadata(
        anime(anime_record_factory), subject(0), [episode(8)], NOW
    )
    assert info.aired_episodes == 1  # count actual entries, never max episode number
    assert info.total_episodes is None
    assert info.episode_count_status == "unknown"


def test_same_day_and_future_episodes_are_not_counted(anime_record_factory):
    info = parse_episode_metadata(
        anime(anime_record_factory),
        subject(),
        [episode(1, "2026-10-12"), episode(2, "2026-10-13"), episode(3, "2026-10-14")],
        NOW,
    )
    assert info.aired_episodes == 1


def test_specials_fractional_recaps_and_duplicate_entries_are_excluded(
    anime_record_factory,
):
    info = parse_episode_metadata(
        anime(anime_record_factory),
        subject(),
        [episode(1), episode(1), episode(2, type=1), episode(3, sort=1.5)],
        NOW,
    )
    assert info.aired_episodes == 1


def test_finished_requires_explicit_past_end_and_complete_dates(anime_record_factory):
    record = anime(anime_record_factory)
    entries = [episode(n) for n in range(1, 13)]
    no_end = parse_episode_metadata(record, subject(), entries, NOW)
    assert no_end.airing_status == "unknown"
    finished = parse_episode_metadata(
        record, subject(end="2026年10月1日"), entries, NOW
    )
    assert finished.airing_status == "finished"
    assert finished.episode_count_status == "confirmed"
    assert finished.end_date == date(2026, 10, 1)
    entries[-1]["airdate"] = ""
    missing = parse_episode_metadata(record, subject(end="2026-10-01"), entries, NOW)
    assert missing.airing_status != "finished"
    assert missing.aired_episodes is None


@pytest.mark.parametrize(
    "mutation",
    [
        {"type": 1},
        {"id": 1651},
        {"name": "LIAR GAME 第2期", "name_cn": "第二季"},
    ],
)
def test_rejects_wrong_media_or_season(anime_record_factory, mutation):
    with pytest.raises(MetadataUnavailable):
        parse_episode_metadata(
            anime(anime_record_factory), {**subject(), **mutation}, [], NOW
        )


def test_unknown_dates_and_future_start(anime_record_factory):
    record = anime(anime_record_factory)
    info = parse_episode_metadata(record, subject(), [episode(1, "未定")], NOW)
    assert info.aired_episodes is None
    assert info.airing_status == "unknown"
    info = parse_episode_metadata(record, {**subject(), "date": "2027-01-01"}, [], NOW)
    assert info.airing_status == "not_yet_aired"
    assert info.aired_episodes == 0


def test_conflicting_count_is_not_silently_replaced(anime_record_factory):
    data = subject(12)
    data["infobox"] = [{"key": "话数", "value": "13"}]
    info = parse_episode_metadata(anime(anime_record_factory), data, [episode(1)], NOW)
    assert info.fetch_state == "conflict"
    assert info.total_episodes is None
    info = parse_episode_metadata(
        anime(anime_record_factory),
        subject(1),
        [episode(1), episode(2)],
        NOW,
    )
    assert info.fetch_state == "conflict"


def test_missing_episode_in_sequence_is_not_an_exact_progress_count(
    anime_record_factory,
):
    entries = [episode(number) for number in (1, 2, 4, 5)]
    info = parse_episode_metadata(
        anime(anime_record_factory), subject(4, "2026-10-01"), entries, NOW
    )
    assert info.fetch_state == "conflict"
    assert info.aired_episodes is None
    assert info.airing_status != "finished"


def test_bad_infobox_is_a_supplementary_failure(anime_record_factory):
    with pytest.raises(MetadataUnavailable, match="infobox"):
        parse_episode_metadata(
            anime(anime_record_factory), {**subject(), "infobox": None}, [], NOW
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"total_episodes": True},
        {"total_episodes": 0},
        {"aired_episodes": -1},
        {"aired_episodes": 13, "total_episodes": 12},
        {"airing_status": "finished"},
        {"checked_at": datetime(2026, 10, 13)},
        {"source_urls": ["https://evil.example/subject/1"]},
        {"episode_count_status": "confirmed"},
        {"airing_status": "not_yet_aired", "aired_episodes": 1},
    ],
)
def test_metadata_contract_rejects_inconsistent_values(updates):
    with pytest.raises(ValidationError):
        EpisodeMetadata(**updates)


def test_source_parser_extracts_real_database_links(fixture_dir):
    html = (fixture_dir / "acgsecrets_202607_minimal.html").read_text()
    html = html.replace(
        '<div class="anime_story">',
        """
      <div class="entity_original_name">Japanese title</div>
      <div class="anime_link_group">
        <a href="https://bangumi.tv/subject/580133">Bangumi</a>
        <a href="https://anilist.co/anime/197754/">AniList</a>
        <a href="https://evil.example/subject/9">Ignore</a>
      </div><div class="anime_story">""",
    )
    candidate = parse_anime_item(html)
    assert candidate.bangumi_id == "anime-2200"
    assert candidate.external_ids == ExternalIds(bangumi=580133, anilist=197754)
    assert candidate.original_name == "Japanese title"


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status
        self.content = b"{}"
        self.closed = False

    def json(self):
        return self.payload

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def test_client_paginates_and_closes_responses(anime_record_factory):
    responses = [
        FakeResponse(subject()),
        FakeResponse({"data": [episode(1)], "total": 2}),
        FakeResponse({"data": [episode(2)], "total": 2}),
    ]
    session = FakeSession(responses)
    client = BangumiClient(session=session, interval_seconds=0)
    assert client.fetch(anime(anime_record_factory), NOW).aired_episodes == 2
    assert session.calls[-1][1]["params"]["offset"] == 1
    assert all(response.closed for response in responses)


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse({}, 429),
        FakeResponse({}, 503),
        requests.Timeout(),
    ],
)
def test_provider_failure_stops_further_requests(response):
    session = FakeSession([response])
    client = BangumiClient(session=session, interval_seconds=0)
    for _ in range(2):
        with pytest.raises(MetadataUnavailable):
            client._get("/subjects/1")
    assert len(session.calls) == 1


def test_budget_and_broken_pagination_are_bounded(anime_record_factory):
    session = FakeSession(
        [FakeResponse(subject()), FakeResponse({"data": [], "total": 2})]
    )
    client = BangumiClient(session=session, interval_seconds=0)
    with pytest.raises(MetadataUnavailable, match="ended early"):
        client._fetch_api(anime(anime_record_factory), NOW)
    with pytest.raises(RequestBudgetExceeded):
        BangumiClient(request_budget=0)._get("/subjects/1")


def test_failure_retains_last_valid_values_and_timestamp(anime_record_factory):
    old = EpisodeMetadata(
        total_episodes=12,
        aired_episodes=8,
        airing_status="airing",
        episode_count_status="planned",
        fetch_state="ok",
        checked_at=NOW - timedelta(days=2),
        attempted_at=NOW - timedelta(days=2),
    )
    record = anime(anime_record_factory, episodes=old)

    def fail(*args):
        raise MetadataUnavailable("offline")

    enricher = EpisodeEnricher(SimpleNamespace(fetch=fail))
    updated = enricher.enrich(record, NOW)
    assert updated.episodes.aired_episodes == 8
    assert updated.episodes.total_episodes == 12
    assert updated.episodes.checked_at == old.checked_at
    assert updated.episodes.fetch_state == "stale"
    assert updated.episodes.attempted_at == NOW


@pytest.mark.parametrize("missing", ["total_episodes", "aired_episodes"])
def test_incomplete_refresh_keeps_previously_confirmed_values(
    anime_record_factory, missing
):
    old = EpisodeMetadata(
        total_episodes=12,
        aired_episodes=8,
        airing_status="airing",
        episode_count_status="planned",
        fetch_state="ok",
        checked_at=NOW - timedelta(days=2),
        attempted_at=NOW - timedelta(days=2),
    )
    fields = {**old.model_dump(), missing: None, "checked_at": NOW, "attempted_at": NOW}
    if missing == "total_episodes":
        fields["episode_count_status"] = "unknown"
    partial = EpisodeMetadata(**fields)
    record = anime(anime_record_factory, episodes=old)
    enricher = EpisodeEnricher(SimpleNamespace(fetch=lambda *args: partial))
    updated = enricher.enrich(record, NOW).episodes
    assert (updated.total_episodes, updated.aired_episodes) == (12, 8)
    assert updated.checked_at == old.checked_at
    assert updated.fetch_state == "stale"


def test_regressed_progress_is_marked_for_review(anime_record_factory):
    old = EpisodeMetadata(
        total_episodes=12,
        aired_episodes=8,
        airing_status="airing",
        episode_count_status="planned",
        fetch_state="ok",
        checked_at=NOW - timedelta(days=2),
        attempted_at=NOW - timedelta(days=2),
    )
    revised = EpisodeMetadata(
        **{
            **old.model_dump(),
            "aired_episodes": 6,
            "checked_at": NOW,
            "attempted_at": NOW,
        }
    )
    enricher = EpisodeEnricher(SimpleNamespace(fetch=lambda *args: revised))
    updated = enricher.enrich(anime(anime_record_factory, episodes=old), NOW).episodes
    assert updated.aired_episodes == 8
    assert updated.airing_status == "unknown"
    assert updated.fetch_state == "conflict"


def test_cache_and_completed_refresh_intervals(anime_record_factory):
    recent = EpisodeMetadata(
        total_episodes=12,
        aired_episodes=12,
        airing_status="finished",
        episode_count_status="confirmed",
        fetch_state="ok",
        end_date=date(2026, 10, 1),
        checked_at=NOW - timedelta(days=2),
        attempted_at=NOW - timedelta(days=2),
    )
    record = anime(anime_record_factory, episodes=recent)
    assert not EpisodeEnricher.due(record, NOW)
    assert EpisodeEnricher.due(record, NOW + timedelta(days=31))
    recent.end_date = date(2026, 10, 12)
    assert EpisodeEnricher.due(record, NOW)
    assert not EpisodeEnricher.due(anime(anime_record_factory, external_ids={}), NOW)


def test_base_recrawl_retains_metadata_but_new_identity_resets_it(
    tmp_path, anime_record_factory
):
    repository = DataRepository(tmp_path, DataQualityPolicy())
    record = anime(
        anime_record_factory, episodes={"total_episodes": 12, "aired_episodes": 8}
    )

    def write(value):
        return repository.write_quarter(
            year="2026",
            season="春",
            records=[value],
            source_url="https://acgsecrets.hk/bangumi/202604/",
            source_count=1,
            parse_failure_count=0,
        )

    write(record)
    write(anime(anime_record_factory))
    assert (
        repository.load_quarter("2026", "春").anime_list[0].episodes.total_episodes
        == 12
    )
    write(anime(anime_record_factory, external_ids={"bangumi": 999}))
    assert repository.load_quarter("2026", "春").anime_list[0].episodes is None


def test_old_linked_anime_outside_crawl_window_refreshes_once(
    tmp_path, anime_record_factory
):
    repository = DataRepository(tmp_path, DataQualityPolicy())
    for year in (2018, 2026):
        repository.write_quarter(
            year=str(year),
            season="春",
            records=[anime(anime_record_factory)],
            source_url=f"https://acgsecrets.hk/bangumi/{year}04/",
            source_count=1,
            parse_failure_count=0,
        )
    calls = []
    info = EpisodeMetadata(
        total_episodes=12, aired_episodes=8, checked_at=NOW, attempted_at=NOW
    )

    def fetch(record, now):
        calls.append(record.bangumi_id)
        return info

    changed = refresh_repository_episodes(
        repository, NOW, EpisodeEnricher(SimpleNamespace(fetch=fetch))
    )
    assert changed == {"2018_春.json", "2026_春.json"}
    assert len(calls) == 1
    assert (
        repository.load_quarter("2018", "春").anime_list[0].episodes.aired_episodes == 8
    )


def test_backfill_links_preserves_existing_records_and_images(
    tmp_path, anime_record_factory, fixture_dir
):
    repository = DataRepository(tmp_path, DataQualityPolicy())
    records = [
        Anime.model_validate(anime_record_factory(2200)),
        Anime.model_validate(anime_record_factory(999)),
    ]
    repository.write_quarter(
        year="2026",
        season="夏",
        records=records,
        source_url="https://acgsecrets.hk/bangumi/202607/",
        source_count=2,
        parse_failure_count=0,
    )
    html = (
        (fixture_dir / "acgsecrets_202607_minimal.html")
        .read_text()
        .replace(
            '<div class="anime_story">',
            '<div class="anime_link_group"><a href="https://bgm.tv/subject/580133">BGM</a></div><div class="anime_story">',
        )
    )
    source = SimpleNamespace(fetch_quarter_html=lambda *args: ("url", html))
    backfill_source_links(repository, source, year="2026")
    updated = repository.load_quarter("2026", "夏").anime_list
    assert len(updated) == 2
    assert updated[0].external_ids.bangumi == 580133
    assert [record.anime_image_url for record in updated] == [
        record.anime_image_url for record in records
    ]


def test_backfill_missing_links_keeps_known_identity_and_metadata(
    tmp_path, anime_record_factory, fixture_dir
):
    repository = DataRepository(tmp_path, DataQualityPolicy())
    record = anime(
        anime_record_factory, episodes={"total_episodes": 12, "aired_episodes": 8}
    ).model_copy(update={"bangumi_id": "anime-2200"})
    repository.write_quarter(
        year="2026",
        season="夏",
        records=[record],
        source_url="https://acgsecrets.hk/bangumi/202607/",
        source_count=1,
        parse_failure_count=0,
    )
    html = (fixture_dir / "acgsecrets_202607_minimal.html").read_text()
    source = SimpleNamespace(fetch_quarter_html=lambda *args: ("url", html))
    backfill_source_links(repository, source, year="2026")
    updated = repository.load_quarter("2026", "夏").anime_list[0]
    assert updated.external_ids == record.external_ids
    assert updated.original_name == record.original_name
    assert updated.episodes == record.episodes


def test_manual_retry_failed_does_not_refresh_successful_cache(anime_record_factory):
    record = anime(
        anime_record_factory,
        episodes={
            "total_episodes": 12,
            "aired_episodes": 8,
            "fetch_state": "stale",
            "attempted_at": NOW,
            "checked_at": NOW,
        },
    )
    calls = []

    def fetch(record, now):
        calls.append(record.bangumi_id)
        return record.episodes.model_copy(update={"fetch_state": "ok"})

    client = SimpleNamespace(fetch=fetch)
    assert EpisodeEnricher(client).enrich(record, NOW).episodes.fetch_state == "stale"
    assert not calls
    updated = EpisodeEnricher(client, retry_failed=True).enrich(record, NOW)
    assert updated.episodes.fetch_state == "ok"
    assert calls == [record.bangumi_id]
    EpisodeEnricher(client, retry_failed=True).enrich(updated, NOW)
    assert len(calls) == 1


def test_reviewed_identity_accepts_only_the_exact_card_title_id_name_and_date(
    anime_record_factory,
):
    from services.episode_metadata import subject_identity_matches

    record = anime(
        anime_record_factory,
        bangumi_id="anime-295",
        anime_name="生肖奇緣",
        original_name="フルーツバスケット",
        external_ids={"bangumi": 266455},
    )
    provider = {
        **subject(),
        "id": 266455,
        "name": "フルーツバスケット 1st season",
        "name_cn": "",
        "date": "2019-04-05",
    }
    assert subject_identity_matches(record, provider)
    assert parse_episode_metadata(record, provider, [], NOW).fetch_state == "ok"
    for update in [
        {"bangumi_id": "anime-999"},
        {"anime_name": "生肖奇緣 第二季"},
        {"original_name": "フルーツバスケット 第2期"},
    ]:
        assert not subject_identity_matches(record.model_copy(update=update), provider)
    for update in [
        {"id": 999},
        {"type": 1},
        {"name": "フルーツバスケット 2nd season"},
        {"date": "2020-04-05"},
    ]:
        assert not subject_identity_matches(record, {**provider, **update})


def test_reviewed_identity_does_not_approve_different_anime_or_ambiguous_part(
    anime_record_factory,
):
    from services.episode_metadata import subject_identity_matches

    wrong = anime(
        anime_record_factory,
        bangumi_id="anime-1750",
        anime_name="暗芝居 第14季",
        original_name="闇芝居 十四期",
        external_ids={"bangumi": 486347},
    )
    assert not subject_identity_matches(
        wrong,
        {
            **subject(),
            "id": 486347,
            "name": "薬屋のひとりごと 第2期",
            "date": "2025-01-10",
        },
    )
    broad = anime(
        anime_record_factory,
        bangumi_id="anime-1631",
        anime_name="Re:從零開始的異世界生活 第3季",
        original_name="Re：ゼロから始める異世界生活 3rd Season",
        external_ids={"bangumi": 425998},
    )
    assert not subject_identity_matches(
        broad,
        {
            **subject(),
            "id": 425998,
            "name": "Re:ゼロから始める異世界生活 3rd season 襲擊編",
            "date": "2024-10-02",
        },
    )
