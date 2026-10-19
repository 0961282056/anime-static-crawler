import json

import pytest

from models import Anime, EpisodeMetadata
from services.broadcast_source_reviews import broadcast_reviews, reviewed_broadcast
from services.data_repository import DataQualityPolicy, DataRepository
from services.errors import DataContractError


def reviewed_record(row, anime_record_factory, *, weekday="無首播日期"):
    return Anime.model_validate(
        {
            **anime_record_factory(1),
            "bangumi_id": row["card_id"],
            "anime_name": row["anime_name"],
            "original_name": row["original_name"],
            "external_ids": {"bangumi": row["subject_id"]},
            "premiere_date": weekday,
            "episodes": EpisodeMetadata(
                checked_at="2026-10-16T00:00:00+08:00"
            ).model_dump(),
        }
    )


def write(repository, record):
    return repository.write_quarter(
        year="2021",
        season="秋",
        records=[record],
        source_url="https://acgsecrets.hk/bangumi/202110/",
        source_count=1,
        parse_failure_count=0,
    )


@pytest.mark.parametrize("index", range(len(broadcast_reviews())))
def test_reviewed_weekday_survives_missing_source_fields_without_changing_episode_times(
    tmp_path, anime_record_factory, index
):
    row = broadcast_reviews()[index]
    original = reviewed_record(row, anime_record_factory)
    repository = DataRepository(tmp_path, DataQualityPolicy())
    write(repository, original)
    before = repository.load_quarter("2021", "秋").anime_list[0]
    assert before.premiere_date == row["weekday"]
    assert before.episodes == original.episodes
    # The source may temporarily omit its BG link and weekday. Existing exact
    # identity evidence remains valid once the repository restores its known ID.
    source = original.model_dump()
    source.update(episodes=None, external_ids={"bangumi": None})
    write(repository, source)
    after = repository.load_quarter("2021", "秋").anime_list[0]
    assert after == before
    assert after.story == original.story


def test_conflicting_weekday_or_reused_card_does_not_overwrite_reviewed_date(
    tmp_path, anime_record_factory
):
    row = broadcast_reviews()[0]
    repository = DataRepository(tmp_path, DataQualityPolicy())
    original = reviewed_record(row, anime_record_factory)
    write(repository, original)
    path = repository.quarter_path("2021", "秋")
    before = path.read_bytes()
    for changes in ({"premiere_date": "一"}, {"original_name": "另一版本"}):
        with pytest.raises(DataContractError, match="Reviewed broadcast"):
            write(repository, original.model_copy(update=changes))
        assert path.read_bytes() == before
    assert (
        reviewed_broadcast(original.model_copy(update={"anime_name": "另一作品"}))
        is None
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"weekday": "一至六"},
        {"subject_id": True},
        {"source_url": "https://user:pass@example.test/"},
        {"checked_at": "2026-10-19T12:00:00"},
        {"display_note": 42},
        {"display_note": " "},
    ],
)
def test_invalid_broadcast_evidence_is_rejected(tmp_path, monkeypatch, changes):
    import services.broadcast_source_reviews as reviews

    row = {**broadcast_reviews()[0], **changes}
    (tmp_path / "reviewed_broadcast_sources.json").write_text(json.dumps([row]))
    reviews.broadcast_reviews.cache_clear()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(reviews, "Path", lambda _: tmp_path / "module.py")
            with pytest.raises(ValueError):
                reviews.broadcast_reviews()
    finally:
        reviews.broadcast_reviews.cache_clear()


def test_first_write_cannot_replace_reviewed_scope_with_a_different_weekday(
    tmp_path, anime_record_factory
):
    row = next(row for row in broadcast_reviews() if row["card_id"] == "anime-1070")
    original = reviewed_record(row, anime_record_factory, weekday="一至五")
    repository = DataRepository(tmp_path, DataQualityPolicy())
    with pytest.raises(DataContractError, match="Reviewed broadcast weekday conflicts"):
        write(repository, original)
    assert not repository.quarter_path("2021", "秋").exists()
