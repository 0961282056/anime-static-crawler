"""Validated, atomic repository for quarterly public datasets."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from models import TAIPEI_TZ, Anime, DataQuality, QuarterDataset
from services.atomic_io import atomic_write_json
from services.errors import DataContractError

QUARTER_FILE_PATTERN = re.compile(r"^(\d{4})_(冬|春|夏|秋)\.json$")


@dataclass(frozen=True)
class WriteResult:
    path: Path
    changed: bool
    previous_count: int
    current_count: int


@dataclass(frozen=True)
class DataQualityPolicy:
    minimum_count_ratio: float = 0.70
    maximum_parse_failure_ratio: float = 0.0
    maximum_fallback_id_ratio: float = 0.0

    def validate(
        self,
        records: list[Anime],
        quality: DataQuality,
        previous: QuarterDataset | None,
    ) -> None:
        if not records:
            raise DataContractError("Refusing to write an empty anime_list")
        if quality.source_count < len(records):
            raise DataContractError("source_count may not be smaller than record_count")
        if quality.record_count + quality.parse_failure_count != quality.source_count:
            raise DataContractError(
                "record_count + parse_failure_count must equal source_count"
            )

        expected_quality = DataQuality.from_records(
            records,
            source_count=quality.source_count,
            parse_failure_count=quality.parse_failure_count,
        )
        if quality != expected_quality:
            raise DataContractError(
                "Embedded quality summary does not match the anime records"
            )

        parse_failure_ratio = (
            quality.parse_failure_count / quality.source_count
            if quality.source_count
            else 1.0
        )
        if parse_failure_ratio > self.maximum_parse_failure_ratio:
            raise DataContractError(
                "Parse failure ratio "
                f"{parse_failure_ratio:.1%} exceeds "
                f"{self.maximum_parse_failure_ratio:.1%}"
            )

        fallback_ratio = quality.fallback_id_count / len(records)
        if fallback_ratio > self.maximum_fallback_id_ratio:
            raise DataContractError(
                "Fallback ID ratio "
                f"{fallback_ratio:.1%} exceeds "
                f"{self.maximum_fallback_id_ratio:.1%}"
            )
        if any(record.bangumi_id == "未知ID" for record in records):
            raise DataContractError("New records may not use 未知ID")

        ids = [record.bangumi_id for record in records]
        if len(ids) != len(set(ids)):
            raise DataContractError("Duplicate bangumi_id values detected")
        names = [record.anime_name.casefold() for record in records]
        if len(names) != len(set(names)):
            raise DataContractError("Duplicate anime names detected in one quarter")

        if previous and previous.anime_list:
            minimum_count = math.ceil(
                len(previous.anime_list) * self.minimum_count_ratio
            )
            if len(records) < minimum_count:
                raise DataContractError(
                    f"Record count dropped from {len(previous.anime_list)} to "
                    f"{len(records)}; minimum allowed is {minimum_count}"
                )


class DataRepository:
    def __init__(self, data_dir: Path, policy: DataQualityPolicy) -> None:
        self.data_dir = data_dir
        self.policy = policy

    def quarter_path(self, year: str, season: str) -> Path:
        if not str(year).isdigit() or len(str(year)) != 4:
            raise DataContractError(f"Invalid year: {year}")
        if season not in {"冬", "春", "夏", "秋"}:
            raise DataContractError(f"Invalid season: {season}")
        return self.data_dir / f"{year}_{season}.json"

    def load_path(self, path: Path) -> QuarterDataset:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return QuarterDataset.model_validate(raw)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise DataContractError(f"Invalid quarterly data {path}: {exc}") from exc

    def load_quarter(self, year: str, season: str) -> QuarterDataset | None:
        path = self.quarter_path(year, season)
        return self.load_path(path) if path.exists() else None

    def write_quarter(
        self,
        *,
        year: str,
        season: str,
        records: list[Anime | dict],
        source_url: str,
        source_count: int,
        parse_failure_count: int,
        generated_at: datetime | None = None,
    ) -> WriteResult:
        path = self.quarter_path(year, season)
        try:
            validated_records = [
                record if isinstance(record, Anime) else Anime.model_validate(record)
                for record in records
            ]
        except ValidationError as exc:
            raise DataContractError(
                f"Record does not satisfy the Anime contract: {exc}"
            ) from exc

        quality = DataQuality.from_records(
            validated_records,
            source_count=source_count,
            parse_failure_count=parse_failure_count,
        )
        previous = self.load_path(path) if path.exists() else None
        self.policy.validate(validated_records, quality, previous)

        previous_records = (
            [record.model_dump(mode="json") for record in previous.anime_list]
            if previous
            else []
        )
        current_records = [
            record.model_dump(mode="json") for record in validated_records
        ]
        if previous and previous_records == current_records:
            return WriteResult(
                path=path,
                changed=False,
                previous_count=len(previous_records),
                current_count=len(current_records),
            )

        try:
            dataset = QuarterDataset(
                anime_list=validated_records,
                generated_at=generated_at or datetime.now(TAIPEI_TZ),
                source_url=source_url,
                quality=quality,
            )
        except ValidationError as exc:
            raise DataContractError(
                f"Quarter dataset does not satisfy the data contract: {exc}"
            ) from exc
        atomic_write_json(path, dataset.model_dump(mode="json"))
        return WriteResult(
            path=path,
            changed=True,
            previous_count=len(previous_records),
            current_count=len(current_records),
        )

    def discover_available_data(self) -> dict[str, list[str]]:
        available: dict[str, list[str]] = {}
        if not self.data_dir.exists():
            return available
        for path in sorted(self.data_dir.glob("*.json")):
            match = QUARTER_FILE_PATTERN.fullmatch(path.name)
            if not match:
                continue
            dataset = self.load_path(path)
            if not dataset.anime_list:
                raise DataContractError(f"Empty quarterly dataset: {path}")
            year, season = match.groups()
            available.setdefault(year, []).append(season)

        season_order = {"冬": 1, "春": 2, "夏": 3, "秋": 4}
        for seasons in available.values():
            seasons.sort(key=season_order.__getitem__)
        return available

    def validate_all(self, *, allow_legacy: bool = False) -> list[Path]:
        paths: list[Path] = []
        if not self.data_dir.exists():
            return paths
        for path in sorted(self.data_dir.glob("*.json")):
            if QUARTER_FILE_PATTERN.fullmatch(path.name):
                dataset = self.load_path(path)
                if not allow_legacy:
                    if not dataset.source_url:
                        raise DataContractError(
                            f"Quarterly data is missing source_url: {path}"
                        )
                    if dataset.quality is None:
                        raise DataContractError(
                            f"Quarterly data is missing quality summary: {path}"
                        )
                    self.policy.validate(
                        dataset.anime_list,
                        dataset.quality,
                        previous=None,
                    )
                paths.append(path)
        return paths
