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


def test_end_before_premiere_is_not_published_as_a_valid_date(anime_record_factory):
    info = parse_episode_metadata(
        anime(anime_record_factory),
        subject(1, "2025-10-01"),
        [episode(1)],
        NOW,
    )
    assert info.end_date is None
    assert info.aired_episodes == info.total_episodes == 1
    assert info.airing_status == "unknown"
    assert info.episode_count_status == "planned"
    assert "早於首播" in info.note


@pytest.mark.parametrize("marks", ["\u200b", "\ufeff", " \u200b \ufeff\n"])
def test_invisible_boundary_marks_do_not_hide_a_valid_final_episode(
    anime_record_factory, marks
):
    record = anime(anime_record_factory)
    entries = [episode(1, marks + "2026-10-01" + marks)]
    result = parse_episode_metadata(record, subject(1, "2026-10-01"), entries, NOW)
    assert result.aired_episodes == result.total_episodes == 1
    assert result.airing_status == "finished"
    # A valid-looking prefix does not make an annotated date unambiguous.
    entries[0]["airdate"] = "2026-10-01（先行配信）" + marks
    result = parse_episode_metadata(record, subject(1, "2026-10-01"), entries, NOW)
    assert result.aired_episodes is None
    assert result.airing_status == "unknown"


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


@pytest.mark.parametrize("total", [1, 12])
def test_aired_episodes_with_future_subject_premiere_are_conflicting_evidence(
    anime_record_factory, total
):
    info = parse_episode_metadata(
        anime(anime_record_factory),
        {**subject(total), "date": "2027-01-01"},
        [episode(1, "2026-10-01")],
        NOW,
    )
    assert info.fetch_state == "conflict"
    assert info.airing_status == "unknown"
    assert info.total_episodes is None
    assert info.checked_at is None
    assert info.attempted_at == NOW


def test_contradictory_provider_dates_keep_prior_success_instead_of_crashing(
    anime_record_factory,
):
    old = EpisodeMetadata(
        total_episodes=1,
        aired_episodes=1,
        episode_count_status="confirmed",
        airing_status="finished",
        fetch_state="ok",
        checked_at=NOW - timedelta(days=100),
        attempted_at=NOW - timedelta(days=100),
        source_kind="bangumi_api",
    )
    record = anime(anime_record_factory, episodes=old)
    responses = [
        FakeResponse({**subject(1), "date": "2027-01-01"}),
        FakeResponse({"data": [episode(1)], "total": 1}),
    ]
    session = FakeSession(responses)
    client = BangumiClient(session=session, interval_seconds=0)
    updated = EpisodeEnricher(client).enrich(record, NOW)
    assert updated.episodes.fetch_state == "conflict"
    assert updated.episodes.total_episodes == old.total_episodes
    assert updated.episodes.aired_episodes == old.aired_episodes
    assert updated.episodes.checked_at == old.checked_at
    assert updated.episodes.attempted_at == NOW
    assert updated.episodes.airing_status == "unknown"
    assert all(response.closed for response in responses)
    assert len(session.calls) == 2


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


@pytest.mark.parametrize("infobox", [None, {}, "invalid", [None], [1], [{"key": []}]])
def test_bad_infobox_is_a_supplementary_failure(anime_record_factory, infobox):
    with pytest.raises(MetadataUnavailable, match="infobox"):
        parse_episode_metadata(
            anime(anime_record_factory), {**subject(), "infobox": infobox}, [], NOW
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


def test_repeated_episode_page_is_incomplete_evidence(anime_record_factory):
    session = FakeSession(
        [
            FakeResponse(subject()),
            FakeResponse({"data": [episode(1)], "total": 2}),
            FakeResponse({"data": [episode(1)], "total": 2}),
        ]
    )
    with pytest.raises(MetadataUnavailable, match="repeated"):
        BangumiClient(session=session, interval_seconds=0)._fetch_api(
            anime(anime_record_factory), NOW
        )


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


@pytest.mark.parametrize("outcome", ["regressed", "incomplete", "conflict", "offline"])
def test_cross_quarter_cache_preserves_each_records_last_valid_snapshot(
    anime_record_factory, outcome
):
    calls = []

    def fetch(record, now):
        calls.append(record.bangumi_id)
        if outcome == "offline":
            raise MetadataUnavailable("offline")
        return EpisodeMetadata(
            total_episodes=12 if outcome != "conflict" else None,
            aired_episodes=None if outcome == "incomplete" else 6,
            fetch_state="conflict" if outcome == "conflict" else "ok",
            checked_at=now,
            attempted_at=now,
        )

    enricher = EpisodeEnricher(SimpleNamespace(fetch=fetch))
    for count, days in [(8, 3), (10, 2)]:
        old = EpisodeMetadata(
            total_episodes=12,
            aired_episodes=count,
            checked_at=NOW - timedelta(days=days),
            attempted_at=NOW - timedelta(days=days),
        )
        updated = enricher.enrich(anime(anime_record_factory, episodes=old), NOW)
        assert updated.episodes.aired_episodes == count
        assert updated.episodes.total_episodes == 12
        assert updated.episodes.checked_at == old.checked_at
        assert updated.episodes.attempted_at == NOW
        assert updated.episodes.fetch_state == (
            "conflict" if outcome in {"regressed", "conflict"} else "stale"
        )
    assert len(calls) == 1


def test_cross_quarter_cache_does_not_skip_a_different_titles_identity_check(
    anime_record_factory,
):
    calls = []

    def fetch(record, now):
        calls.append(record.original_name)
        return parse_episode_metadata(record, subject(), [episode(1)], now)

    enricher = EpisodeEnricher(SimpleNamespace(fetch=fetch))
    valid = enricher.enrich(anime(anime_record_factory), NOW)
    assert valid.episodes.aired_episodes == 1
    other = enricher.enrich(
        anime(anime_record_factory, original_name="LIAR GAME 第2期"), NOW
    )
    assert other.episodes.fetch_state == "stale"
    assert other.episodes.aired_episodes is None
    assert len(calls) == 2


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
    assert EpisodeEnricher.due(record, NOW + timedelta(days=91))
    recent.end_date = date(2026, 10, 12)
    assert EpisodeEnricher.due(record, NOW)
    assert not EpisodeEnricher.due(anime(anime_record_factory, external_ids={}), NOW)


def test_base_recrawl_retains_metadata_and_rejects_conflicting_identity(
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
    from services.errors import DataContractError

    with pytest.raises(DataContractError, match="Conflicting explicit source IDs"):
        write(anime(anime_record_factory, external_ids={"bangumi": 999}))
    assert (
        repository.load_quarter("2026", "春").anime_list[0].episodes.total_episodes
        == 12
    )


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


def test_exhausted_budget_resumes_overdue_history_before_repeating_new_quarter(
    tmp_path, anime_record_factory, caplog
):
    repository = DataRepository(tmp_path, DataQualityPolicy())
    from services.episode_refresh import refresh_policy

    for year, card, lateness in [(2018, 1, 4), (2026, 2, 2), (2025, 3, 3)]:
        record = anime(
            anime_record_factory,
            bangumi_id=f"anime-{card}",
            episodes=EpisodeMetadata(total_episodes=12, aired_episodes=1),
        )
        delay = refresh_policy(record, NOW, (year, 0)).days + lateness
        repository.write_quarter(
            year=str(year),
            season="春",
            records=[
                record.model_copy(
                    update={
                        "episodes": EpisodeMetadata(
                            total_episodes=12,
                            aired_episodes=1,
                            attempted_at=NOW - timedelta(days=delay),
                            checked_at=NOW - timedelta(days=delay),
                        )
                    }
                ),
            ],
            source_url=f"https://acgsecrets.hk/bangumi/{year}04/",
            source_count=1,
            parse_failure_count=0,
        )
    observed = []
    for day in range(3):
        calls = []

        def fetch(record, now, calls=calls):
            if calls:
                raise RequestBudgetExceeded
            calls.append(record.bangumi_id)
            return EpisodeMetadata(
                total_episodes=12,
                aired_episodes=2,
                attempted_at=now,
                checked_at=now,
            )

        refresh_repository_episodes(
            repository,
            NOW + timedelta(days=day),
            EpisodeEnricher(SimpleNamespace(fetch=fetch)),
        )
        observed.extend(calls)
    assert observed == ["anime-1", "anime-3", "anime-2"]
    assert "identities deferred" in caplog.text
    for year in (2018, 2025, 2026):
        assert (
            repository.load_quarter(str(year), "春")
            .anime_list[0]
            .episodes.aired_episodes
            == 2
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"anime_name": "另一部作品"},
        {"original_name": "ANOTHER ANIME"},
        {"anime_name": "另一部作品", "external_ids": {}},
    ],
)
def test_recrawl_cannot_transfer_old_metadata_to_changed_card_titles(
    tmp_path, anime_record_factory, updates
):
    repository = DataRepository(tmp_path, DataQualityPolicy())
    old = anime(
        anime_record_factory,
        episodes=EpisodeMetadata(total_episodes=12, aired_episodes=8),
    )
    for record in [old, anime(anime_record_factory, **updates)]:
        repository.write_quarter(
            year="2026",
            season="春",
            records=[record],
            source_url="https://acgsecrets.hk/bangumi/202604/",
            source_count=1,
            parse_failure_count=0,
        )
    result = repository.load_quarter("2026", "春").anime_list[0]
    assert result.episodes is None
    if "external_ids" in updates:
        assert result.external_ids.bangumi is None


def test_backfill_links_preserves_existing_records_and_images(
    tmp_path, anime_record_factory, fixture_dir
):
    repository = DataRepository(tmp_path, DataQualityPolicy())
    records = [
        Anime.model_validate(anime_record_factory(2200, name="測試動畫")),
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


@pytest.mark.parametrize(
    ("args", "wrong_id", "correct_id"),
    [
        (("anime-371", "魔法禁書目錄III", "とある魔術の禁書目録III"), 22654, 226540),
        (
            ("anime-1292", "The Marginal Service", "THE MARGINAL SERVICE"),
            357961,
            407659,
        ),
    ],
)
def test_reviewed_source_id_correction_is_exact_and_does_not_overwrite_new_links(
    args, wrong_id, correct_id
):
    from services.episode_metadata import reviewed_source_subject_id

    assert reviewed_source_subject_id(*args, wrong_id) == correct_id
    assert reviewed_source_subject_id(*args, correct_id) == correct_id
    assert reviewed_source_subject_id(*args, 999) == 999
    assert reviewed_source_subject_id("anime-999", *args[1:], wrong_id) == wrong_id
    assert (
        reviewed_source_subject_id(args[0], "別的作品", args[2], wrong_id) == wrong_id
    )
    assert (
        reviewed_source_subject_id(args[0], args[1], "another season", wrong_id)
        == wrong_id
    )


@pytest.mark.parametrize(
    "problem",
    [
        "renamed",
        "original_changed",
        "bangumi_conflict",
        "anilist_conflict",
        "duplicate",
    ],
)
def test_link_backfill_rejects_ambiguous_identity(
    tmp_path, anime_record_factory, fixture_dir, problem
):
    repository = DataRepository(tmp_path, DataQualityPolicy())
    record = Anime.model_validate(
        {
            **anime_record_factory(2200, name="測試動畫"),
            "external_ids": {
                "bangumi": 580133,
                "anilist": 197754 if problem == "anilist_conflict" else None,
            },
            "episodes": {"total_episodes": 12},
        }
    )
    repository.write_quarter(
        year="2026",
        season="夏",
        records=[record],
        source_url="https://acgsecrets.hk/bangumi/202607/",
        source_count=1,
        parse_failure_count=0,
    )
    html = (fixture_dir / "acgsecrets_202607_minimal.html").read_text()
    html = html.replace(
        '<div class="anime_story">',
        '<a href="https://bgm.tv/subject/580133">BGM</a><a href="https://anilist.co/anime/197754">AniList</a><div class="anime_story">',
    )
    if problem == "renamed":
        html = html.replace("測試動畫", "其他作品")
    elif problem == "original_changed":
        record = record.model_copy(update={"original_name": "TV 第2期"})
        repository.write_quarter(
            year="2026",
            season="夏",
            records=[record],
            source_url="https://acgsecrets.hk/bangumi/202607/",
            source_count=1,
            parse_failure_count=0,
        )
    elif problem == "bangumi_conflict":
        html = html.replace("subject/580133", "subject/999")
    elif problem == "anilist_conflict":
        html = html.replace("anime/197754", "anime/999")
    else:
        html += html
    source = SimpleNamespace(fetch_quarter_html=lambda *args: ("url", html))
    assert backfill_source_links(repository, source, year="2026") == 0
    assert repository.load_quarter("2026", "夏").anime_list == [record]


def test_reviewed_parenthesized_sequel_is_not_base_or_another_version(
    anime_record_factory,
):
    from services.episode_metadata import subject_identity_matches

    record = anime(
        anime_record_factory,
        bangumi_id="anime-1659",
        anime_name="放學後少年花子君 續篇",
        original_name="放課後少年花子くん (続編)",
        external_ids={"bangumi": 464594},
    )
    provider = {
        "id": 464594,
        "type": 2,
        "name": "放課後少年花子くん 続編",
        "date": "2024-10-07",
    }
    assert subject_identity_matches(record, provider)
    for key, value in [
        ("name", "放課後少年花子くん"),
        ("name", "放課後少年花子くん 続編＋"),
        ("date", "2023-10-12"),
        ("id", 999),
    ]:
        assert not subject_identity_matches(record, {**provider, key: value})
    assert not subject_identity_matches(
        record.model_copy(update={"bangumi_id": "anime-999"}), provider
    )


def test_reviewed_quote_formatting_keeps_plus_version_boundary(anime_record_factory):
    from services.episode_metadata import subject_identity_matches

    record = anime(
        anime_record_factory,
        bangumi_id="anime-1401",
        anime_name="催眠麥克風 -Division Rap Battle- Rhyme Anima +",
        original_name="ヒプノシスマイク-Division Rap Battle- Rhyme Anima +",
        external_ids={"bangumi": 426049},
    )
    provider = {
        "id": 426049,
        "type": 2,
        "name": "『ヒプノシスマイク-Division Rap Battle-』Rhyme Anima ＋",
        "date": "2023-10-06",
    }
    assert subject_identity_matches(record, provider)
    assert not subject_identity_matches(
        record,
        {**provider, "name": "『ヒプノシスマイク-Division Rap Battle-』Rhyme Anima"},
    )
    assert not subject_identity_matches(
        record.model_copy(
            update={
                "original_name": "ヒプノシスマイク-Division Rap Battle- Rhyme Anima"
            }
        ),
        provider,
    )
    assert not subject_identity_matches(record, {**provider, "date": "2020-10-02"})


def test_reviewed_diamond_formatting_cannot_cross_cour_or_authorize_summary(
    anime_record_factory,
):
    from services.episode_metadata import subject_identity_matches
    from services.story_metadata import enrich_story

    record = anime(
        anime_record_factory,
        bangumi_id="anime-2329",
        anime_name="鑽石王牌 actⅡ第2季 2nd cour",
        original_name="ダイヤのA actII -Second Season- 第2クール",
        external_ids={"bangumi": 664395, "anilist": 213658},
        story="暫無簡介",
    )
    subject = {
        "id": 664395,
        "type": 2,
        "name": "ダイヤのA actⅡ Second Season 第2クール",
        "date": "2026-10-11",
        "summary": "A provider summary cannot authorize an unreviewed official part scope.",
    }
    assert subject_identity_matches(record, subject)
    for change in (
        {"name": "ダイヤのA actⅡ Second Season"},
        {"name": "ダイヤのA actⅡ Second Season 第1クール"},
        {"date": "2026-04-05"},
        {"id": 123},
    ):
        assert not subject_identity_matches(record, {**subject, **change})
    assert not subject_identity_matches(
        record.model_copy(update={"bangumi_id": "anime-999"}), subject
    )
    assert enrich_story(record, subject, NOW, method="bangumi_api") == record


def test_first_conflicting_evidence_does_not_claim_success(anime_record_factory):
    data = subject(12)
    data["infobox"] = [{"key": "话数", "value": "13"}]
    result = parse_episode_metadata(
        anime(anime_record_factory), data, [episode(1)], NOW
    )
    assert result.fetch_state == "conflict"
    assert result.checked_at is None
    assert result.attempted_at == NOW


@pytest.mark.parametrize("had_success", [False, True])
def test_conflict_keeps_success_history_until_real_recovery(
    anime_record_factory, had_success
):
    old = (
        EpisodeMetadata(
            total_episodes=12,
            aired_episodes=8,
            airing_status="airing",
            episode_count_status="planned",
            fetch_state="ok",
            checked_at=NOW - timedelta(days=2),
            attempted_at=NOW - timedelta(days=2),
        )
        if had_success
        else None
    )
    record = anime(anime_record_factory, episodes=old)
    conflict = subject(12)
    conflict["infobox"] = [{"key": "话数", "value": "13"}]
    client = SimpleNamespace(
        fetch=lambda a, now: parse_episode_metadata(a, conflict, [episode(1)], now)
    )
    failed = EpisodeEnricher(client).enrich(record, NOW)
    assert failed.episodes.fetch_state == "conflict"
    assert failed.episodes.checked_at == (old.checked_at if old else None)
    assert failed.episodes.attempted_at == NOW
    if old:
        assert failed.episodes.total_episodes == old.total_episodes
        assert failed.episodes.aired_episodes == old.aired_episodes
    recovered_at = NOW + timedelta(days=1)
    good_client = SimpleNamespace(
        fetch=lambda a, now: parse_episode_metadata(
            a, subject(12), [episode(n) for n in range(1, 9)], now
        )
    )
    recovered = EpisodeEnricher(good_client, retry_failed=True).enrich(
        failed, recovered_at
    )
    assert recovered.episodes.fetch_state == "ok"
    assert recovered.episodes.checked_at == recovered_at
    assert recovered.episodes.attempted_at == recovered_at
