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
