from datetime import datetime

import pytest
from pydantic import ValidationError

from models import Anime
from services.data_repository import DataQualityPolicy, DataRepository
from services.errors import DataContractError
from services.story_metadata import enrich_story
from services.wikipedia_story_reviews import (
    public_wikipedia_stories,
    wikipedia_story_reviews,
)


def record(row, anime_record_factory):
    return {
        **anime_record_factory(1, name=row["anime_name"], story=row["story"]),
        "bangumi_id": row["card_id"],
        "original_name": row["original_name"],
        "external_ids": {"bangumi": row["subject_id"], "anilist": row["anilist_id"]},
        "story_source": {
            "provider": "wikipedia",
            "method": "wikipedia_excerpt",
            "subject_id": row["subject_id"],
            "source_url": row["source_url"],
            "checked_at": row["checked_at"],
            "wikipedia": row["attribution"],
        },
    }


@pytest.mark.parametrize("row", wikipedia_story_reviews(), ids=lambda r: r["card_id"])
def test_revision_excerpt_rejects_wrong_version_text_or_attribution(
    row, anime_record_factory
):
    raw = record(row, anime_record_factory)
    anime = Anime.model_validate(raw)
    assert (
        enrich_story(
            anime, {}, datetime.fromisoformat(row["checked_at"]), method="bangumi_api"
        )
        == anime
    )
    for change in (
        {"bangumi_id": "anime-99999"},
        {"anime_name": "前一季"},
        {"original_name": "母系列"},
        {"story": raw["story"] + "未經來源核准的文字"},
        {"external_ids": {"bangumi": row["subject_id"], "anilist": 1}},
    ):
        with pytest.raises(ValidationError):
            Anime.model_validate({**raw, **change})
    for change in (
        {"provider": "official"},
        {"provider": "bangumi"},
        {"method": "bangumi_api"},
        {"source_url": "https://zh.wikipedia.org/wiki/母系列"},
        {"checked_at": "2020-01-01T00:00:00+08:00"},
        {"source_snapshot_date": "2024-10-25"},
        {"wikipedia": None},
        *(
            {"wikipedia": {**row["attribution"], key: value}}
            for key, value in (
                ("revision_id", row["attribution"]["revision_id"] + 1),
                ("revision_id", True),
                ("revision_at", "2020-01-01T00:00:00"),
                ("contributors_url", "https://example.com/"),
                ("license", "public domain"),
                ("license_url", "https://example.com/license"),
                ("changes", "未變更"),
                ("page_title", "前一季"),
            )
        ),
    ):
        with pytest.raises(ValidationError):
            Anime.model_validate(
                {**raw, "story_source": {**raw["story_source"], **change}}
            )


@pytest.mark.parametrize("row", wikipedia_story_reviews(), ids=lambda r: r["card_id"])
def test_recrawl_retains_licensed_excerpt_and_rejects_rebinding(
    row, tmp_path, anime_record_factory
):
    raw = record(row, anime_record_factory)
    repo = DataRepository(tmp_path, DataQualityPolicy())

    def write(value):
        return repo.write_quarter(
            year="2026",
            season="秋",
            records=[value],
            source_url="https://acgsecrets.hk/bangumi/202610/",
            source_count=1,
            parse_failure_count=0,
        )

    write(raw)
    incoming = {**raw, "story": "另一個來源的介紹", "story_source": None}
    write({**incoming, "external_ids": {}, "original_name": None})
    saved = repo.load_quarter("2026", "秋").anime_list[0]
    assert saved.story == raw["story"]
    assert saved.story_source.provider == "wikipedia"
    assert saved.story_source.wikipedia.license == "CC BY-SA 4.0"
    for change in (
        {"original_name": "第一季"},
        {"anime_name": "第一季"},
        {"external_ids": {"bangumi": row["subject_id"], "anilist": 1}},
        {
            "story_source": {
                "provider": "bangumi",
                "method": "bangumi_api",
                "subject_id": row["subject_id"],
                "checked_at": row["checked_at"],
            }
        },
    ):
        with pytest.raises(DataContractError):
            write({**incoming, **change})


def test_public_registry_rejects_duplicate_redirect_or_missing_license(monkeypatch):
    import services.wikipedia_story_reviews as reviews

    row = wikipedia_story_reviews()[0]
    assert public_wikipedia_stories()[row["card_id"]] == row
    for rows in (
        [row, row],
        [{**row, "source_url": "https://example.com/wiki"}],
        [{**row, "source_url": row["source_url"] + "&redirect=1"}],
        [
            {
                **row,
                "source_url": row["source_url"].replace(
                    str(row["attribution"]["revision_id"]), "1"
                ),
            }
        ],
        [{**row, "attribution": {**row["attribution"], "license": None}}],
        [
            {
                **row,
                "attribution": {
                    **row["attribution"],
                    "contributors_url": "https://example.com/",
                },
            }
        ],
    ):
        monkeypatch.setattr(reviews, "wikipedia_story_reviews", lambda rows=rows: rows)
        with pytest.raises(ValueError):
            reviews.public_wikipedia_stories()


def test_quality_receipt_keeps_wikipedia_revision_separate_from_check_time(
    tmp_path, anime_record_factory
):
    from models import QuarterDataset
    from services.quality_evidence import stored_evidence

    row = wikipedia_story_reviews()[0]
    anime = Anime.model_validate(record(row, anime_record_factory))
    dataset = QuarterDataset(anime_list=[anime], generated_at=row["checked_at"])
    path = tmp_path / "2026_秋.json"
    path.write_text(dataset.model_dump_json(), encoding="utf-8")
    before = path.read_bytes()
    _, receipts = stored_evidence(tmp_path)
    story = next(receipt for receipt in receipts if receipt["field"] == "story")
    assert story["provider"] == "wikipedia"
    assert story["method"] == "wikipedia_excerpt"
    assert story["scope"] == "reviewed_wikipedia_revision_excerpt"
    assert datetime.fromisoformat(
        story["source_snapshot_at"]
    ) == datetime.fromisoformat(row["attribution"]["revision_at"])
    assert story["checked_at"] is None
    assert datetime.fromisoformat(story["last_verified_at"]) == datetime.fromisoformat(
        row["checked_at"]
    )
    assert path.read_bytes() == before
