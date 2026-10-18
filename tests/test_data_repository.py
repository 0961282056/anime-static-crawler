from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import services.atomic_io as atomic_io
import services.data_repository as data_repository_module
from models import TAIPEI_TZ
from services.data_repository import DataQualityPolicy, DataRepository
from services.errors import DataContractError

SOURCE_URL = "https://acgsecrets.hk/bangumi/202607/"
INITIAL_TIME = datetime(2026, 7, 10, 12, 0, tzinfo=TAIPEI_TZ)


def _repository(tmp_path: Path) -> DataRepository:
    return DataRepository(tmp_path / "data", DataQualityPolicy())


def _write(
    repository: DataRepository,
    records: list[dict[str, str]],
    *,
    generated_at: datetime = INITIAL_TIME,
    source_count: int | None = None,
    parse_failure_count: int = 0,
):
    return repository.write_quarter(
        year="2026",
        season="夏",
        records=records,
        source_url=SOURCE_URL,
        source_count=len(records) if source_count is None else source_count,
        parse_failure_count=parse_failure_count,
        generated_at=generated_at,
    )


def test_error_dictionary_is_rejected_and_existing_json_is_preserved(
    tmp_path: Path,
    anime_record_factory: Callable[..., dict[str, str]],
) -> None:
    repository = _repository(tmp_path)
    result = _write(repository, [anime_record_factory(1)])
    original = result.path.read_bytes()

    with pytest.raises(DataContractError, match="Anime contract"):
        _write(repository, [{"error": "upstream failed"}])

    assert result.path.read_bytes() == original


def test_invalid_record_reports_quarter_index_and_fields_without_payload(tmp_path):
    repository = _repository(tmp_path)
    payload = {"story": "large private input that must not enter the summary"}
    with pytest.raises(DataContractError) as caught:
        _write(repository, [payload])
    message = str(caught.value)
    assert "Record 1" in message
    assert "2026_夏.json" in message
    assert "anime_name" in message and "bangumi_id" in message
    assert payload["story"] not in message


def test_quality_failure_reports_the_exact_quarter_path(tmp_path, anime_record_factory):
    repository = _repository(tmp_path)
    result = _write(repository, [anime_record_factory(1)])
    payload = json.loads(result.path.read_text(encoding="utf-8"))
    payload["quality"]["missing_story_count"] = 1
    result.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DataContractError) as caught:
        repository.validate_all()
    assert caught.value.operation_context["path"] == result.path
    assert caught.value.operation_stage == "data-validation"


def test_atomic_replace_failure_preserves_previous_dataset_and_cleans_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    anime_record_factory: Callable[..., dict[str, str]],
) -> None:
    repository = _repository(tmp_path)
    result = _write(repository, [anime_record_factory(1, story="舊資料")])
    original = result.path.read_bytes()

    def fail_replace(source: str | Path, destination: str | Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(atomic_io.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        _write(repository, [anime_record_factory(1, story="新資料")])

    assert result.path.read_bytes() == original
    assert not list(result.path.parent.glob(f".{result.path.name}.*.tmp"))


def test_quality_gate_rejects_large_record_count_drop(
    tmp_path: Path,
    anime_record_factory: Callable[..., dict[str, str]],
) -> None:
    repository = _repository(tmp_path)
    original_records = [anime_record_factory(index) for index in range(10)]
    result = _write(repository, original_records)
    original = result.path.read_bytes()

    with pytest.raises(DataContractError, match="Record count dropped from 10 to 6"):
        _write(repository, original_records[:6])

    assert result.path.read_bytes() == original


def test_identical_records_do_not_rewrite_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    anime_record_factory: Callable[..., dict[str, str]],
) -> None:
    repository = _repository(tmp_path)
    records = [anime_record_factory(1)]
    first = _write(repository, records)
    original = first.path.read_bytes()

    def unexpected_write(path: Path, payload: object) -> None:
        raise AssertionError("unchanged records must not be rewritten")

    monkeypatch.setattr(
        data_repository_module,
        "atomic_write_json",
        unexpected_write,
    )

    second = _write(
        repository,
        records,
        generated_at=INITIAL_TIME + timedelta(days=1),
    )

    assert second.changed is False
    assert second.previous_count == 1
    assert second.current_count == 1
    assert first.path.read_bytes() == original


def test_generated_at_is_written_with_asia_taipei_offset(
    tmp_path: Path,
    anime_record_factory: Callable[..., dict[str, str]],
) -> None:
    repository = _repository(tmp_path)
    naive_taipei_time = datetime(2026, 7, 10, 9, 30)

    result = _write(
        repository,
        [anime_record_factory(1)],
        generated_at=naive_taipei_time,
    )

    payload = json.loads(result.path.read_text(encoding="utf-8"))
    generated_at = datetime.fromisoformat(payload["generated_at"])
    assert generated_at.utcoffset() == timedelta(hours=8)
    assert generated_at.replace(tzinfo=None) == naive_taipei_time


def test_parse_failure_count_fails_the_default_quality_gate(
    tmp_path: Path,
    anime_record_factory: Callable[..., dict[str, str]],
) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(DataContractError, match="Parse failure ratio"):
        _write(
            repository,
            [anime_record_factory(1)],
            source_count=2,
            parse_failure_count=1,
        )

    assert not repository.quarter_path("2026", "夏").exists()


def test_invalid_quarter_source_url_is_rejected_before_write(
    tmp_path: Path,
    anime_record_factory: Callable[..., dict[str, str]],
) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(DataContractError, match="Quarter dataset"):
        repository.write_quarter(
            year="2026",
            season="夏",
            records=[anime_record_factory(1)],
            source_url="https://evil.example/bangumi/202607/",
            source_count=1,
            parse_failure_count=0,
            generated_at=INITIAL_TIME,
        )

    assert not repository.quarter_path("2026", "夏").exists()


def test_strict_validation_rejects_legacy_unknown_ids(
    tmp_path: Path,
    anime_record_factory: Callable[..., dict[str, str]],
) -> None:
    repository = _repository(tmp_path)
    record = anime_record_factory(1)
    record["bangumi_id"] = "未知ID"
    path = repository.quarter_path("2026", "夏")
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "anime_list": [record],
                "generated_at": INITIAL_TIME.isoformat(),
                "source_url": SOURCE_URL,
                "quality": {
                    "source_count": 1,
                    "record_count": 1,
                    "parse_failure_count": 0,
                    "fallback_id_count": 0,
                    "missing_story_count": 0,
                    "missing_date_count": 0,
                    "missing_time_count": 0,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(DataContractError, match="may not use 未知ID"):
        repository.validate_all()
    assert repository.validate_all(allow_legacy=True) == [path]


def test_strict_validation_rejects_tampered_quality_summary(
    tmp_path: Path,
    anime_record_factory: Callable[..., dict[str, str]],
) -> None:
    repository = _repository(tmp_path)
    result = _write(repository, [anime_record_factory(1)])
    payload = json.loads(result.path.read_text(encoding="utf-8"))
    payload["quality"]["missing_story_count"] = 1
    result.path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(DataContractError, match="quality summary"):
        repository.validate_all()


@pytest.mark.parametrize("provider", ["bangumi", "anilist"])
@pytest.mark.parametrize("has_metadata", [False, True])
def test_conflicting_known_link_rejects_entire_write(
    tmp_path, anime_record_factory, provider, has_metadata
):
    repository = _repository(tmp_path)
    old = {**anime_record_factory(1), "external_ids": {provider: 100}}
    result = _write(repository, [old])
    original = result.path.read_bytes()
    incoming = {**old, "external_ids": {provider: 200}}
    if has_metadata:
        incoming["episodes"] = {"total_episodes": 12}
    with pytest.raises(DataContractError, match=provider):
        _write(repository, [incoming])
    assert result.path.read_bytes() == original


@pytest.mark.parametrize("replacement", [False, True])
def test_single_disappearing_card_blocks_write_even_with_new_replacement(
    tmp_path, anime_record_factory, replacement
):
    repository = _repository(tmp_path)
    records = [anime_record_factory(i) for i in range(4)]
    result = _write(repository, records)
    original = result.path.read_bytes()
    incoming = records[:3]
    if replacement:
        incoming.append(anime_record_factory(5))
    with pytest.raises(DataContractError, match="disappeared.*anime-0003"):
        _write(repository, incoming)
    assert result.path.read_bytes() == original


def test_new_card_with_all_previous_ids_is_allowed(tmp_path, anime_record_factory):
    repository = _repository(tmp_path)
    records = [anime_record_factory(i) for i in range(4)]
    _write(repository, records)
    records.append(anime_record_factory(5))
    result = _write(repository, records)
    assert result.changed
    assert result.current_count == 5


def test_reviewed_exact_display_rename_preserves_episode_evidence(
    tmp_path, anime_record_factory
):
    repository = _repository(tmp_path)
    old = {
        **anime_record_factory(1, name="DARK MACHINE THE ANIMATION"),
        "bangumi_id": "anime-2282",
        "original_name": "DARK MACHINE THE ANIMATION",
        "external_ids": {"bangumi": 505550, "anilist": 179876},
        "episodes": {
            "total_episodes": 12,
            "aired_episodes": 1,
            "checked_at": INITIAL_TIME.isoformat(),
            "attempted_at": INITIAL_TIME.isoformat(),
            "source_kind": "bangumi_api",
        },
    }
    _write(repository, [old])
    before = repository.load_quarter("2026", "夏").anime_list[0]
    _write(repository, [{**old, "anime_name": "闇機 DARK MACHINE", "episodes": None}])
    after = repository.load_quarter("2026", "夏").anime_list[0]
    assert after.anime_name == "闇機 DARK MACHINE"
    assert after.episodes == before.episodes
    assert after.external_ids == before.external_ids


def test_manual_episode_scope_cannot_be_lost_by_display_rename(
    tmp_path, anime_record_factory
):
    from services.episode_source_reviews import source_reviews

    row = next(r for r in source_reviews() if r["card_id"] == "anime-275")
    old = {
        **anime_record_factory(1, name=row["anime_name"]),
        "bangumi_id": row["card_id"],
        "original_name": row["original_name"],
        "external_ids": {"bangumi": row["subject_id"]},
        "episodes": row["metadata"],
    }
    repository = _repository(tmp_path)
    result = _write(repository, [old])
    original = result.path.read_bytes()
    with pytest.raises(DataContractError, match="Reviewed source binding"):
        _write(repository, [{**old, "anime_name": "新版中文名", "episodes": None}])
    assert result.path.read_bytes() == original


def test_official_story_binding_cannot_be_lost_by_display_rename(
    tmp_path, anime_record_factory
):
    from services.story_source_reviews import story_reviews

    row = story_reviews()[0]
    old = {
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
    repository = _repository(tmp_path)
    result = _write(repository, [old])
    original = result.path.read_bytes()
    with pytest.raises(DataContractError, match="Reviewed source binding"):
        _write(
            repository,
            [
                {
                    **old,
                    "anime_name": "新版中文名",
                    "story": "暫無簡介",
                    "story_source": None,
                }
            ],
        )
    assert result.path.read_bytes() == original


@pytest.mark.parametrize(
    "updates",
    [
        {"anime_name": "闇機 DARK MACHINE 第二季"},
        {"anime_name": "闇機 DARK MACHINE", "original_name": "DARK MACHINE Season 2"},
    ],
)
def test_display_name_review_cannot_authorize_another_version(
    tmp_path, anime_record_factory, updates
):
    repository = _repository(tmp_path)
    old = {
        **anime_record_factory(1, name="DARK MACHINE THE ANIMATION"),
        "bangumi_id": "anime-2282",
        "original_name": "DARK MACHINE THE ANIMATION",
        "external_ids": {"bangumi": 505550, "anilist": 179876},
        "episodes": {"total_episodes": 12, "aired_episodes": 1},
    }
    _write(repository, [old])
    _write(repository, [{**old, **updates, "episodes": None}])
    assert repository.load_quarter("2026", "夏").anime_list[0].episodes is None


@pytest.mark.parametrize("kind", ["episode", "story"])
@pytest.mark.parametrize("supplied_metadata", [False, True])
def test_original_title_change_cannot_drop_reviewed_binding(
    tmp_path, anime_record_factory, kind, supplied_metadata
):
    from services.episode_source_reviews import source_reviews
    from services.story_source_reviews import story_reviews

    row = (
        next(r for r in source_reviews() if r["card_id"] == "anime-275")
        if kind == "episode"
        else story_reviews()[0]
    )
    old = {
        **anime_record_factory(1, name=row["anime_name"]),
        "bangumi_id": row["card_id"],
        "original_name": row["original_name"],
        "external_ids": {"bangumi": row["subject_id"]},
    }
    if kind == "episode":
        old["episodes"] = row["metadata"]
    else:
        old.update(
            story=row["story"],
            story_source={
                "provider": "official",
                "method": "manual_official",
                "subject_id": row["subject_id"],
                "source_url": row["source_url"],
                "checked_at": row["checked_at"],
            },
        )
    repository = _repository(tmp_path)
    result = _write(repository, [old])
    original = result.path.read_bytes()
    incoming = {
        **old,
        "original_name": row["original_name"] + " Season 2",
        "story": "暫無簡介",
        "story_source": None,
        "episodes": {"total_episodes": 6} if supplied_metadata else None,
    }
    with pytest.raises(DataContractError, match="Reviewed source binding"):
        _write(repository, [incoming])
    assert result.path.read_bytes() == original


def test_validated_instance_with_modified_invalid_counts_is_rejected(
    tmp_path, anime_record_factory
):
    from models import Anime, EpisodeMetadata

    repository = _repository(tmp_path)
    old = {
        **anime_record_factory(1),
        "episodes": {"total_episodes": 12, "aired_episodes": 10},
    }
    result = _write(repository, [old])
    original = result.path.read_bytes()
    valid = Anime.model_validate(old)
    invalid = valid.model_copy(
        update={
            "episodes": EpisodeMetadata.model_validate(old["episodes"]).model_copy(
                update={"total_episodes": 6}
            )
        }
    )
    with pytest.raises(DataContractError, match="Anime contract"):
        _write(repository, [invalid])
    assert result.path.read_bytes() == original


def test_modified_instance_cannot_write_story_evidence_for_another_subject(
    tmp_path, anime_record_factory
):
    from models import Anime, ExternalIds

    repository = _repository(tmp_path)
    valid = Anime.model_validate(
        {
            **anime_record_factory(1),
            "external_ids": {"bangumi": 100},
            "story_source": {
                "subject_id": 100,
                "checked_at": INITIAL_TIME,
                "method": "bangumi_api",
            },
        }
    )
    invalid = valid.model_copy(update={"external_ids": ExternalIds(bangumi=200)})
    with pytest.raises(DataContractError, match="Anime contract"):
        _write(repository, [invalid])
    assert not repository.quarter_path("2026", "夏").exists()


@pytest.mark.parametrize("kind", ["episode", "story"])
def test_temporary_identity_omissions_preserve_reviewed_evidence(
    tmp_path, anime_record_factory, kind
):
    from services.episode_source_reviews import source_reviews
    from services.story_source_reviews import story_reviews

    row = (
        next(r for r in source_reviews() if r["card_id"] == "anime-275")
        if kind == "episode"
        else story_reviews()[0]
    )
    old = {
        **anime_record_factory(1, name=row["anime_name"]),
        "bangumi_id": row["card_id"],
        "original_name": row["original_name"],
        "external_ids": {"bangumi": row["subject_id"]},
    }
    if kind == "episode":
        old["story"] = "暫無簡介"
        old["episodes"] = row["metadata"]
    else:
        old.update(
            story=row["story"],
            story_source={
                "provider": "official",
                "method": "manual_official",
                "subject_id": row["subject_id"],
                "source_url": row["source_url"],
                "checked_at": row["checked_at"],
            },
        )
    repository = _repository(tmp_path)
    result = _write(repository, [old])
    original = result.path.read_bytes()
    incoming = {
        **old,
        "original_name": None,
        "external_ids": {},
        "episodes": None,
        "story": "暫無簡介",
        "story_source": None,
    }
    assert not _write(repository, [incoming]).changed
    assert result.path.read_bytes() == original


@pytest.mark.parametrize("provider", ["bangumi", "official"])
@pytest.mark.parametrize("same_text", [True, False])
def test_base_recrawl_keeps_attributed_story_and_evidence(
    tmp_path, anime_record_factory, provider, same_text
):
    if provider == "official":
        from services.story_source_reviews import story_reviews

        row = story_reviews()[0]
        old = {
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
    else:
        old = {
            **anime_record_factory(1, story="已逐筆核對並保留來源的作品介紹原文"),
            "original_name": "Verified Work",
            "external_ids": {"bangumi": 100},
            "story_source": {
                "provider": "bangumi",
                "method": "bangumi_api",
                "subject_id": 100,
                "checked_at": INITIAL_TIME.isoformat(),
            },
        }
    repository = _repository(tmp_path)
    result = _write(repository, [old])
    before = repository.load_path(result.path).anime_list[0]
    _write(
        repository,
        [
            {
                **old,
                "story": old["story"]
                if same_text
                else "來源頁新增但尚未獨立核對範圍的介紹",
                "story_source": None,
                "episodes": None,
            }
        ],
    )
    after = repository.load_path(result.path).anime_list[0]
    assert after.story == before.story
    assert after.story_source == before.story_source


def test_explicitly_attributed_replacement_remains_possible(
    tmp_path, anime_record_factory
):
    repository = _repository(tmp_path)
    old = {
        **anime_record_factory(1, story="先前核對的作品介紹原文"),
        "original_name": "Verified Work",
        "external_ids": {"bangumi": 100},
        "story_source": {
            "provider": "bangumi",
            "method": "bangumi_api",
            "subject_id": 100,
            "checked_at": INITIAL_TIME.isoformat(),
        },
    }
    result = _write(repository, [old])
    new = {
        **old,
        "story": "附有新來源證據的作品介紹更正內容",
        "story_source": {
            **old["story_source"],
            "checked_at": (INITIAL_TIME + timedelta(days=1)).isoformat(),
        },
        "episodes": None,
    }
    assert _write(repository, [new]).changed
    after = repository.load_path(result.path).anime_list[0]
    assert after.story == new["story"]
    assert after.story_source.checked_at == INITIAL_TIME + timedelta(days=1)
