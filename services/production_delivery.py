"""Join exact-release verification and source-refresh work without provider calls."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from models import TAIPEI_TZ
from services.atomic_io import atomic_write_json
from services.data_repository import DataQualityPolicy, DataRepository
from services.episode_refresh import refresh_backlog
from services.errors import DataContractError
from services.notifier import Notification
from services.settings import ProjectPaths

COMMIT = re.compile(r"[0-9a-f]{40}")


def read_report(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _commit(value) -> str:
    return (
        value if isinstance(value, str) and COMMIT.fullmatch(value) else "unavailable"
    )


def verified_delivery(report: dict, expected: str) -> bool:
    return bool(
        COMMIT.fullmatch(expected)
        and report.get("status") == "verified"
        and report.get("workflow_result") == "success"
        and report.get("expected_commit") == expected
        and report.get("deployed_commit") == expected
        and report.get("data_status") == "verified"
        and report.get("browser_status") == "verified"
        and report.get("browser_commit") == expected
    )


def write_delivery_report(
    paths: ProjectPaths,
    expected: str,
    workflow_result: str,
    *,
    now: datetime | None = None,
) -> dict:
    if not COMMIT.fullmatch(expected):
        raise ValueError("Expected commit must be a full lowercase git SHA")
    now = now or datetime.now(TAIPEI_TZ)
    output = paths.root / "audit-output"
    production = read_report(output / "production.json")
    browser = read_report(output / "production-browser.json")
    metadata = None
    try:
        repository = DataRepository(paths.data_dir, DataQualityPolicy())
        rows = [
            (path.stem, anime)
            for path in repository.validate_all()
            for anime in repository.load_path(path).anime_list
        ]
        if rows:
            metadata = refresh_backlog(rows, now)
    except (FileNotFoundError, ValueError, DataContractError):
        # Missing/invalid local data is observable; never turn it into zero pending.
        pass
    report = {
        "status": "failed",
        "workflow_result": workflow_result,
        "expected_commit": expected,
        "deployed_commit": _commit(production.get("deployed_commit")),
        "data_status": production.get("status", "unavailable"),
        "browser_status": browser.get("status", "unavailable"),
        "browser_commit": _commit(browser.get("commit")),
        "records": production.get("records", 0),
        "metadata_refresh": metadata,
    }
    if (
        workflow_result == "success"
        and production.get("expected_commit") == expected
        and production.get("status") == "superseded"
        and report["deployed_commit"] not in {expected, "unavailable"}
    ):
        report["status"] = "superseded"
    elif metadata is not None:
        candidate = {**report, "status": "verified"}
        if production.get("expected_commit") == expected and verified_delivery(
            candidate, expected
        ):
            report = candidate
    atomic_write_json(output / "delivery.json", report)
    return report


def build_production_notification(
    report: dict, expected: str, run_url: str, *, now: datetime | None = None
) -> Notification:
    timestamp = now or datetime.now(TAIPEI_TZ)
    metadata = report.get("metadata_refresh")
    counts_valid = isinstance(metadata, dict) and all(
        type(metadata.get(key)) is int and metadata[key] >= 0
        for key in ("pending_count", "priority_count", "background_count")
    )
    counts_valid = bool(
        counts_valid
        and isinstance(metadata.get("items"), list)
        and len(metadata["items"]) == metadata["pending_count"]
        and metadata["priority_count"] + metadata["background_count"]
        == metadata["pending_count"]
    )
    status = "FAILURE"
    title = "🚨 動畫正式站驗收未完成"
    detail = "正式站資料、資源與瀏覽器尚未全部通過同一提交的驗收。"
    if verified_delivery(report, expected) and counts_valid:
        pending = metadata["pending_count"]
        status = "PARTIAL" if pending else "SUCCESS"
        title = (
            "⚠️ 動畫正式站已驗收，來源更新仍有待辦"
            if pending
            else "✅ 動畫正式站驗收完成"
        )
        detail = (
            "正式站資料、關鍵資源與實際頁面均已通過本提交驗收。"
            f"\n來源更新待辦：{pending} 部（優先 {metadata['priority_count']}／背景 {metadata['background_count']}）。"
        )
        if pending:
            detail += f"\n待辦最舊成功查證：{metadata.get('oldest_checked_at') or '尚未查證'}。"
            detail += "\n完整身分、所屬季度與下次到期時間見 delivery.json；到期不代表已完成更新。"
    elif (
        COMMIT.fullmatch(expected)
        and report.get("expected_commit") == expected
        and report.get("workflow_result") == "success"
        and report.get("status") == "superseded"
        and _commit(report.get("deployed_commit")) not in {expected, "unavailable"}
    ):
        status = "PARTIAL"
        title = "ℹ️ 動畫正式站已有較新版本"
        detail = "本提交已由經主分支祖先關係確認的較新版本接替；本提交未宣告驗收成功。"
    detail += f"\n目標提交：{_commit(expected)}；正式提交：{_commit(report.get('deployed_commit'))}。"
    detail += f"\n驗收紀錄：{run_url}#artifacts"
    records = report.get("records", 0)
    return Notification(
        status=status,
        year=str(timestamp.astimezone(TAIPEI_TZ).year),
        season="正式站驗收",
        count=records if type(records) is int and records >= 0 else 0,
        message=detail,
        title=title,
    )
