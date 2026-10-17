"""Validated, atomic repository for quarterly public datasets."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from models import TAIPEI_TZ, Anime, DataQuality, ExternalIds, QuarterDataset
from services.atomic_io import atomic_write_json
from services.diagnostics import operation
from services.display_name_reviews import reviewed_display_rename
from services.episode_source_reviews import reviewed_source
from services.errors import DataContractError
from services.source_identity import conflicting_providers
from services.story_source_reviews import reviewed_story

QUARTER_FILE_PATTERN = re.compile(r"^(\d{4})_(冬|春|夏|秋)\.json$")


def _validation_detail(error: ValidationError) -> str:
    issues = error.errors(include_input=False, include_url=False)
    details = [
        f"{'.'.join(str(part) for part in issue['loc']) or '<root>'}: {issue['msg']} ({issue['type']})"
        for issue in issues[:20]
    ]
    if len(issues) > 20:
        details.append(f"... {len(issues) - 20} additional field errors")
    return f"{len(issues)} field error(s): " + "; ".join(details)


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

            missing_ids = {a.bangumi_id for a in previous.anime_list} - set(ids)
            if missing_ids:
                raise DataContractError(
                    "Previously stored cards disappeared; reviewed removal required: "
                    + ", ".join(sorted(missing_ids))
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
        except ValidationError as exc:
            raise DataContractError(
                f"Invalid quarterly data {path}: {_validation_detail(exc)}"
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
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
        validated_records = []
        for index, record in enumerate(records, start=1):
            try:
                # model_copy() bypasses validation, including nested models.
                # Revalidate serialized fields at the persistence boundary.
                validated_records.append(
                    Anime.model_validate(
                        record.model_dump() if isinstance(record, Anime) else record
                    )
                )
            except ValidationError as exc:
                raise DataContractError(
                    f"Record {index} does not satisfy the Anime contract ({path.name}): {_validation_detail(exc)}"
                ) from exc

        quality = DataQuality.from_records(
            validated_records,
            source_count=source_count,
            parse_failure_count=parse_failure_count,
        )
        previous = self.load_path(path) if path.exists() else None
        # Base source refreshes do not carry episode enrichment. Preserve it by
        # stable card ID only while the linked anime identity is unchanged.
        previous_by_id = (
            {record.bangumi_id: record for record in previous.anime_list}
            if previous
            else {}
        )
        for index, record in enumerate(validated_records):
            old = previous_by_id.get(record.bangumi_id)
            if old and (
                conflicts := conflicting_providers(
                    old.external_ids, record.external_ids
                )
            ):
                raise DataContractError(
                    f"Conflicting explicit source IDs for {record.bangumi_id}: "
                    f"{', '.join(conflicts)}; reviewed identity correction required"
                )
            renamed = old is not None and record.anime_name != old.anime_name
            if old and record.episodes is None:
                changed_identity = (
                    (
                        record.external_ids.bangumi is not None
                        and record.external_ids.bangumi != old.external_ids.bangumi
                    )
                    or (renamed and not reviewed_display_rename(old, record))
                    or (
                        record.original_name is not None
                        and old.original_name is not None
                        and record.original_name != old.original_name
                    )
                )
                if not changed_identity:
                    validated_records[index] = record.model_copy(
                        update={
                            "episodes": old.episodes,
                            "external_ids": ExternalIds(
                                bangumi=record.external_ids.bangumi
                                or old.external_ids.bangumi,
                                anilist=record.external_ids.anilist
                                or old.external_ids.anilist,
                            ),
                            "original_name": record.original_name or old.original_name,
                            "story": old.story
                            if record.story == "暫無簡介" and old.story_source
                            else record.story,
                            "story_source": old.story_source
                            if record.story == "暫無簡介" and old.story_source
                            else record.story_source,
                        }
                    )
            # Check the effective record after restoring temporarily absent
            # source fields. Every identity field can invalidate a reviewed
            # scope, even when the Chinese display name has not changed.
            effective = validated_records[index]
            if old and (
                (reviewed_source(old) and not reviewed_source(effective))
                or (
                    old.story_source
                    and old.story_source.provider == "official"
                    and not reviewed_story(effective)
                )
            ):
                raise DataContractError(
                    f"Reviewed source binding changed for {record.bangumi_id}; "
                    "update its exact reviewed rule before changing identity"
                )
        quality = DataQuality.from_records(
            validated_records,
            source_count=source_count,
            parse_failure_count=parse_failure_count,
        )
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
                f"Quarter dataset does not satisfy the data contract ({path.name}): {_validation_detail(exc)}"
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
                with operation("data-validation", log_success=False, path=path):
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
