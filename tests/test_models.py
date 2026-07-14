from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from models import TAIPEI_TZ, Anime, AnimeCandidate, QuarterDataset


def _anime_payload() -> dict[str, str]:
    return {
        "bangumi_id": "anime-2235",
        "anime_name": "測試動畫",
        "anime_image_url": (
            "https://res.cloudinary.com/test-cloud/image/upload/"
            "f_auto,q_auto:best/v1/anime_covers/" + "a" * 64
        ),
        "premiere_date": "一",
        "premiere_time": "26:38",
        "story": "測試簡介",
    }


@pytest.mark.parametrize(
    "bangumi_id",
    ("anime-", "ANIME-2235", "fallback-deadbeef", "../anime-2235"),
)
def test_anime_rejects_invalid_bangumi_id_formats(bangumi_id: str) -> None:
    payload = _anime_payload()
    payload["bangumi_id"] = bangumi_id

    with pytest.raises(ValidationError, match="bangumi_id must be"):
        Anime.model_validate(payload)


def test_anime_accepts_managed_source_and_fallback_ids() -> None:
    source_payload = _anime_payload()
    fallback_payload = {
        **source_payload,
        "bangumi_id": "fallback-" + "b" * 64,
    }

    assert Anime.model_validate(source_payload).bangumi_id == "anime-2235"
    assert Anime.model_validate(fallback_payload).bangumi_id.startswith("fallback-")


@pytest.mark.parametrize("premiere_date", ("星期一", "天曜日", "Monday", ""))
def test_anime_rejects_unknown_broadcast_days(premiere_date: str) -> None:
    payload = _anime_payload()
    payload["premiere_date"] = premiere_date

    if not premiere_date:
        assert Anime.model_validate(payload).premiere_date == "無首播日期"
        return
    with pytest.raises(ValidationError):
        Anime.model_validate(payload)


def test_anime_normalizes_legacy_sunday_label() -> None:
    payload = _anime_payload()
    payload["premiere_date"] = "天"

    assert Anime.model_validate(payload).premiere_date == "日"


@pytest.mark.parametrize("premiere_time", ("9:00", "30:00", "23:60", "明天"))
def test_anime_rejects_invalid_broadcast_times(premiere_time: str) -> None:
    payload = _anime_payload()
    payload["premiere_time"] = premiere_time

    with pytest.raises(ValidationError, match="premiere_time must use HH:MM"):
        Anime.model_validate(payload)


@pytest.mark.parametrize(
    "anime_image_url",
    (
        "http://res.cloudinary.com/test-cloud/image/upload/v1/anime_covers/" + "a" * 64,
        "https://evil.example/image/upload/v1/anime_covers/" + "a" * 64,
        "https://res.cloudinary.com/test-cloud/image/upload/v1/other/" + "a" * 64,
        "https://res.cloudinary.com/test-cloud/image/upload/v1/anime_covers/not-managed",
    ),
)
def test_anime_rejects_unmanaged_cloudinary_urls(anime_image_url: str) -> None:
    payload = _anime_payload()
    payload["anime_image_url"] = anime_image_url

    with pytest.raises(ValidationError, match="anime_image_url must be"):
        Anime.model_validate(payload)


def test_candidate_uses_the_same_id_day_and_time_contract() -> None:
    with pytest.raises(ValidationError):
        AnimeCandidate(
            bangumi_id="invalid",
            anime_name="測試",
            source_image_url="https://static.acgsecrets.hk/cover.jpg",
            premiere_date="星期一",
            premiere_time="9:00",
        )


@pytest.mark.parametrize(
    "source_url",
    (
        "http://acgsecrets.hk/bangumi/202607/",
        "https://evil.example/bangumi/202607/",
        "https://acgsecrets.hk/bangumi/202606/",
        "https://acgsecrets.hk/bangumi/202607/?preview=1",
    ),
)
def test_quarter_dataset_rejects_invalid_source_urls(source_url: str) -> None:
    with pytest.raises(ValidationError, match="source_url must be"):
        QuarterDataset(
            anime_list=[Anime.model_validate(_anime_payload())],
            generated_at=datetime(2026, 7, 14, tzinfo=TAIPEI_TZ),
            source_url=source_url,
        )
