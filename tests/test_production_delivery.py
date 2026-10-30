import json
from datetime import timedelta
from unittest.mock import Mock

import pytest

from models import Anime, EpisodeMetadata
from services.atomic_io import atomic_write_json
from services.data_repository import DataQualityPolicy, DataRepository
from services.notifier import DiscordNotifier
from services.production_delivery import (
    build_production_notification,
    read_report,
    write_delivery_report,
)
from tests.test_episode_metadata import NOW

SHA = "a" * 40
RUN = "https://github.com/example/repo/actions/runs/123"


def test_management_commands_pass_exact_delivery_report_to_notification(
    delivery, monkeypatch
):
    import manage

    paths, _, _, _, _ = delivery
    monkeypatch.setattr(manage.ProjectPaths, "from_environment", lambda: paths)
    monkeypatch.setenv("EXPECTED_COMMIT", SHA)
    monkeypatch.setenv("VERIFY_RESULT", "success")
    monkeypatch.setenv("RUN_URL", RUN)
    monkeypatch.setenv("VERIFY_JOB_RESULT", "success")
    monkeypatch.setenv("EVIDENCE_RESULT", "success")
    notifier = Mock()
    monkeypatch.setattr(manage, "DiscordNotifier", lambda *a, **k: notifier)
    manage.execute_command("production-report")
    manage.execute_command("notify-production")
    notifier.send.assert_called_once()
    notification = notifier.send.call_args.args[0]
    assert notification.status == "PARTIAL" and SHA in notification.message


def test_failed_management_report_is_saved_before_command_fails(delivery, monkeypatch):
    import manage

    paths, _, _, _, _ = delivery
    monkeypatch.setattr(manage.ProjectPaths, "from_environment", lambda: paths)
    monkeypatch.setenv("EXPECTED_COMMIT", SHA)
    monkeypatch.setenv("VERIFY_RESULT", "failure")
    with pytest.raises(ValueError, match="incomplete"):
        manage.execute_command("production-report")
    assert read_report(paths.root / "audit-output/delivery.json")["status"] == "failed"


@pytest.mark.parametrize(
    ("verify_result", "evidence_result"),
    [
        ("failure", "success"),
        ("cancelled", "success"),
        ("success", "failure"),
        ("success", "skipped"),
        (None, "success"),
        ("success", None),
    ],
)
def test_notification_rejects_leftover_reports_after_job_or_digest_failure(
    delivery, monkeypatch, verify_result, evidence_result
):
    import manage

    paths, _, _, _, _ = delivery
    write_delivery_report(paths, SHA, "success", now=NOW)
    monkeypatch.setattr(manage.ProjectPaths, "from_environment", lambda: paths)
    monkeypatch.setenv("EXPECTED_COMMIT", SHA)
    monkeypatch.setenv("RUN_URL", RUN)
    for name, value in (
        ("VERIFY_JOB_RESULT", verify_result),
        ("EVIDENCE_RESULT", evidence_result),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    notifier = Mock()
    monkeypatch.setattr(manage, "DiscordNotifier", lambda *a, **k: notifier)
    manage.execute_command("notify-production")
    assert notifier.send.call_args.args[0].status == "FAILURE"


@pytest.fixture
def delivery(project_paths, anime_record_factory, monkeypatch):
    repository = DataRepository(project_paths.data_dir, DataQualityPolicy())
    record = Anime(
        **anime_record_factory(1),
        external_ids={"bangumi": 123},
        episodes=EpisodeMetadata(
            airing_status="airing",
            fetch_state="ok",
            checked_at=NOW - timedelta(days=2),
            attempted_at=NOW - timedelta(days=2),
        ),
    )
    repository.write_quarter(
        year="2026",
        season="秋",
        records=[record],
        source_url="https://acgsecrets.hk/bangumi/202610/",
        source_count=1,
        parse_failure_count=0,
    )
    output = project_paths.root / "audit-output"
    production = {
        "status": "verified",
        "expected_commit": SHA,
        "deployed_commit": SHA,
        "records": 1,
    }
    browser = {"status": "verified", "commit": SHA}
    atomic_write_json(output / "production.json", production)
    atomic_write_json(output / "production-browser.json", browser)
    # Report construction must not crawl providers or send a notification.
    monkeypatch.setattr(
        "requests.get", lambda *a, **k: pytest.fail("unexpected HTTP GET")
    )
    monkeypatch.setattr(
        "requests.post", lambda *a, **k: pytest.fail("unexpected HTTP POST")
    )
    return project_paths, repository, record, production, browser


def test_delivery_keeps_pending_source_work_partial_until_verified_refresh(delivery):
    paths, repository, record, _, _ = delivery
    before = {p.name: p.read_bytes() for p in repository.validate_all()}
    report = write_delivery_report(paths, SHA, "success", now=NOW)
    notice = build_production_notification(report, SHA, RUN, now=NOW)
    assert report["status"] == "verified"
    assert notice.status == "PARTIAL"
    assert "來源更新待辦：1" in notice.message and SHA in notice.message
    assert {p.name: p.read_bytes() for p in repository.validate_all()} == before
    fresh = record.model_copy(
        update={
            "episodes": record.episodes.model_copy(
                update={"checked_at": NOW, "attempted_at": NOW}
            )
        }
    )
    repository.write_quarter(
        year="2026",
        season="秋",
        records=[fresh],
        source_url="https://acgsecrets.hk/bangumi/202610/",
        source_count=1,
        parse_failure_count=0,
    )
    report = write_delivery_report(paths, SHA, "success", now=NOW)
    assert report["metadata_refresh"]["pending_count"] == 0
    assert build_production_notification(report, SHA, RUN, now=NOW).status == "SUCCESS"
    assert read_report(paths.root / "audit-output/delivery.json") == report


@pytest.mark.parametrize(
    ("target", "key", "value"),
    [
        ("production", "expected_commit", "b" * 40),
        ("production", "deployed_commit", "b" * 40),
        ("production", "status", "failed"),
        ("browser", "commit", "b" * 40),
        ("browser", "status", "failed"),
    ],
)
def test_delivery_requires_same_commit_in_all_verification_reports(
    delivery, target, key, value
):
    paths, _, _, production, browser = delivery
    payload = production if target == "production" else browser
    payload[key] = value
    payload["detail"] = "secret provider exception must not be sent"
    name = "production.json" if target == "production" else "production-browser.json"
    atomic_write_json(paths.root / "audit-output" / name, payload)
    report = write_delivery_report(paths, SHA, "success", now=NOW)
    notice = build_production_notification(report, SHA, RUN, now=NOW)
    assert report["status"] == "failed" and notice.status == "FAILURE"
    assert "secret provider exception" not in notice.message


def test_missing_browser_or_failed_workflow_cannot_become_success(delivery):
    paths, _, _, _, _ = delivery
    assert write_delivery_report(paths, SHA, "failure", now=NOW)["status"] == "failed"
    (paths.root / "audit-output/production-browser.json").unlink()
    assert write_delivery_report(paths, SHA, "success", now=NOW)["status"] == "failed"
    assert build_production_notification({}, SHA, RUN, now=NOW).status == "FAILURE"


def test_superseded_release_is_not_reported_as_verified(delivery):
    paths, _, _, production, _ = delivery
    production.update(status="superseded", deployed_commit="b" * 40)
    atomic_write_json(paths.root / "audit-output/production.json", production)
    report = write_delivery_report(paths, SHA, "success", now=NOW)
    notice = build_production_notification(report, SHA, RUN, now=NOW)
    assert report["status"] == "superseded" and notice.status == "PARTIAL"
    assert "未宣告驗收成功" in notice.message


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"pending_count": False},
        {"pending_count": 0, "priority_count": 0, "background_count": 0, "items": [{}]},
        {"pending_count": 0, "priority_count": 1, "background_count": 0, "items": []},
    ],
)
def test_invalid_pending_inventory_cannot_claim_full_success(delivery, metadata):
    paths, _, _, _, _ = delivery
    report = write_delivery_report(paths, SHA, "success", now=NOW)
    report["metadata_refresh"] = metadata
    assert build_production_notification(report, SHA, RUN, now=NOW).status == "FAILURE"
    report["expected_commit"] = "b" * 40
    assert build_production_notification(report, SHA, RUN, now=NOW).status == "FAILURE"


def test_missing_or_malformed_inventory_is_unavailable_not_zero(project_paths):
    output = project_paths.root / "audit-output"
    atomic_write_json(
        output / "production.json",
        {"status": "verified", "expected_commit": SHA, "deployed_commit": SHA},
    )
    atomic_write_json(
        output / "production-browser.json", {"status": "verified", "commit": SHA}
    )
    report = write_delivery_report(project_paths, SHA, "success", now=NOW)
    assert report["status"] == "failed" and report["metadata_refresh"] is None
    path = output / "delivery.json"
    for content in ("{invalid", "[]", "null"):
        path.write_text(content, encoding="utf-8")
        assert read_report(path) == {}
    with pytest.raises(ValueError, match="full lowercase"):
        write_delivery_report(project_paths, "invalid", "success", now=NOW)


def test_discord_uses_delivery_title_and_does_not_invent_data_change(
    delivery, monkeypatch
):
    paths, _, _, _, _ = delivery
    report = write_delivery_report(paths, SHA, "success", now=NOW)
    notice = build_production_notification(report, SHA, RUN, now=NOW)
    post = Mock(return_value=Mock(status_code=204))
    monkeypatch.setattr("requests.post", post)
    DiscordNotifier("https://discord.example/webhook", required=True).send(notice)
    payload = post.call_args.kwargs["json"]
    assert payload["embeds"][0]["title"] == notice.title
    assert "資料有變更" not in payload["embeds"][0]["description"]
    assert payload["allowed_mentions"] == {"parse": []}
    assert json.loads(json.dumps(payload))["embeds"][0]["color"] == 16753920
