from __future__ import annotations

import traceback
from datetime import datetime

import pytest
import requests

from models import TAIPEI_TZ
from services.errors import NotificationError
from services.notifier import (
    DiscordNotifier,
    Notification,
    WorkflowOutcome,
    build_selector_canary_failure_notification,
    build_workflow_notification,
    workflow_outcome_from_environment,
)

FIXED_NOW = datetime(2026, 7, 13, 12, 0, tzinfo=TAIPEI_TZ)


def test_selector_canary_alert_is_failure_only_and_has_no_exception_text() -> None:
    run_url = "https://github.com/example/repo/actions/runs/99"
    notification = build_selector_canary_failure_notification(
        run_url=run_url,
        event_name="schedule",
        run_attempt="2",
        now=FIXED_NOW,
    )

    assert notification.status == "FAILURE"
    assert notification.season == "來源 Selector Canary"
    assert notification.count == 0
    assert notification.changed is False
    assert run_url in notification.message
    assert "未寫入 JSON、cache" in notification.message
    assert "schedule" in notification.message
    assert "執行嘗試=2" in notification.message


def test_no_change_workflow_is_successful() -> None:
    notification = build_workflow_notification(
        WorkflowOutcome(
            crawl_result="success",
            publish_result="skipped",
            data_changed=False,
            record_count=2067,
            parse_failures=3,
            event_name="schedule",
            run_attempt="2",
        ),
        now=FIXED_NOW,
    )

    assert notification.status == "SUCCESS"
    assert notification.year == "2026"
    assert notification.season == "自動排程"
    assert notification.count == 2067
    assert notification.changed is False
    assert notification.parse_failures == 3
    assert "沒有語意資料變更" in notification.message
    assert "觸發=schedule" in notification.message
    assert "執行嘗試=2" in notification.message


def test_changed_workflow_requires_a_successful_pull_request() -> None:
    pr_url = "https://github.com/example/anime-static-crawler/pull/42"
    notification = build_workflow_notification(
        WorkflowOutcome(
            crawl_result="success",
            publish_result="success",
            data_changed=True,
            record_count=418,
            parse_failures=1,
            pr_url=pr_url,
            run_url="https://github.com/example/anime-static-crawler/actions/runs/99",
            event_name="workflow_dispatch",
        ),
        now=FIXED_NOW,
    )

    assert notification.status == "SUCCESS"
    assert notification.changed is True
    assert notification.count == 418
    assert pr_url in notification.message
    assert "通過 Quality Gate 並自動合併" in notification.message


@pytest.mark.parametrize(
    "outcome",
    [
        WorkflowOutcome("failure", "skipped", False),
        WorkflowOutcome("cancelled", "skipped", False),
        WorkflowOutcome("success", "success", False),
        WorkflowOutcome("success", "failure", True),
        WorkflowOutcome("success", "skipped", True),
        WorkflowOutcome("success", "success", True, pr_url=""),
        WorkflowOutcome("", "", False),
    ],
)
def test_incomplete_workflow_outcomes_fail_closed(outcome: WorkflowOutcome) -> None:
    run_url = "https://github.com/example/anime-static-crawler/actions/runs/99"
    notification = build_workflow_notification(
        WorkflowOutcome(
            crawl_result=outcome.crawl_result,
            publish_result=outcome.publish_result,
            data_changed=outcome.data_changed,
            pr_url=outcome.pr_url,
            run_url=run_url,
        ),
        now=FIXED_NOW,
    )

    assert notification.status == "FAILURE"
    assert "排程未完整成功" in notification.message
    assert run_url in notification.message


def test_workflow_outcome_is_parsed_from_environment() -> None:
    outcome = workflow_outcome_from_environment(
        {
            "CRAWL_RESULT": " success ",
            "PUBLISH_RESULT": "success",
            "DATA_CHANGED": "TRUE",
            "RECORD_COUNT": "418",
            "PARSE_FAILURES": "2",
            "PR_URL": " https://github.com/example/repo/pull/42 ",
            "RUN_URL": "https://github.com/example/repo/actions/runs/99",
            "EVENT_NAME": "schedule",
            "RUN_ATTEMPT": "3",
        }
    )

    assert outcome == WorkflowOutcome(
        crawl_result="success",
        publish_result="success",
        data_changed=True,
        record_count=418,
        parse_failures=2,
        pr_url="https://github.com/example/repo/pull/42",
        run_url="https://github.com/example/repo/actions/runs/99",
        event_name="schedule",
        run_attempt="3",
    )


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({"DATA_CHANGED": "sometimes"}, "DATA_CHANGED must be"),
        ({"RECORD_COUNT": "many"}, "RECORD_COUNT must be an integer"),
        ({"PARSE_FAILURES": "-1"}, "PARSE_FAILURES must not be negative"),
    ],
)
def test_invalid_workflow_environment_is_rejected(
    environment: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(NotificationError, match=message):
        workflow_outcome_from_environment(environment)


def test_required_webhook_must_be_configured() -> None:
    notification = Notification(status="SUCCESS", year="2026", season="自動排程")

    with pytest.raises(NotificationError, match="DISCORD_WEBHOOK_URL is required"):
        DiscordNotifier("  ", required=True).send(notification)

    assert DiscordNotifier(None).send(notification) is False


def test_discord_http_error_raises_notification_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    webhook_url = "https://discord.example/api/webhooks/123456/secret-token"

    class FailingResponse:
        status_code = 500

        def raise_for_status(self) -> None:
            response = requests.Response()
            response.status_code = self.status_code
            response.url = webhook_url
            raise requests.HTTPError(
                f"500 Server Error for url: {webhook_url}",
                response=response,
            )

    def post(
        url: str,
        *,
        json: dict[str, object],
        timeout: int,
    ) -> FailingResponse:
        assert url == webhook_url
        assert json["username"] == "Anime Crawler Bot"
        assert timeout == 10
        return FailingResponse()

    monkeypatch.setattr("services.notifier.requests.post", post)

    with pytest.raises(
        NotificationError,
        match=r"^Discord notification failed \(HTTP 500\)$",
    ) as error:
        DiscordNotifier(webhook_url, required=True).send(
            Notification(status="FAILURE", year="2026", season="自動排程")
        )

    rendered_traceback = "".join(traceback.format_exception(error.value))
    assert webhook_url not in str(error.value)
    assert webhook_url not in rendered_traceback
    assert error.value.__suppress_context__ is True


def test_discord_network_error_does_not_expose_request_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    webhook_url = "https://discord.example/api/webhooks/123456/secret-token"

    def post(*args: object, **kwargs: object) -> None:
        raise requests.ConnectionError(f"connection failed for {webhook_url}")

    monkeypatch.setattr("services.notifier.requests.post", post)

    with pytest.raises(
        NotificationError,
        match="^Discord notification failed$",
    ) as error:
        DiscordNotifier(webhook_url, required=True).send(
            Notification(status="FAILURE", year="2026", season="自動排程")
        )

    assert webhook_url not in "".join(traceback.format_exception(error.value))
