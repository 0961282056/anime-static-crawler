from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from models import Anime
from services.data_repository import DataQualityPolicy, DataRepository
from services.errors import DataContractError
from services.external_link_reviews import external_link_reviews, reviewed_anilist_link


def record(row, factory):
    return Anime.model_validate(
        {
            **factory(1),
            "bangumi_id": row["card_id"],
            "anime_name": row["anime_name"],
            "original_name": row["original_name"],
            "external_ids": {"bangumi": row["subject_id"]},
            "episodes": {
                "total_episodes": 12,
                "aired_episodes": 12,
                "checked_at": "2026-10-16T00:00:00+08:00",
            },
        }
    )


def write(repository, quarter, anime):
    year, season = quarter.split("_")
    month = {"冬": "01", "春": "04", "夏": "07", "秋": "10"}[season]
    return repository.write_quarter(
        year=year,
        season=season,
        records=[anime],
        source_url=f"https://acgsecrets.hk/bangumi/{year}{month}/",
        source_count=1,
        parse_failure_count=0,
    )


def test_reviewed_reference_survives_source_omissions_without_refreshing_episode_proof(
    tmp_path, anime_record_factory
):
    key, row = next(iter(external_link_reviews().items()))
    quarter = key[0]
    repository = DataRepository(tmp_path, DataQualityPolicy())
    original = record(row, anime_record_factory)
    result = write(repository, quarter, original)
    stored = repository.load_path(result.path).anime_list[0]
    assert stored.external_ids.anilist == row["anilist_id"]
    assert stored.episodes == original.episodes
    assert stored.story == original.story

    missing = original.model_dump()
    missing.update(episodes=None, original_name=None, external_ids={})
    before = result.path.read_bytes()
    assert write(repository, quarter, missing).changed is False
    assert result.path.read_bytes() == before


@pytest.mark.parametrize(
    "change",
    [
        {"bangumi_id": "anime-9999999"},
        {"anime_name": "另一季"},
        {"original_name": "Other Cour"},
        {"external_ids": {"bangumi": 9999999}},
    ],
)
def test_near_matches_cannot_receive_a_reviewed_link(
    tmp_path, anime_record_factory, change
):
    key, row = next(iter(external_link_reviews().items()))
    repository = DataRepository(tmp_path, DataQualityPolicy())
    original = record(row, anime_record_factory).model_dump()
    changed = Anime.model_validate({**original, **change})
    assert reviewed_anilist_link(key[0], changed) is None
    result = write(repository, key[0], changed)
    assert repository.load_path(result.path).anime_list[0].external_ids.anilist is None


def test_other_quarter_is_not_implicitly_approved(tmp_path, anime_record_factory):
    _, row = next(iter(external_link_reviews().items()))
    original = record(row, anime_record_factory)
    assert reviewed_anilist_link("2017_冬", original) is None
    repository = DataRepository(tmp_path, DataQualityPolicy())
    result = write(repository, "2017_冬", original)
    assert repository.load_path(result.path).anime_list[0].external_ids.anilist is None


def test_reviewed_unicode_equivalence_does_not_expand_identity_matching(
    tmp_path, anime_record_factory
):
    key, row = next(
        (key, row)
        for key, row in external_link_reviews().items()
        if row.get("name_comparison") == "unicode_nfkc"
    )
    repository = DataRepository(tmp_path, DataQualityPolicy())
    original = record(row, anime_record_factory)
    result = write(repository, key[0], original)
    stored = repository.load_path(result.path).anime_list[0]
    assert stored.external_ids.anilist == row["anilist_id"]
    assert stored.original_name == row["original_name"] != row["source_title"]
    # Equivalence is evidence for this literal reviewed card, not a runtime
    # permission to attach links to other spellings of an incoming identity.
    changed = original.model_copy(update={"original_name": row["source_title"]})
    assert reviewed_anilist_link(key[0], changed) is None


@pytest.mark.parametrize("previous_write", [False, True])
def test_conflicting_explicit_link_never_replaces_reviewed_reference(
    tmp_path, anime_record_factory, previous_write
):
    key, row = next(iter(external_link_reviews().items()))
    repository = DataRepository(tmp_path, DataQualityPolicy())
    original = record(row, anime_record_factory)
    path = repository.quarter_path(*key[0].split("_"))
    before = None
    if previous_write:
        write(repository, key[0], original)
        before = path.read_bytes()
    changed = original.model_dump()
    changed["external_ids"]["anilist"] = row["anilist_id"] + 1
    with pytest.raises(DataContractError, match="[Aa]niList|anilist"):
        write(repository, key[0], changed)
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.parametrize(
    "change",
    [{"anime_name": "另一季"}, {"original_name": "Another Cour"}],
)
def test_reused_card_cannot_erase_or_reassign_reviewed_identity(
    tmp_path, anime_record_factory, change
):
    key, row = next(iter(external_link_reviews().items()))
    repository = DataRepository(tmp_path, DataQualityPolicy())
    original = record(row, anime_record_factory)
    result = write(repository, key[0], original)
    before = result.path.read_bytes()
    changed = original.model_dump()
    changed.update(change, episodes=None)
    with pytest.raises(DataContractError, match="Reviewed external-link identity"):
        write(repository, key[0], changed)
    assert result.path.read_bytes() == before


def test_every_reviewed_quarter_reference_matches_published_data():
    repository = DataRepository(
        Path(__file__).parents[1] / "dist" / "data", DataQualityPolicy()
    )
    datasets = {
        path.stem: repository.load_path(path) for path in repository.validate_all()
    }
    records = {
        (quarter, anime.bangumi_id): anime
        for quarter, dataset in datasets.items()
        for anime in dataset.anime_list
    }
    for key, row in external_link_reviews().items():
        anime = records[(key[0], row["card_id"])]
        assert reviewed_anilist_link(key[0], anime) == row
        assert anime.external_ids.anilist == row["anilist_id"]


@pytest.mark.parametrize(
    "mutation",
    [
        "unpinned_source",
        "wrong_license",
        "duplicate_quarter",
        "ambiguous_bangumi",
        "ambiguous_anilist",
        "future_begin",
        "missing_timezone",
        "boolean_id",
        "unicode_different_title",
        "unicode_naive_review_time",
        "unreviewed_comparison",
    ],
)
def test_invalid_or_ambiguous_review_evidence_is_rejected(
    tmp_path, monkeypatch, mutation
):
    import services.external_link_reviews as reviews

    path = Path(reviews.__file__).with_name("reviewed_external_links.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    document["entries"] = document["entries"][:1]
    row = document["entries"][0]
    if mutation == "unpinned_source":
        document["source"]["revision"] = "master"
    elif mutation == "wrong_license":
        document["source"]["license"] = "CC0"
    elif mutation == "duplicate_quarter":
        row["quarters"].append(row["quarters"][0])
    elif mutation in {"ambiguous_bangumi", "ambiguous_anilist"}:
        duplicate = copy.deepcopy(row)
        duplicate["card_id"] = "anime-9999999"
        field = "anilist_id" if mutation == "ambiguous_bangumi" else "subject_id"
        duplicate[field] += 1
        document["entries"].append(duplicate)
    elif mutation == "future_begin":
        row["source_begin"] = "2099-01-01T00:00:00+08:00"
    elif mutation == "missing_timezone":
        row["source_begin"] = "2018-01-01T00:00:00"
    elif mutation == "boolean_id":
        row["anilist_id"] = True
    elif mutation in {"unicode_different_title", "unicode_naive_review_time"}:
        row.update(
            name_comparison="unicode_nfkc",
            source_title="Another Season"
            if mutation == "unicode_different_title"
            else row["original_name"],
            reviewed_at="2026-10-25T12:00:00+08:00"
            if mutation == "unicode_different_title"
            else "2026-10-25T12:00:00",
        )
    elif mutation == "unreviewed_comparison":
        row["name_comparison"] = "fuzzy"
    (tmp_path / path.name).write_text(json.dumps(document), encoding="utf-8")
    reviews.external_link_reviews.cache_clear()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(reviews, "Path", lambda _: tmp_path / "module.py")
            with pytest.raises(ValueError):
                reviews.external_link_reviews()
    finally:
        reviews.external_link_reviews.cache_clear()
