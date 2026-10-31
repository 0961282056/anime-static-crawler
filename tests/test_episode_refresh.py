from datetime import timedelta

import pytest

from models import EpisodeMetadata
from services.data_repository import DataQualityPolicy, DataRepository
from services.episode_metadata import (
    BangumiClient,
    EpisodeEnricher,
    RequestBudgetExceeded,
    refresh_repository_episodes,
)
from services.episode_refresh import (
    due,
    identity,
    next_refresh_at,
    quarter_context,
    refresh_backlog,
    refresh_policy,
    refresh_statistics,
)
from tests.test_episode_metadata import NOW, anime


def test_backlog_tracks_exact_identities_and_clears_only_after_all_copies_refresh(
    anime_record_factory,
):
    old = anime(
        anime_record_factory,
        episodes=EpisodeMetadata(
            airing_status="airing",
            fetch_state="ok",
            checked_at=NOW - timedelta(days=2),
            attempted_at=NOW - timedelta(days=2),
        ),
    )
    rows = [("2026_夏", old), ("2026_秋", old)]
    before = old.model_dump(mode="json")
    report = refresh_backlog(rows, NOW)
    assert report["pending_count"] == report["priority_count"] == 1
    item = report["items"][0]
    assert item["quarters"] == ["2026_夏", "2026_秋"]
    assert item["checked_at"] == old.episodes.checked_at.isoformat()
    assert item["attempted_at"] == old.episodes.attempted_at.isoformat()
    assert item["subject_id"] == old.external_ids.bangumi
    assert item["reasons"] == ["due"]
    assert old.model_dump(mode="json") == before
    fresh = old.model_copy(
        update={
            "episodes": old.episodes.model_copy(
                update={
                    "checked_at": NOW,
                    "attempted_at": NOW,
                }
            )
        }
    )
    assert (
        refresh_backlog([("2026_夏", old), ("2026_秋", fresh)], NOW)["pending_count"]
        == 1
    )
    assert (
        refresh_backlog([("2026_夏", fresh), ("2026_秋", fresh)], NOW)["pending_count"]
        == 0
    )


def test_backlog_keeps_failed_provider_work_even_before_retry_is_due(
    anime_record_factory,
):
    old = anime(
        anime_record_factory,
        episodes=EpisodeMetadata(
            airing_status="finished",
            fetch_state="stale",
            total_episodes=12,
            aired_episodes=12,
            episode_count_status="confirmed",
            checked_at=NOW - timedelta(days=100),
            attempted_at=NOW,
        ),
    )
    report = refresh_backlog([("2018_春", old)], NOW)
    assert not due(old, NOW, (2018, 1))
    assert report["pending_count"] == report["background_count"] == 1
    assert report["items"][0]["reasons"] == ["previous_attempt_failed"]
    assert (
        report["items"][0]["next_refresh_at"] == (NOW + timedelta(days=7)).isoformat()
    )


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        ("2026-10-24T04:16:51+08:00", "2026-10-25T03:22:36+08:00", True),
        ("2026-10-24T04:16:51+08:00", "2026-10-24T22:59:00+08:00", False),
        ("2026-10-24T23:30:00+09:00", "2026-10-24T23:45:00+08:00", True),
        ("2026-10-24T16:30:00+00:00", "2026-10-24T23:45:00+08:00", False),
    ],
)
def test_daily_refresh_follows_japanese_dates_despite_schedule_jitter(
    anime_record_factory, previous, current, expected
):
    from datetime import datetime

    record = anime(
        anime_record_factory,
        episodes=EpisodeMetadata(
            airing_status="airing",
            fetch_state="ok",
            attempted_at=datetime.fromisoformat(previous),
        ),
    )
    assert due(record, datetime.fromisoformat(current), (2026, 3)) is expected


def test_historical_unknown_uses_latest_quarter_and_stable_bounded_cadence(
    anime_record_factory,
):
    record = anime(anime_record_factory, episodes=EpisodeMetadata(fetch_state="ok"))
    historical = refresh_policy(record, NOW, (2018, 0))
    assert not historical.priority
    assert 15 <= historical.days <= 30
    assert historical == refresh_policy(record, NOW + timedelta(days=1), (2018, 0))
    context = quarter_context([("2018_冬", record), ("2026_秋", record)])
    assert refresh_policy(record, NOW, context[identity(record)]).days == 1
    assert refresh_policy(record, NOW, context[identity(record)]).priority


def test_completed_history_is_staggered_and_failures_retry_sooner(anime_record_factory):
    info = EpisodeMetadata(
        total_episodes=12,
        aired_episodes=12,
        airing_status="finished",
        episode_count_status="confirmed",
        fetch_state="ok",
    )
    record = anime(anime_record_factory, episodes=info)
    policy = refresh_policy(record, NOW, (2018, 0))
    assert not policy.priority
    assert 60 <= policy.days <= 90
    failed = record.model_copy(
        update={"episodes": info.model_copy(update={"fetch_state": "stale"})}
    )
    assert refresh_policy(failed, NOW, (2018, 0)).days == 7
    recent = record.model_copy(
        update={
            "episodes": info.model_copy(
                update={"end_date": NOW.date() - timedelta(days=2)}
            )
        }
    )
    assert refresh_policy(recent, NOW, (2018, 0)).priority


def test_old_quarter_long_running_anime_remains_daily_priority(anime_record_factory):
    for status in ("airing", "not_yet_aired"):
        record = anime(
            anime_record_factory, episodes=EpisodeMetadata(airing_status=status)
        )
        policy = refresh_policy(record, NOW, (2018, 0))
        assert policy.days == 1 and policy.priority


def test_bulk_completed_verification_spreads_first_cycle_and_then_keeps_ninety_days(
    anime_record_factory,
):
    dates = set()
    for number in range(360):
        record = anime(
            anime_record_factory,
            bangumi_id=f"anime-{number + 1}",
            episodes=EpisodeMetadata(
                total_episodes=12,
                aired_episodes=12,
                airing_status="finished",
                episode_count_status="confirmed",
                fetch_state="ok",
                attempted_at=NOW,
            ),
        )
        first = next_refresh_at(record, NOW, (2018, 0))
        assert NOW < first <= NOW + timedelta(days=90)
        dates.add(first.date())
        updated = record.model_copy(
            update={
                "episodes": record.episodes.model_copy(update={"attempted_at": first})
            }
        )
        assert next_refresh_at(updated, first, (2018, 0)) == first + timedelta(days=90)
    assert len(dates) > 75


def test_health_uses_oldest_verification_not_successful_attempt_time(
    anime_record_factory,
):
    record = anime(
        anime_record_factory,
        episodes=EpisodeMetadata(
            airing_status="airing",
            fetch_state="ok",
            checked_at=NOW - timedelta(days=3),
            attempted_at=NOW,
        ),
    )
    newer = record.model_copy(
        update={"episodes": record.episodes.model_copy(update={"checked_at": NOW})}
    )
    health = refresh_statistics([("2026_秋", newer), ("2018_春", record)], NOW)
    assert health["priority_identities"] == 1
    assert health["priority_older_than_48h"] == 1
    assert health["priority_overdue"] == 0
    assert (
        health["priority_oldest_checked_at"] == record.episodes.checked_at.isoformat()
    )


class CapacityClient(BangumiClient):
    def __init__(self):
        super().__init__(request_budget=12, interval_seconds=0)
        self.calls = []
        self.initial_deadline = self.deadline

    def fetch(self, record, now):
        if self.remaining < 2:
            raise RequestBudgetExceeded
        self.remaining -= 2
        self.calls.append((record.bangumi_id, self.deadline))
        return record.episodes.model_copy(
            update={"attempted_at": now, "checked_at": now}
        )


def setup_capacity_repository(tmp_path, factory, background_due=True):
    repository = DataRepository(tmp_path, DataQualityPolicy())
    for year, season, count, status in [
        (2026, "秋", 6, "airing"),
        (2018, "春", 3, "unknown"),
    ]:
        records = []
        for i in range(count):
            age = 100 if year == 2018 and background_due else 2 if year == 2026 else 0
            records.append(
                anime(
                    factory,
                    bangumi_id=f"anime-{year}{i}",
                    anime_name=f"work {year}-{i}",
                    episodes=EpisodeMetadata(
                        airing_status=status,
                        checked_at=NOW - timedelta(days=age),
                        attempted_at=NOW - timedelta(days=age),
                    ),
                )
            )
        repository.write_quarter(
            year=str(year),
            season=season,
            records=records,
            source_url=f"https://acgsecrets.hk/bangumi/{year}{'10' if year == 2026 else '04'}/",
            source_count=count,
            parse_failure_count=0,
        )
    return repository


def test_daily_work_preserves_request_and_time_capacity_for_history(
    tmp_path, anime_record_factory
):
    repository = setup_capacity_repository(tmp_path, anime_record_factory)
    client = CapacityClient()
    refresh_repository_episodes(repository, NOW, EpisodeEnricher(client))
    priority = [
        (card, deadline)
        for card, deadline in client.calls
        if card.startswith("anime-2026")
    ]
    background = [
        (card, deadline)
        for card, deadline in client.calls
        if card.startswith("anime-2018")
    ]
    assert len(priority) == 4 and len(background) == 2
    assert all(deadline < client.initial_deadline for _, deadline in priority)
    assert all(deadline == client.initial_deadline for _, deadline in background)
    assert client.remaining == 0 and client.deadline == client.initial_deadline
    assert (
        sum(
            a.episodes.checked_at == NOW
            for a in repository.load_quarter("2018", "春").anime_list
        )
        == 2
    )


def test_unused_history_capacity_is_available_for_daily_work(
    tmp_path, anime_record_factory
):
    repository = setup_capacity_repository(
        tmp_path, anime_record_factory, background_due=False
    )
    client = CapacityClient()
    refresh_repository_episodes(repository, NOW, EpisodeEnricher(client))
    assert len(client.calls) == 6
    assert all(card.startswith("anime-2026") for card, _ in client.calls)
    assert all(deadline == client.initial_deadline for _, deadline in client.calls)


def test_remaining_reserved_capacity_finishes_deferred_daily_work(
    tmp_path,
    anime_record_factory,
):
    repository = setup_capacity_repository(tmp_path, anime_record_factory)
    dataset = repository.load_quarter("2018", "春")
    records = [dataset.anime_list[0]] + [
        a.model_copy(
            update={"episodes": a.episodes.model_copy(update={"attempted_at": NOW})}
        )
        for a in dataset.anime_list[1:]
    ]
    repository.write_quarter(
        year="2018",
        season="春",
        records=records,
        source_url=dataset.source_url,
        source_count=3,
        parse_failure_count=0,
    )
    client = CapacityClient()
    enricher = EpisodeEnricher(client)
    refresh_repository_episodes(repository, NOW, enricher)
    assert len(client.calls) == 6
    assert sum(card.startswith("anime-2026") for card, _ in client.calls) == 5
    assert client.calls[-1][0].startswith("anime-2026")
    assert client.calls[-1][1] == client.initial_deadline
    assert len(enricher.deferred) == 1
