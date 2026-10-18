import json
from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from models import Anime, ExternalIds, StorySource
from services.data_repository import DataQualityPolicy, DataRepository
from services.story_metadata import enrich_story

NOW = datetime.fromisoformat("2026-10-16T12:00:00+08:00")
SUMMARY = "這是來源提供的作品簡介，用於驗證來源身分、快照日期與資料保留行為。"


def test_official_excerpt_requires_exact_reviewed_card_url_and_text(
    anime_record_factory,
):
    from services.story_source_reviews import story_reviews

    for row in story_reviews():
        raw = {
            **anime_record_factory(1, name=row["anime_name"], story=row["story"]),
            "bangumi_id": row["card_id"],
            "original_name": row["original_name"],
            "external_ids": {"bangumi": row["subject_id"]},
            "story_source": {
                "provider": "official",
                "method": "manual_official",
                "source_url": row["source_url"],
                "subject_id": row["subject_id"],
                "checked_at": row["checked_at"],
            },
        }
        anime = Anime.model_validate(raw)
        assert enrich_story(anime, {}, NOW, method="bangumi_api") == anime
        for changes in (
            {"original_name": "第一季"},
            {"bangumi_id": "anime-999"},
            {"story": "錯誤的母條目內容"},
            {
                "story_source": {
                    **raw["story_source"],
                    "source_url": row["source_url"] + "?redirect=evil",
                }
            },
            {
                "story_source": {
                    **raw["story_source"],
                    "source_snapshot_date": "2026-10-14",
                }
            },
        ):
            with pytest.raises(ValidationError):
                Anime.model_validate({**raw, **changes})
        with pytest.raises(ValidationError):
            StorySource.model_validate({**raw["story_source"], "provider": "bangumi"})


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


def test_recrawl_without_summary_preserves_official_excerpt(
    tmp_path, anime_record_factory
):
    from services.story_source_reviews import story_reviews

    row = story_reviews()[0]
    raw = {
        **anime_record_factory(1, name=row["anime_name"], story=row["story"]),
        "bangumi_id": row["card_id"],
        "original_name": row["original_name"],
        "external_ids": {"bangumi": row["subject_id"]},
        "story_source": {
            "provider": "official",
            "method": "manual_official",
            "subject_id": row["subject_id"],
            "source_url": row["source_url"],
            "checked_at": row["checked_at"],
        },
    }
    repository = DataRepository(tmp_path, DataQualityPolicy())
    for record in (raw, {**raw, "story": "暫無簡介", "story_source": None}):
        repository.write_quarter(
            year="2026",
            season="秋",
            records=[Anime.model_validate(record)],
            source_url="https://acgsecrets.hk/bangumi/202610/",
            source_count=1,
            parse_failure_count=0,
        )
    saved = repository.load_quarter("2026", "秋").anime_list[0]
    assert saved.story == row["story"]
    assert saved.story_source.checked_at.isoformat() == row["checked_at"]
    assert saved.story_source.source_url == row["source_url"]


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
                "external_ids": ExternalIds(bangumi=100, anilist=200),
            }
        )
    )
    changed = repository.load_quarter("2026", "秋")
    assert changed.anime_list[0].story == "暫無簡介"
    assert changed.anime_list[0].story_source is None


@pytest.mark.parametrize(
    "variant", ["作品 第2期＋", "作品 第3期", "作品 第2期 第2部分"]
)
def test_different_version_cannot_inherit_subject_story_or_episode_identity(
    anime, variant
):
    from services.episode_metadata import subject_identity_matches

    variant_record = anime.model_copy(update={"original_name": variant})
    subject = {"id": 100, "type": 2, "name": "作品 第2期", "summary": SUMMARY}
    assert not subject_identity_matches(variant_record, subject)
    assert (
        enrich_story(variant_record, subject, NOW, method="bangumi_api")
        == variant_record
    )


def test_fullwidth_and_whitespace_normalization_preserves_version_symbol(anime):
    from services.episode_metadata import subject_identity_matches

    variant = anime.model_copy(update={"original_name": "作品　第２期＋"})
    subject = {"id": 100, "type": 2, "name": "作品 第2期+", "summary": SUMMARY}
    assert subject_identity_matches(variant, subject)
    assert enrich_story(variant, subject, NOW, method="bangumi_api").story == SUMMARY


def test_full_subject_formatting_review_can_fill_summary_but_keeps_plus_boundary(
    anime_record_factory,
):
    anime = Anime(
        **{
            **anime_record_factory(1401, story="暫無簡介"),
            "bangumi_id": "anime-1401",
            "anime_name": "催眠麥克風 -Division Rap Battle- Rhyme Anima +",
            "original_name": "ヒプノシスマイク-Division Rap Battle- Rhyme Anima +",
            "external_ids": {"bangumi": 426049},
        }
    )
    subject = {
        "id": 426049,
        "type": 2,
        "name": "『ヒプノシスマイク-Division Rap Battle-』Rhyme Anima ＋",
        "date": "2023-10-06",
        "summary": SUMMARY,
    }
    assert enrich_story(anime, subject, NOW, method="bangumi_api").story == SUMMARY
    for change in (
        {"date": "2020-10-02"},
        {"name": "『ヒプノシスマイク-Division Rap Battle-』Rhyme Anima"},
        {"id": 999},
    ):
        assert (
            enrich_story(anime, {**subject, **change}, NOW, method="bangumi_api")
            == anime
        )


def test_episode_binding_to_parent_does_not_authorize_parent_summary(
    anime_record_factory,
):
    from services.episode_metadata import subject_identity_matches

    anime = Anime(
        **{
            **anime_record_factory(418, story="暫無簡介"),
            "bangumi_id": "anime-418",
            "anime_name": "1日外出錄班長",
            "original_name": "1日外出録ハンチョウ",
            "external_ids": {"bangumi": 237834},
        }
    )
    parent = {
        "id": 237834,
        "type": 2,
        "name": "中間管理録トネガワ",
        "date": "2018-07-03",
        "summary": SUMMARY,
    }
    assert subject_identity_matches(anime, parent)
    assert enrich_story(anime, parent, NOW, method="bangumi_api") == anime


def test_reviewed_summary_aliases_require_the_entire_exact_identity(
    anime_record_factory,
):
    proof = json.loads(
        Path("docs/story-fill-2026-10-18/scope-review.json").read_text(encoding="utf-8")
    )
    for row in proof:
        record = Anime.model_validate(
            {
                **anime_record_factory(1, story="暫無簡介"),
                "bangumi_id": row["card_id"],
                "anime_name": row["anime_name"],
                "original_name": row["source_original_name"],
                "external_ids": {"bangumi": row["subject_id"]},
            }
        )
        subject = {
            "id": row["subject_id"],
            "type": 2,
            "name": row["subject_name"],
            "date": row["first_air_date"],
            "summary": SUMMARY,
        }
        filled = enrich_story(record, subject, NOW, method="bangumi_api")
        assert filled.story == SUMMARY, row["card_id"]
        for mutation in (
            {"id": 99999999},
            {"date": "1900-01-01"},
            {"name": row["subject_name"] + " 第2クール＋"},
        ):
            assert (
                enrich_story(record, {**subject, **mutation}, NOW, method="bangumi_api")
                == record
            ), row["card_id"]
        for mutation in (
            {"bangumi_id": "anime-99999999"},
            {"original_name": row["source_original_name"] + " 第2クール＋"},
        ):
            other = record.model_copy(update=mutation)
            assert enrich_story(other, subject, NOW, method="bangumi_api") == other, (
                row["card_id"]
            )


def test_unreviewed_partial_season_cannot_use_whole_season_summary(
    anime_record_factory,
):
    record = Anime.model_validate(
        {
            **anime_record_factory(2279, story="暫無簡介"),
            "bangumi_id": "anime-2279",
            "anime_name": "藥師少女的獨語 第三季 前半",
            "original_name": "薬屋のひとりごと 第3期 第1クール",
            "external_ids": {"bangumi": 568244},
        }
    )
    whole_season = {
        "id": 568244,
        "type": 2,
        "name": "薬屋のひとりごと 第3期",
        "date": "2026-10-02",
        "summary": SUMMARY,
    }
    assert enrich_story(record, whole_season, NOW, method="bangumi_api") == record
