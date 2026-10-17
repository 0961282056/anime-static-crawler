from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from models import TAIPEI_TZ
from services.review_notifications import (
    BOT,
    MARKER,
    GitHubReviewClient,
    issue_body,
    publish_reviews,
    review_cases,
)

NOW = datetime(2026, 10, 16, tzinfo=TAIPEI_TZ)
RUN = "https://github.com/0961282056/anime-static-crawler/actions/runs/123"


@pytest.fixture
def report():
    return {
        "schema_version": 1,
        "mode": "read_only",
        "observed_at": NOW.isoformat(),
        "errors": [],
        "identities": [
            {
                "card_id": "anime-1",
                "anime_name": "測試作品",
                "original_name": "作品",
                "bangumi_subject_id": 10,
                "quarters": ["2026_秋"],
                "manual_review": None,
                "primary_observations": [
                    {
                        "quarter": "2026_秋",
                        "total_episodes": 12,
                        "fetch_state": "ok",
                        "episode_count_status": "confirmed",
                        "provenance": {
                            "last_verified_at": (NOW - timedelta(days=1)).isoformat(),
                            "source_urls": ["https://bgm.tv/subject/10"],
                        },
                    }
                ],
                "secondary": [
                    {
                        "quarter": "2026_秋",
                        "result": "difference",
                        "checked_at": NOW.isoformat(),
                        "reasons": ["total_disagreement"],
                        "observation": {"total_episodes": 16},
                        "source_url": "https://anilist.co/anime/20",
                    }
                ],
            }
        ],
    }


class Client:
    def __init__(self, existing=()):
        self.existing = list(existing)
        self.calls = []

    def issues(self):
        return self.existing

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs.get("json", kwargs.get("params", {}))))
        return [] if method == "GET" else {"number": 1}


def publish(report, client, **kwargs):
    return publish_reviews(report, client, assignee="0961282056", run_url=RUN, **kwargs)


def test_confirmed_fresh_conflict_produces_readable_review_and_assignment(report):
    original = deepcopy(report)
    client = Client()
    assert publish(report, client)["created"] == 1
    method, path, data = client.calls[0]
    assert (method, path) == ("POST", "/issues")
    assert data["assignees"] == ["0961282056"]
    assert "| 2026_秋 | 12 | 16 |" in data["body"]
    assert "https://bgm.tv/subject/10" in data["body"]
    assert "A：保留" in data["body"] and "B：建議修正" in data["body"]
    assert "不會自動改寫" in data["body"]
    assert report == original


@pytest.mark.parametrize(
    ("field", "value"), [("episode_count_status", "planned"), ("fetch_state", "stale")]
)
def test_unconfirmed_or_failed_primary_is_not_a_user_decision(report, field, value):
    report["identities"][0]["primary_observations"][0][field] = value
    assert publish(report, Client())["candidates"] == 0


def test_stale_primary_and_routine_secondary_flags_stay_in_report(report):
    report["identities"][0]["primary_observations"][0]["provenance"][
        "last_verified_at"
    ] = (NOW - timedelta(days=8)).isoformat()
    assert review_cases(report) == []
    report["identities"][0]["secondary"][0].update(
        result="needs_identity_or_scope_review", reasons=["original_title_not_exact"]
    )
    assert review_cases(report) == []


@pytest.mark.parametrize("state", ["open", "closed"])
def test_same_evidence_never_repeats_even_with_new_run_or_timestamp(report, state):
    case = review_cases(report)[0]
    client = Client([{"number": 5, "state": state, "body": issue_body(case, RUN)}])
    report["observed_at"] = (NOW + timedelta(days=1)).isoformat()
    report["identities"][0]["primary_observations"][0]["provenance"][
        "last_verified_at"
    ] = NOW.isoformat()
    assert publish(report, client)["unchanged"] == 1
    assert client.calls == []


def test_changed_evidence_comments_and_reopens_same_issue(report):
    case = review_cases(report)[0]
    client = Client([{"number": 5, "state": "closed", "body": issue_body(case, RUN)}])
    report["identities"][0]["secondary"][0]["observation"]["total_episodes"] = 24
    assert publish(report, client)["updated"] == 1
    assert client.calls[0][:2] == ("GET", "/issues/5/comments")
    assert client.calls[1][:2] == ("POST", "/issues/5/comments")
    assert "@0961282056" in client.calls[1][2]["body"]
    assert client.calls[2][2]["state"] == "open"


def test_provider_failure_is_operations_issue_not_episode_approval(report):
    report["identities"] = []
    report["errors"] = [{"stage": "anilist", "reason": "http_403", "subject_ids": [20]}]
    client = Client()
    assert publish(report, client)["created"] == 1
    body = client.calls[0][2]["body"]
    assert "不需要判斷集數" in body
    assert "http_403" in body
    assert "B：建議修正" not in body
    case = review_cases(report)[0]
    report["errors"][0]["subject_ids"] = [21, 22]
    assert review_cases(report)[0]["fingerprint"] == case["fingerprint"]


def test_expired_snapshot_and_id_conflicts_have_official_links(report):
    row = report["identities"][0]
    row["secondary"] = []
    row["manual_review"] = {
        "due": True,
        "last_verified_at": NOW.isoformat(),
        "next_review_at": NOW.isoformat(),
        "scope_explanation": "TV 版，不含網路篇",
        "source_urls": ["https://official.example/story/"],
    }
    row["secondary_unavailable_reason"] = "conflicting_explicit_ids"
    row["explicit_anilist_ids"] = [20, 21]
    cases = review_cases(report)
    assert {case["kind"] for case in cases} == {"snapshot_due", "identity_conflict"}
    assert any(
        "https://official.example/story/" in issue_body(case, RUN) for case in cases
    )
    assert any("https://anilist.co/anime/21" in issue_body(case, RUN) for case in cases)


def test_notification_budget_and_untrusted_mentions(report):
    report["errors"] = [{"stage": "anilist", "reason": "http_403"}]
    report["identities"][0]["anime_name"] = "@everyone <script>"
    client = Client()
    result = publish(report, client, max_changes=1)
    assert result["created"] == 1 and result["deferred"] == 1
    assert "維運" in client.calls[0][2]["title"]
    case = next(
        case for case in review_cases(report) if case["kind"] != "source_unavailable"
    )
    assert "@everyone" not in issue_body(case, RUN)
    assert "<script>" not in issue_body(case, RUN)


def test_failed_comment_does_not_mark_new_evidence_as_notified(report):
    client = Client(
        [{"number": 5, "body": f"{MARKER}{review_cases(report)[0]['key']}:old -->"}]
    )

    def fail(method, path, **kwargs):
        if method == "GET":
            return []
        raise RuntimeError("HTTP 403")

    client.request = fail
    with pytest.raises(RuntimeError):
        publish(report, client)
    assert client.existing[0]["body"].endswith(":old -->")


def test_github_listing_uses_only_bot_issues_and_bounds_pagination(monkeypatch):
    client = GitHubReviewClient("owner/repo", "injected-test-token")
    rows = [
        {"user": {"login": BOT}, "number": 1, "body": MARKER},
        {"user": {"login": "human"}, "body": MARKER},
        {"user": {"login": BOT}, "body": MARKER, "pull_request": {}},
    ]
    monkeypatch.setattr(client, "request", lambda *a, **k: rows)
    assert [issue["number"] for issue in client.issues()] == [1]
    monkeypatch.setattr(client, "request", lambda *a, **k: [{}] * 100)
    with pytest.raises(RuntimeError, match="budget exhausted"):
        client.issues()


def test_invalid_run_or_assignee_and_duplicate_markers_fail_closed(report):
    with pytest.raises(ValueError):
        publish_reviews(report, Client(), assignee="@someone", run_url=RUN)
    with pytest.raises(ValueError):
        publish_reviews(
            report, Client(), assignee="owner", run_url="https://example.com/"
        )
    issue = {"number": 1, "body": issue_body(review_cases(report)[0], RUN)}
    with pytest.raises(RuntimeError, match="Duplicate"):
        publish(report, Client([issue, issue]))


def test_incomplete_comment_listing_does_not_send_or_update_evidence(report):
    case = review_cases(report)[0]
    client = Client([{"number": 5, "state": "closed", "body": issue_body(case, RUN)}])
    report["identities"][0]["secondary"][0]["observation"]["total_episodes"] = 24
    reads = []

    def endless_comments(method, path, **kwargs):
        reads.append((method, kwargs["params"]["page"]))
        return [{"id": i, "body": "", "user": {"login": "human"}} for i in range(100)]

    client.request = endless_comments
    with pytest.raises(RuntimeError, match="pagination budget exhausted"):
        publish(report, client)
    assert reads == [("GET", n) for n in range(1, 6)]
    assert client.existing[0]["state"] == "closed"
