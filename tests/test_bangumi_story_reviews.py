from datetime import datetime

import pytest
from pydantic import ValidationError

from models import Anime
from services.bangumi_story_reviews import bangumi_story_reviews
from services.data_repository import DataQualityPolicy, DataRepository
from services.errors import DataContractError
from services.story_metadata import enrich_story


def excerpt(anime_record_factory):
    row = bangumi_story_reviews()[0]
    return {
        **anime_record_factory(1, name=row["anime_name"], story=row["story"]),
        "bangumi_id": row["card_id"],
        "original_name": row["original_name"],
        "external_ids": {"bangumi": row["subject_id"]},
        "story_source": {
            "provider": "bangumi",
            "method": "bangumi_excerpt",
            "subject_id": row["subject_id"],
            "source_url": row["source_url"],
            "checked_at": row["checked_at"],
        },
    }


def test_excerpt_rejects_other_work_text_provider_and_evidence(anime_record_factory):
    raw = excerpt(anime_record_factory)
    anime = Anime.model_validate(raw)
    assert (
        enrich_story(
            anime,
            {},
            datetime.fromisoformat(raw["story_source"]["checked_at"]),
            method="bangumi_api",
        )
        == anime
    )
    for change in (
        {"bangumi_id": "anime-275"},
        {"anime_name": "暗芝居 6期"},
        {"original_name": "闇芝居 六期"},
        {"story": raw["story"] + "新聞稿及演員評論"},
        {"external_ids": {"bangumi": 250117}},
    ):
        with pytest.raises(ValidationError):
            Anime.model_validate({**raw, **change})
    for change in (
        {"provider": "official"},
        {"source_url": "https://api.bgm.tv/v0/subjects/250117"},
        {"source_snapshot_date": "2026-10-18"},
        {"checked_at": "2026-10-18T00:00:00+08:00"},
        {"method": "bangumi_api"},
    ):
        with pytest.raises(ValidationError):
            Anime.model_validate(
                {**raw, "story_source": {**raw["story_source"], **change}}
            )


def test_recrawl_preserves_excerpt_and_rejects_scope_changes(
    tmp_path, anime_record_factory
):
    raw = excerpt(anime_record_factory)
    repo = DataRepository(tmp_path, DataQualityPolicy())

    def write(record):
        return repo.write_quarter(
            year="2019",
            season="夏",
            records=[record],
            source_url="https://acgsecrets.hk/bangumi/201907/",
            source_count=1,
            parse_failure_count=0,
        )

    write(raw)
    incoming = {
        **raw,
        "story_source": None,
        "story": "來源回傳的未經範圍審核的介紹",
        "external_ids": {},
        "original_name": None,
    }
    write(incoming)
    saved = repo.load_quarter("2019", "夏").anime_list[0]
    assert saved.story == raw["story"]
    assert saved.story_source.method == "bangumi_excerpt"
    with pytest.raises(DataContractError, match="Reviewed source binding changed"):
        write({**incoming, "original_name": "闇芝居 六期"})
    with pytest.raises(DataContractError, match="Reviewed source binding changed"):
        write(
            {
                **raw,
                "story": "同一條目的完整新聞稿及演員評論，不能取代已審核節錄",
                "story_source": {
                    "provider": "bangumi",
                    "method": "bangumi_api",
                    "subject_id": raw["external_ids"]["bangumi"],
                    "checked_at": raw["story_source"]["checked_at"],
                },
            }
        )
