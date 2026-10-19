"""Weekly source coverage, manual-review queue and provenance sidecar reports."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from models import TAIPEI_TZ, Anime
from services.atomic_io import atomic_write_json, atomic_write_text
from services.data_repository import DataQualityPolicy, DataRepository
from services.episode_refresh import identity
from services.episode_source_reviews import reviewed_source
from services.errors import ItemParseError, SourceFetchError
from services.http_client import SourceClient
from services.parser import extract_item_html, parse_anime_item
from services.secondary_source import (
    AniListClient,
    SecondarySourceError,
    compare_secondary,
)
from services.settings import CrawlerSettings

logger = logging.getLogger(__name__)


def manual_review_entry(anime: Anime, now: datetime) -> dict | None:
    review = reviewed_source(anime)
    if not review or review["mode"] != "snapshot" or review.get("official_adapter"):
        return None
    info = anime.episodes
    checked = info.checked_at if info else None
    interval = 90 if info and info.airing_status == "finished" else 7
    target = checked + timedelta(days=interval) if checked else None
    return {
        "interval_days": interval,
        "last_verified_at": checked.isoformat() if checked else None,
        "next_review_at": target.isoformat() if target else None,
        "due": target is None or now >= target,
        "reason": "finished_snapshot_periodic_review"
        if interval == 90
        else "unfinished_or_unknown_snapshot",
        "scope_explanation": review.get("scope_note")
        or review["metadata"].get("note", ""),
        "source_urls": review["metadata"]["source_urls"],
        "verification_method": "manual_official_review",
    }


def provenance(anime: Anime) -> dict:
    """Describe legacy evidence honestly; never turn an attempt into verification."""
    info = anime.episodes
    review = reviewed_source(anime)
    return {
        "provider": "reviewed_official"
        if review and review["mode"] == "snapshot"
        else "bangumi"
        if anime.external_ids.bangumi
        else "unknown",
        "verification_method": "manual_official_review"
        if review
        and review["mode"] == "snapshot"
        and not review.get("official_adapter")
        else info.source_kind
        if info and info.source_kind != "unknown"
        else "legacy_method_not_structured",
        "scope": "manually_reviewed_entry" if review else "linked_entry_main_episodes",
        "scope_explanation": (
            info.note
            if info and review.get("official_adapter")
            else review.get("scope_note") or review["metadata"].get("note", "")
        )
        if review
        else "僅限已連結條目正篇；跨來源範圍尚未獨立確認。",
        "source_urls": info.source_urls if info else [],
        "last_verified_at": info.checked_at.isoformat()
        if info and info.checked_at
        else None,
        "last_attempt_at": info.attempted_at.isoformat()
        if info and info.attempted_at
        else None,
        "source_snapshot_at": info.source_snapshot_date.isoformat()
        if info and info.source_snapshot_date
        else None,
        "snapshot_date_reason": "explicit_recorded_snapshot_date"
        if info and info.source_snapshot_date
        else "not_structured_in_legacy_data",
        "unknown_fields": [
            field
            for field, unknown in (
                ("total_episodes", info is None or info.total_episodes is None),
                ("aired_episodes", info is None or info.aired_episodes is None),
                ("airing_status", info is None or info.airing_status == "unknown"),
            )
            if unknown
        ],
        "unknown_reason": "see_preserved_source_note"
        if info and info.note
        else "not_recorded",
        "source_note": info.note if info else "",
    }


def audit_quarter(dataset: Any, document: str) -> tuple[dict, list]:
    """Require a fully parsed, unique source snapshot before comparing sets."""
    cards = [parse_anime_item(html) for html in extract_item_html(document)]
    source = {card.bangumi_id: card for card in cards}
    if len(source) != len(cards):
        raise ItemParseError("Duplicate source card IDs; comparison rejected")
    stored = {anime.bangumi_id: anime for anime in dataset.anime_list}
    differences = []
    for key in sorted(source.keys() | stored.keys()):
        before, after = stored.get(key), source.get(key)
        if before is None or after is None:
            differences.append(
                {
                    "card_id": key,
                    "kind": "missing_locally"
                    if before is None
                    else "absent_from_source",
                    "anime_name": (after or before).anime_name,
                }
            )
            continue
        changed = {}
        for field in ("anime_name", "original_name", "premiere_date", "premiere_time"):
            if getattr(before, field) != getattr(after, field):
                changed[field] = {
                    "stored": getattr(before, field),
                    "source": getattr(after, field),
                }
        for field in ("bangumi", "anilist"):
            old, new = (
                getattr(before.external_ids, field),
                getattr(after.external_ids, field),
            )
            if new is not None and old != new:
                changed[f"external_ids.{field}"] = {"stored": old, "source": new}
        if changed:
            differences.append(
                {
                    "card_id": key,
                    "anime_name": before.anime_name,
                    "kind": "changed",
                    "fields": changed,
                }
            )
    return {
        "stored_count": len(stored),
        "source_count": len(source),
        "differences": differences,
    }, cards


def optional_field_availability(
    anime: Anime,
    candidate: Any,
    unavailable_reason: str = "source_fetch_or_parse_failed",
) -> dict:
    fields = {
        "story": "暫無簡介",
        "premiere_date": "無首播日期",
        "premiere_time": "無首播時間",
    }
    result = {}
    for field, missing in fields.items():
        if getattr(anime, field) != missing:
            continue
        result[field] = (
            unavailable_reason
            if candidate is None
            else "absent_at_source"
            if getattr(candidate, field) == missing
            else "available_at_source_not_imported"
        )
    return result


def run_source_audit(
    data_dir: Path,
    *,
    now: datetime | None = None,
    source_client: Any = None,
    secondary_client: Any = None,
) -> dict:
    now = now or datetime.now(TAIPEI_TZ)
    if now.tzinfo is None:
        raise ValueError("Audit time must include a timezone")
    repository = DataRepository(data_dir, DataQualityPolicy())
    paths = repository.validate_all(allow_legacy=True)
    if not paths:
        raise ValueError("No quarterly data to audit")
    if source_client is None:
        settings = CrawlerSettings.from_environment()
        session = requests.Session()
        session.headers.update({"User-Agent": settings.source_user_agent})
        # Weekly audit has a separate budget: one bounded attempt per quarter.
        source_client = SourceClient(settings, session=session)
    secondary_client = secondary_client or AniListClient()
    report: dict = {
        "schema_version": 1,
        "observed_at": now.isoformat(),
        "mode": "read_only",
        "published_data_changed": False,
        "quarters": [],
        "identities": [],
        "errors": [],
        "limitations": [
            "Only locally recorded quarters are checked; new unknown quarters are not discovered.",
            "AniList explicit links only; no fuzzy search and no automatic count/status overrides.",
            "Corroborated titles and numbers do not prove identical episode scope.",
            "Official manual snapshots are queued for review, not automatically reverified.",
            "Legacy source method/snapshot dates remain unknown where not structured.",
        ],
    }
    grouped: dict[tuple, dict] = {}
    source_started = time.monotonic()
    for path in paths:
        dataset = repository.load_path(path)
        quarter = path.stem
        # Bind secondary links only to the exact unchanged identity on this page.
        source_cards = {}
        item = {
            "quarter": quarter,
            "attempted_at": None,
            "checked_at": None,
            "result": "error",
        }
        try:
            if time.monotonic() - source_started >= 600:
                raise SourceFetchError("Quarter audit time budget exhausted")
            year, season = quarter.split("_")
            item["attempted_at"] = now.isoformat()
            url, document = source_client.fetch_quarter_html(year, season)
            comparison, cards = audit_quarter(dataset, document)
            source_cards = {card.bangumi_id: card for card in cards}
            item.update(
                comparison,
                source_url=url,
                document_sha256=hashlib.sha256(document.encode()).hexdigest(),
                checked_at=now.isoformat(),
                result="difference" if comparison["differences"] else "matched",
            )
        except (SourceFetchError, ItemParseError, ValueError) as exc:
            item["error"] = type(exc).__name__
            report["errors"].append(
                {"stage": "quarter", "quarter": quarter, "reason": type(exc).__name__}
            )
        report["quarters"].append(item)
        logger.info("Quarter source audit: %s %s", quarter, item["result"])
        for anime in dataset.anime_list:
            group = grouped.setdefault(
                identity(anime),
                {
                    "copies": [],
                    "quarters": [],
                    "availability": [],
                    "source_links": [],
                    "ids": set(),
                },
            )
            group["copies"].append(anime)
            group["quarters"].append(quarter)
            if anime.external_ids.anilist:
                group["ids"].add(anime.external_ids.anilist)
            candidate = source_cards.get(anime.bangumi_id)
            # Text availability is tied to the card and its two names. A BG ID
            # resolved by the enricher may be absent from the source HTML.
            same_card = candidate and (
                candidate.bangumi_id,
                candidate.anime_name,
                candidate.original_name,
            ) == (anime.bangumi_id, anime.anime_name, anime.original_name)
            unavailable_reason = (
                "source_fetch_or_parse_failed"
                if item["result"] == "error"
                else "source_card_absent"
                if candidate is None
                else "source_identity_changed"
            )
            group["availability"].append(
                optional_field_availability(
                    anime, candidate if same_card else None, unavailable_reason
                )
            )
            link_verified = bool(
                same_card
                and candidate.external_ids.bangumi in (None, anime.external_ids.bangumi)
            )
            group["source_links"].append(
                {
                    "state": "verified" if link_verified else unavailable_reason,
                    "checked_at": now.isoformat() if link_verified else None,
                    "anilist_id": candidate.external_ids.anilist
                    if link_verified
                    else None,
                }
            )
            if link_verified and candidate.external_ids.anilist:
                group["ids"].add(candidate.external_ids.anilist)

    ids = sorted(
        {
            key
            for group in grouped.values()
            if len(group["ids"]) == 1
            for key in group["ids"]
        }
    )
    observations: dict[int, dict] = {}
    failed: dict[int, str] = {}
    attempted_ids: set[int] = set()
    for offset in range(0, len(ids), 40):
        batch = ids[offset : offset + 40]
        attempted_ids.update(batch)
        try:
            observations.update(secondary_client.fetch(batch))
        except SecondarySourceError as exc:
            reason = str(exc)
            if reason == "request_budget_exhausted":
                attempted_ids.difference_update(batch)
            report["errors"].append(
                {
                    "stage": "anilist",
                    "subject_ids": batch,
                    "reason": reason,
                    "response": getattr(secondary_client, "last_response_info", None),
                }
            )
            for subject_id in ids[offset:]:
                failed[subject_id] = reason
            # Do not hammer a failing provider; retain completed batches.
            break

    for group in grouped.values():
        anime = group["copies"][0]
        if (
            (review := reviewed_source(anime))
            and review.get("official_adapter")
            and any(
                copy.episodes and copy.episodes.fetch_state != "ok"
                for copy in group["copies"]
            )
        ):
            report["errors"].append(
                {
                    "stage": "official_catalogue",
                    "reason": "verification_failed_prior_evidence_retained",
                    "card_id": anime.bangumi_id,
                }
            )
        row = {
            "card_id": anime.bangumi_id,
            "anime_name": anime.anime_name,
            "original_name": anime.original_name,
            "bangumi_subject_id": anime.external_ids.bangumi,
            "quarters": group["quarters"],
            "primary_observations": [
                {
                    "quarter": quarter,
                    "provenance": provenance(copy),
                    "total_episodes": copy.episodes.total_episodes
                    if copy.episodes
                    else None,
                    "airing_status": copy.episodes.airing_status
                    if copy.episodes
                    else "unknown",
                    "episode_count_status": copy.episodes.episode_count_status
                    if copy.episodes
                    else "unknown",
                    "aired_episodes": copy.episodes.aired_episodes
                    if copy.episodes
                    else None,
                    "fetch_state": copy.episodes.fetch_state
                    if copy.episodes
                    else "unmatched",
                    "optional_field_availability": availability,
                    "source_link_observation": source_link,
                }
                for quarter, copy, availability, source_link in zip(
                    group["quarters"],
                    group["copies"],
                    group["availability"],
                    group["source_links"],
                    strict=True,
                )
            ],
            "manual_review": None,
            "secondary": [],
            "explicit_anilist_ids": sorted(group["ids"]),
        }
        reviews = [manual_review_entry(copy, now) for copy in group["copies"]]
        reviews = [review for review in reviews if review]
        if reviews:
            row["manual_review"] = min(
                reviews, key=lambda value: value["last_verified_at"] or ""
            )
        if len(group["ids"]) != 1:
            row["secondary_unavailable_reason"] = (
                "conflicting_explicit_ids"
                if group["ids"]
                else "no_explicit_anilist_link"
            )
        else:
            subject_id = next(iter(group["ids"]))
            for quarter, copy in zip(group["quarters"], group["copies"], strict=True):
                comparison = compare_secondary(
                    copy,
                    observations.get(subject_id),
                    subject_id=subject_id,
                    quarter=quarter,
                    today=now.date(),
                    reviewed_scope=reviewed_source(copy) is not None,
                )
                comparison.update(
                    quarter=quarter,
                    attempted_at=now.isoformat()
                    if subject_id in attempted_ids
                    else None,
                    checked_at=now.isoformat() if subject_id in observations else None,
                    source_snapshot_at=None,
                    verification_method="graphql_api",
                    scope="linked_entry_reported_total_and_status",
                    link_basis="explicit_quarter_source_or_stored_link",
                )
                if subject_id in failed:
                    comparison.update(
                        result="fetch_failed"
                        if subject_id in attempted_ids
                        else "not_attempted",
                        reasons=[failed[subject_id]],
                    )
                row["secondary"].append(comparison)
        report["identities"].append(row)

    report["summary"] = {
        "quarters": len(paths),
        "records": sum(len(group["copies"]) for group in grouped.values()),
        "unique_identities": len(grouped),
        "quarter_results": dict(Counter(q["result"] for q in report["quarters"])),
        "coverage_differences": sum(
            len(q.get("differences", [])) for q in report["quarters"]
        ),
        "manual_snapshot_identities": sum(
            row["manual_review"] is not None for row in report["identities"]
        ),
        "manual_reviews_due": sum(
            bool(row["manual_review"] and row["manual_review"]["due"])
            for row in report["identities"]
        ),
        "anilist_subjects_requested": len(ids),
        "anilist_subjects_attempted": len(attempted_ids),
        "anilist_subjects_observed": len(observations),
        "secondary_results_by_quarter_record": dict(
            Counter(
                comparison["result"]
                for row in report["identities"]
                for comparison in row["secondary"]
            )
        ),
        "identities_without_secondary_link": sum(
            row.get("secondary_unavailable_reason") == "no_explicit_anilist_link"
            for row in report["identities"]
        ),
        "errors": len(report["errors"]),
    }
    return report


def write_audit_report(report: dict, output: Path) -> str:
    atomic_write_json(output / "source-audit.json", report)
    from services.quality_evidence import audit_evidence

    atomic_write_json(
        output / "evidence.json",
        {
            "schema_version": 1,
            "assembled_at": report["observed_at"],
            "basis": "source_audit_with_retained_primary_evidence",
            "content_fingerprint_kind": "sha256_of_sorted_json_or_quarter_html",
            "records": audit_evidence(report),
        },
    )
    summary = report["summary"]
    lines = [
        "## 每週資料來源核對",
        "",
        f"核對時間：{report['observed_at']}",
        "",
        "此報告僅核對，不修改公開資料；差異不代表來源必然錯誤。",
        "",
    ]
    lines.extend(
        f"- {key}: {json.dumps(value, ensure_ascii=False)}"
        for key, value in summary.items()
    )
    for error in report["errors"]:
        lines.append(
            f"- 來源異常：{error['stage']}／{error['reason']}；回應：{json.dumps(error.get('response'), ensure_ascii=False)}"
        )

    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ").replace("<", "&lt;")

    lines.extend(
        ["", "### 季度收錄差異", "", "| 季度 | 作品 | 差異 |", "|---|---|---|"]
    )
    coverage_rows = [
        (quarter["quarter"], difference)
        for quarter in report["quarters"]
        for difference in quarter.get("differences", [])
    ]
    for quarter, difference in coverage_rows[:40]:
        detail = difference["kind"] + ": " + ", ".join(difference.get("fields", {}))
        lines.append(
            f"| {quarter} | {cell(difference['anime_name'])} | {cell(detail)} |"
        )
    lines.append(f"共 {len(coverage_rows)} 筆；摘要最多列出 40 筆，完整清單見 JSON。")
    lines.extend(
        [
            "",
            "### 第二來源待查證清單",
            "",
            "下列差異先由維護者查證；標題差異或既有官方範圍規則不等於需要使用者重新決定。",
            "",
            "| 季度 | 作品 | 核對結果 | 原因 |",
            "|---|---|---|---|",
        ]
    )
    secondary_rows = [
        (row, comparison)
        for row in report["identities"]
        for comparison in row["secondary"]
        if comparison["result"] != "corroborated"
    ]
    for row, comparison in secondary_rows[:40]:
        lines.append(
            f"| {comparison['quarter']} | {cell(row['anime_name'])} | {comparison['result']} | {cell(', '.join(comparison['reasons']))} |"
        )
    lines.append(f"共 {len(secondary_rows)} 筆；摘要最多列出 40 筆，完整清單見 JSON。")
    lines.extend(
        [
            "",
            "### 人工快照複查清單",
            "",
            "| 作品 | 到期 | 下次複查 | 原因 |",
            "|---|---|---|---|",
        ]
    )
    for row in report["identities"]:
        review = row["manual_review"]
        if review:
            name = cell(row["anime_name"])
            lines.append(
                f"| {name} | {'是' if review['due'] else '否'} | {review['next_review_at'] or '尚未查證'} | {review['reason']} |"
            )
    lines.extend(
        [
            "",
            "完整的季度差異、各筆來源、身分與範圍檢查結果見同份 artifact 的 source-audit.json。",
            "",
            "來源取得失敗時 checked_at 維持空值；不延長既有資料的查證時間。",
            "",
        ]
    )
    content = "\n".join(lines)
    atomic_write_text(output / "source-audit.md", content)
    return content
