"""Read-only evidence receipts and reproducible quality trends from Git data history."""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import tarfile
from datetime import datetime
from html import escape
from pathlib import Path

from models import TAIPEI_TZ
from services.atomic_io import atomic_write_json, atomic_write_text
from services.data_repository import DataQualityPolicy, DataRepository
from services.source_audit import provenance

QUARTER = re.compile(r"\d{4}_[冬春夏秋]\.json")
ISSUES = (
    "missing_story",
    "missing_date",
    "missing_bangumi",
    "missing_anilist",
    "episode_stale",
    "episode_conflict",
    "episode_method_unknown",
    "episode_evidence_missing",
)


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def record_key(quarter: str, row: dict) -> str:
    # A reused card, renamed version or different subject is a different identity.
    return json.dumps(
        [
            quarter,
            row.get("bangumi_id"),
            row.get("anime_name"),
            row.get("original_name"),
            (row.get("external_ids") or {}).get("bangumi"),
        ],
        ensure_ascii=False,
    )


def measure(documents: dict[str, dict | list]) -> dict:
    records = {}
    issues = {name: set() for name in ISSUES}
    for quarter, document in sorted(documents.items()):
        rows = document if isinstance(document, list) else document["anime_list"]
        if not isinstance(rows, list):
            raise ValueError(f"Invalid historical quarter: {quarter}")
        for row in rows:
            key = record_key(quarter, row)
            if key in records:
                raise ValueError(f"Duplicate historical identity: {quarter}")
            records[key] = row
            episode = row.get("episodes") or {}
            ids = row.get("external_ids") or {}
            flags = {
                "missing_story": not row.get("story") or row["story"] == "暫無簡介",
                "missing_date": not row.get("premiere_date")
                or row["premiere_date"] == "無首播日期",
                "missing_bangumi": not ids.get("bangumi"),
                "missing_anilist": not ids.get("anilist"),
                "episode_stale": episode.get("fetch_state") == "stale",
                "episode_conflict": episode.get("fetch_state") == "conflict",
                "episode_method_unknown": bool(episode)
                and episode.get("source_kind", "unknown") == "unknown",
                "episode_evidence_missing": not episode.get("checked_at"),
            }
            for name, present in flags.items():
                if present:
                    issues[name].add(key)
    if not records:
        raise ValueError("No quarterly records")
    return {
        "quarters": len(documents),
        "records": len(records),
        "counts": {name: len(keys) for name, keys in issues.items()},
        "rates": {
            name: round(len(keys) / len(records), 6) for name, keys in issues.items()
        },
        "identities": set(records),
        "issues": issues,
    }


def transition(previous: dict | None, current: dict) -> dict | None:
    if previous is None:
        return None
    common = previous["identities"] & current["identities"]
    return {
        "added_records": len(current["identities"] - previous["identities"]),
        "removed_records": len(previous["identities"] - current["identities"]),
        "comparable_records": len(common),
        "issues": {
            name: {
                "resolved": len(
                    (previous["issues"][name] - current["issues"][name]) & common
                ),
                "introduced": len(
                    (current["issues"][name] - previous["issues"][name]) & common
                ),
                "unchanged": len(
                    previous["issues"][name] & current["issues"][name] & common
                ),
            }
            for name in ISSUES
        },
    }


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout


def historical_documents(root: Path, commit: str) -> dict:
    documents = {}
    with tarfile.open(
        fileobj=io.BytesIO(git(root, "archive", commit, "dist/data"))
    ) as archive:
        # Read members in memory; never extract paths or follow links.
        for member in archive:
            path = Path(member.name)
            if (
                member.isfile()
                and path.parent.as_posix() == "dist/data"
                and QUARTER.fullmatch(path.name)
            ):
                if member.size > 10 * 1024 * 1024:
                    raise ValueError("Historical quarter exceeds size limit")
                handle = archive.extractfile(member)
                assert handle is not None
                documents[path.stem] = json.load(handle)
    return documents


def quality_trend(root: Path, documents: dict, *, limit: int = 120) -> dict:
    if not 1 <= limit <= 1000:
        raise ValueError("History limit must be between 1 and 1000")
    history = (
        git(
            root,
            "log",
            "--first-parent",
            f"--max-count={limit + 1}",
            "--format=%H %cI",
            "HEAD",
            "--",
            "dist/data",
        )
        .decode()
        .splitlines()
    )
    shallow = git(root, "rev-parse", "--is-shallow-repository").strip() == b"true"
    snapshots, skipped = [], []
    previous = None
    for line in reversed(history[:limit]):
        commit, recorded = line.split(" ", 1)
        try:
            values = measure(historical_documents(root, commit))
        except (ValueError, KeyError, TypeError) as exc:
            skipped.append(
                {
                    "commit": commit,
                    "reason": type(exc).__name__,
                    "detail": str(exc)[:200],
                }
            )
            continue
        snapshots.append(
            {
                "commit": commit,
                "recorded_at": recorded,
                **{
                    key: values[key]
                    for key in ("quarters", "records", "counts", "rates")
                },
                "change": transition(previous, values),
            }
        )
        previous = values
    current = measure(documents)
    return {
        "schema_version": 1,
        "basis": "git_first_parent_data_revisions",
        "timestamp_meaning": "Commit recording time, never a source verification time.",
        "history_limit": limit,
        "history_truncated": len(history) > limit,
        "shallow_history": shallow,
        "skipped_revisions": skipped,
        "snapshots": snapshots,
        "working_tree": {
            **{key: current[key] for key in ("quarters", "records", "counts", "rates")},
            "change_since_last_data_commit": transition(previous, current),
            "issue_records": {
                name: [json.loads(key) for key in sorted(keys)]
                for name, keys in current["issues"].items()
            },
        },
    }


def evidence_entry(
    *,
    identity,
    quarter,
    field,
    provider,
    method,
    scope,
    state,
    source_urls=(),
    attempted_at=None,
    checked_at=None,
    last_verified_at=None,
    snapshot_at=None,
    locator=None,
    content_sha256=None,
    reason=None,
) -> dict:
    if state not in {"observed", "retained", "failed", "not_attempted", "unrecorded"}:
        raise ValueError("Unknown evidence state")
    if state == "observed" and checked_at is None:
        raise ValueError("A successful observation requires checked_at")
    if state != "observed" and checked_at is not None:
        raise ValueError("Only a new successful observation may have checked_at")
    value = {
        "identity": identity,
        "quarter": quarter,
        "field": field,
        "provider": provider,
        "method": method,
        "scope": scope,
        "state": state,
        "source_urls": list(source_urls),
        "attempted_at": attempted_at,
        "checked_at": checked_at,
        "last_verified_at": last_verified_at,
        "source_snapshot_at": snapshot_at,
        "locator": locator,
        "content_sha256": content_sha256,
        "reason": reason,
    }
    return {"evidence_id": fingerprint(value), **value}


def audit_evidence(report: dict) -> list[dict]:
    """Normalize current HTTP observations and retained primary evidence separately."""
    receipts = []
    for quarter in report["quarters"]:
        receipts.append(
            evidence_entry(
                identity=None,
                quarter=quarter["quarter"],
                field="quarter_coverage",
                provider="acgsecrets",
                method="quarter_html",
                scope="quarter_card_identities",
                state="observed"
                if quarter.get("checked_at")
                else "failed"
                if quarter.get("attempted_at")
                else "not_attempted",
                source_urls=[quarter["source_url"]]
                if quarter.get("source_url")
                else [],
                attempted_at=quarter.get("attempted_at"),
                checked_at=quarter.get("checked_at"),
                content_sha256=quarter.get("document_sha256"),
                locator=f"source-audit.json#/quarters/{len(receipts)}",
                reason=quarter.get("error"),
            )
        )
    for index, row in enumerate(report["identities"]):
        identity = {
            key: row.get(key)
            for key in ("card_id", "anime_name", "original_name", "bangumi_subject_id")
        }
        for offset, observation in enumerate(row["primary_observations"]):
            p = observation["provenance"]
            receipts.append(
                evidence_entry(
                    identity=identity,
                    quarter=observation["quarter"],
                    field="episodes",
                    provider=p["provider"],
                    method=p["verification_method"],
                    scope=p["scope"],
                    state="retained",
                    source_urls=p["source_urls"],
                    attempted_at=p["last_attempt_at"],
                    last_verified_at=p["last_verified_at"],
                    snapshot_at=p["source_snapshot_at"],
                    content_sha256=fingerprint(observation),
                    locator=f"source-audit.json#/identities/{index}/primary_observations/{offset}",
                    reason=p["scope_explanation"],
                )
            )
        for offset, comparison in enumerate(row["secondary"]):
            receipts.append(
                evidence_entry(
                    identity=identity,
                    quarter=comparison["quarter"],
                    field="secondary_comparison",
                    provider="anilist",
                    method=comparison["verification_method"],
                    scope=comparison["scope"],
                    state="observed"
                    if comparison.get("checked_at")
                    else "failed"
                    if comparison.get("attempted_at")
                    else "not_attempted",
                    source_urls=["https://graphql.anilist.co"],
                    attempted_at=comparison.get("attempted_at"),
                    checked_at=comparison.get("checked_at"),
                    locator=f"source-audit.json#/identities/{index}/secondary/{offset}",
                    content_sha256=fingerprint(comparison),
                    reason=comparison.get("reasons"),
                )
            )
    return receipts


def stored_evidence(data_dir: Path) -> tuple[dict, list]:
    repository = DataRepository(data_dir, DataQualityPolicy())
    documents, receipts = {}, []
    for path in repository.validate_all(allow_legacy=True):
        dataset = repository.load_path(path)
        documents[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        for index, anime in enumerate(dataset.anime_list):
            identity = {
                "card_id": anime.bangumi_id,
                "anime_name": anime.anime_name,
                "original_name": anime.original_name,
                "bangumi_subject_id": anime.external_ids.bangumi,
            }
            p = provenance(anime)
            receipts.append(
                evidence_entry(
                    identity=identity,
                    quarter=path.stem,
                    field="episodes",
                    provider=p["provider"],
                    method=p["verification_method"],
                    scope=p["scope"],
                    state="retained",
                    source_urls=p["source_urls"],
                    attempted_at=p["last_attempt_at"],
                    last_verified_at=p["last_verified_at"],
                    snapshot_at=p["source_snapshot_at"],
                    locator=f"dist/data/{path.name}#/anime_list/{index}/episodes",
                    content_sha256=fingerprint(anime.episodes.model_dump(mode="json"))
                    if anime.episodes
                    else None,
                    reason=p["scope_explanation"],
                )
            )
            story = anime.story_source
            receipts.append(
                evidence_entry(
                    identity=identity,
                    quarter=path.stem,
                    field="story",
                    provider=story.provider if story else "unknown",
                    method=story.method if story else "unrecorded",
                    scope="exact_linked_entry_summary_original_text"
                    if story
                    else "unrecorded",
                    state="retained" if story else "unrecorded",
                    source_urls=[
                        story.source_url or f"https://bgm.tv/subject/{story.subject_id}"
                    ]
                    if story
                    else [],
                    last_verified_at=story.checked_at.isoformat() if story else None,
                    snapshot_at=story.source_snapshot_date.isoformat()
                    if story and story.source_snapshot_date
                    else None,
                    locator=f"dist/data/{path.name}#/anime_list/{index}/story",
                    content_sha256=fingerprint(anime.story),
                    reason="missing_story"
                    if anime.story == "暫無簡介"
                    else "source_not_recorded"
                    if not story
                    else None,
                )
            )
    return documents, receipts


def trend_svg(trend: dict) -> str:
    snapshots = trend["snapshots"]
    series = [
        ("missing_story", "簡介缺漏", "#bb86fc"),
        ("missing_anilist", "AniList 未連結", "#64b5f6"),
        ("episode_evidence_missing", "集數查證時間缺漏", "#ffb74d"),
    ]
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="360" viewBox="0 0 960 360" role="img">',
        "<title>資料品質缺漏率趨勢</title><desc>依 Git 資料版本，百分比以各版本季度記錄總數為分母；時間不是來源查證時間。</desc>",
        '<rect width="960" height="360" fill="#18202b"/>',
        '<g fill="#eeeeee" font-family="sans-serif" font-size="14">',
        '<text x="30" y="28">資料品質缺漏率（0–100%） · Git 記錄時間，非來源查證時間</text>',
    ]
    for y, label in [(60, "100%"), (160, "50%"), (260, "0%")]:
        parts.append(
            f'<text x="12" y="{y + 5}">{label}</text><path d="M65 {y}H930" stroke="#405064"/>'
        )
    for offset, (key, label, color) in enumerate(series):
        points = " ".join(
            f"{65 + i * 865 / max(1, len(snapshots) - 1):.1f},{260 - s['rates'][key] * 200:.1f}"
            for i, s in enumerate(snapshots)
        )
        parts.append(
            f'<polyline points="{points}" stroke="{color}" fill="none" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{30 + offset * 300}" y="335" fill="{color}">{label}</text>'
        )
    if snapshots:
        for x, snapshot in [(65, snapshots[0]), (790, snapshots[-1])]:
            parts.append(
                f'<text x="{x}" y="290">{escape(snapshot["recorded_at"][:10])}</text>'
            )
    parts.append("</g></svg>\n")
    return "".join(parts)


def write_quality_evidence(
    root: Path, data_dir: Path, output: Path, *, history_limit: int = 120
) -> str:
    if output.resolve().is_relative_to((root / "dist").resolve()):
        raise ValueError("Evidence output must be outside public dist")
    documents, receipts = stored_evidence(data_dir)
    trend = quality_trend(root, documents, limit=history_limit)
    commit = git(root, "rev-parse", "HEAD").decode().strip()
    snapshots = {}
    for quarter, document in documents.items():
        relative = f"dist/data/{quarter}.json"
        target = output / relative
        atomic_write_json(target, document)
        snapshots[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
    bundle = {
        "schema_version": 1,
        "assembled_at": datetime.now(TAIPEI_TZ).isoformat(),
        "basis": "stored_evidence_not_a_new_source_verification",
        "commit": commit,
        "data_matches_commit": documents == historical_documents(root, commit),
        "data_snapshot_sha256": snapshots,
        "content_fingerprint_kind": "sha256_of_sorted_json",
        "records": receipts,
    }
    atomic_write_json(output / "evidence.json", bundle)
    atomic_write_json(output / "quality-trend.json", trend)
    atomic_write_text(output / "quality-trend.svg", trend_svg(trend))
    current = trend["working_tree"]
    lines = [
        "## 證據與品質趨勢",
        "",
        "唯讀整理；不重新查證來源、不改寫公開資料或補推測日期。",
        "",
        f"季度：{current['quarters']}；記錄：{current['records']}；Git 資料版本：{len(trend['snapshots'])}。",
        "",
        "| 品質項目 | 目前缺漏／異常 | 比率 |",
        "|---|---:|---:|",
    ]
    lines.extend(
        f"| {name} | {current['counts'][name]} | {current['rates'][name]:.2%} |"
        for name in ISSUES
    )
    lines.extend(
        [
            "",
            "evidence.json：精確作品身分、季度、欄位、來源／方法／範圍、各種時間、內容指紋與原始記錄位置。",
            "",
            "quality-trend.json：各資料版本原始數量及比率；同身分新生／解決問題與新增／移除記錄分開計算。",
            "",
            "quality-trend.svg 可直接開啟看圖。歷史持久保存在 Git，artifact 到期後可重建。",
            "",
            f"歷史截斷：{trend['history_truncated']}；淺層 clone：{trend['shallow_history']}；不相容歷史版本：{len(trend['skipped_revisions'])}。",
            "",
        ]
    )
    content = "\n".join(lines)
    atomic_write_text(output / "quality-evidence.md", content)
    return content
