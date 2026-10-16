from datetime import date, datetime

import pytest

from models import Anime, ExternalIds
from services.data_repository import DataQualityPolicy, DataRepository
from services.story_metadata import enrich_story

NOW = datetime.fromisoformat("2026-10-16T12:00:00+08:00")
SUMMARY = "這是來源提供的作品簡介，用於驗證來源身分、快照日期與資料保留行為。"


@pytest.fixture
def anime(anime_record_factory):
    return Anime(
        **anime_record_factory(1, story="暫無簡介"),
        original_name="作品 第2期",
        external_ids=ExternalIds(bangumi=100),
    )


def enrich(anime, **changes):
    subject = {
        "id": 100,
        "type": 2,
        "name": "作品 第2期",
        "summary": SUMMARY,
        **changes,
    }
    return enrich_story(
        anime, subject, NOW, method="bangumi_archive", snapshot_date=date(2026, 10, 14)
    )


def test_missing_summary_is_attributed_to_exact_entry_and_archive(anime):
    updated = enrich(anime)
    assert updated.story == SUMMARY
    assert updated.story_source.subject_id == 100
    assert updated.story_source.source_snapshot_date == date(2026, 10, 14)
    assert updated.story_source.checked_at == NOW
    assert updated.episodes == anime.episodes


@pytest.mark.parametrize(
    "changes",
    [
        {"id": 101},
        {"name": "作品 第1期"},
        {"type": 1},
        {"summary": None},
        {"summary": "暂无简介"},
        {"summary": "短文"},
        {"summary": "x" * 10001},
    ],
)
def test_missing_malformed_or_different_work_does_not_fill(anime, changes):
    assert enrich(anime, **changes) == anime


def test_existing_summary_is_never_replaced(anime):
    known = anime.model_copy(update={"story": "原有介紹"})
    assert enrich(known) == known


def test_base_source_missing_text_preserves_attributed_summary_and_quality(
    tmp_path, anime
):
    repository = DataRepository(tmp_path, DataQualityPolicy())

    def save(record):
        repository.write_quarter(
            year="2026",
            season="秋",
            records=[record],
            source_url="https://acgsecrets.hk/bangumi/202610/",
            source_count=1,
            parse_failure_count=0,
        )

    save(enrich(anime))
    candidate = anime.model_copy(
        update={"episodes": None, "external_ids": ExternalIds(anilist=200)}
    )
    save(candidate)
    result = repository.load_quarter("2026", "秋")
    assert result.anime_list[0].story == SUMMARY
    assert result.anime_list[0].story_source.subject_id == 100
    assert result.anime_list[0].external_ids == ExternalIds(bangumi=100, anilist=200)
    assert result.quality.missing_story_count == 0
    # An actual identity change must not inherit an old work's summary.
    save(
        candidate.model_copy(
            update={
                "original_name": "另一作品",
                "external_ids": ExternalIds(bangumi=300),
            }
        )
    )
    changed = repository.load_quarter("2026", "秋")
    assert changed.anime_list[0].story == "暫無簡介"
    assert changed.anime_list[0].story_source is None
