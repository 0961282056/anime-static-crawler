"""Operational notifications kept outside crawler and repository logic."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

import requests

from models import TAIPEI_TZ
from services.errors import NotificationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Notification:
    status: str
    year: str
    season: str
    count: int = 0
    changed: bool = False
    parse_failures: int = 0
    message: str = ""


@dataclass(frozen=True)
class WorkflowOutcome:
    crawl_result: str
    publish_result: str
    data_changed: bool
    record_count: int = 0
    parse_failures: int = 0
    pr_url: str = ""
    run_url: str = ""
    event_name: str = ""
    run_attempt: str = "1"


def workflow_outcome_from_environment(
    environment: Mapping[str, str],
) -> WorkflowOutcome:
    changed_value = environment.get("DATA_CHANGED", "").strip().lower()
    if changed_value not in {"", "false", "true"}:
        raise NotificationError(
            "DATA_CHANGED must be true, false, or empty when the crawl failed"
        )

    def nonnegative_integer(name: str) -> int:
        raw_value = environment.get(name, "0").strip() or "0"
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise NotificationError(f"{name} must be an integer") from exc
        if value < 0:
            raise NotificationError(f"{name} must not be negative")
        return value

    return WorkflowOutcome(
        crawl_result=environment.get("CRAWL_RESULT", "").strip(),
        publish_result=environment.get("PUBLISH_RESULT", "").strip(),
        data_changed=changed_value == "true",
        record_count=nonnegative_integer("RECORD_COUNT"),
        parse_failures=nonnegative_integer("PARSE_FAILURES"),
        pr_url=environment.get("PR_URL", "").strip(),
        run_url=environment.get("RUN_URL", "").strip(),
        event_name=environment.get("EVENT_NAME", "").strip(),
        run_attempt=environment.get("RUN_ATTEMPT", "1").strip() or "1",
    )


def build_workflow_notification(
    outcome: WorkflowOutcome,
    *,
    now: datetime | None = None,
) -> Notification:
    no_change_success = (
        outcome.crawl_result == "success"
        and not outcome.data_changed
        and outcome.publish_result == "skipped"
    )
    pull_request_success = (
        outcome.crawl_result == "success"
        and outcome.data_changed
        and outcome.publish_result == "success"
        and bool(outcome.pr_url)
    )
    success = no_change_success or pull_request_success

    if pull_request_success:
        detail = f"資料更新 PR 已通過 Quality Gate 並自動合併：{outcome.pr_url}"
    elif no_change_success:
        detail = "爬蟲、資料驗證與建置成功；沒有語意資料變更。"
    else:
        detail = (
            "排程未完整成功；"
            f"crawl={outcome.crawl_result or 'missing'}，"
            f"publish={outcome.publish_result or 'missing'}。"
        )
        if outcome.run_url:
            detail += f" 請檢查：{outcome.run_url}"

    metadata = f"觸發={outcome.event_name or 'unknown'}；執行嘗試={outcome.run_attempt}"
    timestamp = now or datetime.now(TAIPEI_TZ)
    return Notification(
        status="SUCCESS" if success else "FAILURE",
        year=str(timestamp.astimezone(TAIPEI_TZ).year),
        season="自動排程",
        count=outcome.record_count,
        changed=outcome.data_changed,
        parse_failures=outcome.parse_failures,
        message=f"{detail} {metadata}",
    )


def build_selector_canary_failure_notification(
    *,
    run_url: str,
    event_name: str,
    run_attempt: str,
    now: datetime | None = None,
) -> Notification:
    """Build a failure-only alert without copying exception text into Discord."""

    timestamp = now or datetime.now(TAIPEI_TZ)
    detail = "來源網站 selector canary 失敗；未寫入 JSON、cache，也未呼叫 Cloudinary。"
    if run_url:
        detail += f" 請檢查：{run_url}"
    detail += (
        f" 觸發={event_name.strip() or 'unknown'}；"
        f"執行嘗試={run_attempt.strip() or '1'}"
    )
    return Notification(
        status="FAILURE",
        year=str(timestamp.astimezone(TAIPEI_TZ).year),
        season="來源 Selector Canary",
        message=detail,
    )


class DiscordNotifier:
    def __init__(self, webhook_url: str | None, *, required: bool = False) -> None:
        self.webhook_url = (webhook_url or "").strip()
        self.required = required

    def send(self, notification: Notification) -> bool:
        if not self.webhook_url:
            if self.required:
                raise NotificationError("DISCORD_WEBHOOK_URL is required")
            return False

        success = notification.status == "SUCCESS"
        color = 3066993 if success else 15158332
        title = "✅ 動畫爬蟲更新成功" if success else "🚨 動畫爬蟲執行失敗"
        description = (
            f"**季度**：{notification.year} {notification.season}\n"
            f"**有效資料**：{notification.count} 筆\n"
            f"**解析失敗**：{notification.parse_failures} 筆\n"
            f"**資料有變更**：{'是' if notification.changed else '否'}"
        )
        if notification.message:
            description += f"\n**說明**：{notification.message[:1000]}"

        payload = {
            "username": "Anime Crawler Bot",
            "embeds": [
                {
                    "title": title,
                    "description": description,
                    "color": color,
                }
            ],
        }
        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as exc:
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            message = "Discord notification failed"
            if isinstance(status_code, int):
                message += f" (HTTP {status_code})"
            raise NotificationError(message) from None
