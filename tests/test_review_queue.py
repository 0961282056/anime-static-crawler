from __future__ import annotations

from copy import deepcopy
from datetime import datetime

import pytest

from models import TAIPEI_TZ
from services.review_notifications import (
    BOT,
    MARKER,
    QUEUE_MARKER,
    issue_body,
    review_cases,
)
from services.review_queue import _load_queue, publish_review_queue

NOW = datetime(2026, 10, 16, tzinfo=TAIPEI_TZ)
RUN = "https://github.com/0961282056/anime-static-crawler/actions/runs/123"


def report(count=0):
    rows = []
    for index in range(count):
        rows.append(
            {
                "card_id": f"anime-{index}",
                "anime_name": f"作品 {index}",
                "original_name": f"作品{index}",
                "bangumi_subject_id": index + 1,
                "quarters": ["2026_秋"],
                "manual_review": {
                    "due": True,
                    "last_verified_at": "2026-10-01T00:00:00+08:00",
                    "next_review_at": "2026-10-08T00:00:00+08:00",
                    "source_urls": ["https://official.example/story/"],
                    "scope_explanation": "TV版",
                },
                "secondary": [],
                "primary_observations": [],
            }
        )
    return {
        "schema_version": 1,
        "mode": "read_only",
        "observed_at": NOW.isoformat(),
        "identities": rows,
        "quarters": [],
        "errors": [],
    }


class GitHub:
    def __init__(self):
        self.rows = {}
        self.comments = {}
        self.calls = []
        self.fail_notification = False

    def issues(self):
        return deepcopy(list(self.rows.values()))

    def request(self, method, path, **kwargs):
        data = kwargs.get("json", {})
        self.calls.append((method, path, deepcopy(data)))
        if method == "GET":
            return deepcopy(self.comments.get(int(path.split("/")[2]), []))
        if method == "POST" and path == "/issues":
            if self.fail_notification and QUEUE_MARKER not in data["body"]:
                raise RuntimeError("temporary write failure")
            number = len(self.rows) + 1
            row = {"number": number, "state": "open", "user": {"login": BOT}, **data}
            self.rows[number] = deepcopy(row)
            return row
        number = int(path.split("/")[2])
        if method == "PATCH":
            self.rows[number].update(data)
            return deepcopy(self.rows[number])
        comment = {
            "id": sum(map(len, self.comments.values())) + 100,
            "user": {"login": BOT},
            **data,
        }
        self.comments.setdefault(number, []).append(comment)
        return comment


def publish(data, client, **kwargs):
    return publish_review_queue(data, client, assignee="owner", run_url=RUN, **kwargs)


def test_deferred_evidence_survives_new_report_without_original_candidate():
    client = GitHub()
    result = publish(report(3), client, max_changes=1)
    assert result["created"] == 1 and result["pending"] == 2
    queue, pending = _load_queue(client.issues())
    evidence = deepcopy(pending)
    assert len(pending) == 2
    later = report()
    later["observed_at"] = "2026-11-01T00:00:00+08:00"
    result = publish(later, client, max_changes=1)
    assert result["created"] == 1 and result["pending"] == 1
    _, remaining = _load_queue(client.issues())
    key = next(iter(remaining))
    assert remaining[key] == evidence[key]
    assert queue["number"] == _load_queue(client.issues())[0]["number"]


def test_crash_after_persisting_queue_does_not_lose_candidate():
    client = GitHub()
    client.fail_notification = True
    with pytest.raises(RuntimeError, match="temporary"):
        publish(report(1), client)
    assert len(_load_queue(client.issues())[1]) == 1
    client.fail_notification = False
    assert publish(report(), client)["created"] == 1
    assert _load_queue(client.issues())[1] == {}
    assert publish(report(1), client)["unchanged"] == 1


def test_only_explicit_reverification_clears_a_deferred_snapshot():
    client = GitHub()
    publish(report(1), client, max_changes=0)
    data = report(1)
    data["identities"][0]["manual_review"].update(
        due=False, last_verified_at=NOW.isoformat()
    )
    result = publish(data, client)
    assert result["created"] == 0 and result["pending"] == 0
    assert not any(MARKER in row["body"] for row in client.rows.values())


def test_invalid_queue_is_not_silently_overwritten():
    client = GitHub()
    client.rows[1] = {"number": 1, "body": QUEUE_MARKER + " damaged", "state": "open"}
    with pytest.raises(RuntimeError, match="missing"):
        publish(report(1), client)
    assert client.calls == []


def seed_review(client, comment="C 待查 2026-10-20", author="owner"):
    case = review_cases(report(1))[0]
    client.rows[1] = {"number": 1, "state": "closed", "body": issue_body(case, RUN)}
    client.comments[1] = [{"id": 50, "user": {"login": author}, "body": comment}]


def test_dated_waiting_reply_reopens_and_reminds_once():
    client = GitHub()
    seed_review(client)
    assert publish(report(), client)["reminded"] == 0
    due = report()
    due["observed_at"] = "2026-10-20T00:00:00+08:00"
    assert publish(due, client)["reminded"] == 1
    assert client.rows[1]["state"] == "open"
    assert "@owner" in client.comments[1][-1]["body"]
    assert publish(due, client)["reminded"] == 0


@pytest.mark.parametrize(
    ("comment", "author"),
    [
        ("C 待查 2026-10-01", "stranger"),
        ("C 待查 2026-99-99", "owner"),
        ("C 待查 明天", "owner"),
    ],
)
def test_untrusted_or_invalid_reminder_commands_do_not_notify(comment, author):
    client = GitHub()
    seed_review(client, comment, author)
    assert publish(report(), client)["reminded"] == 0
    assert client.calls == [("GET", "/issues/1/comments", {})]


def test_newer_keep_decision_cancels_an_older_waiting_date():
    client = GitHub()
    seed_review(client, "C 待查 2026-10-01")
    client.comments[1].append(
        {"id": 51, "user": {"login": "owner"}, "body": "A 保留目前資料"}
    )
    assert publish(report(), client)["reminded"] == 0


def test_no_candidates_never_creates_a_system_queue_issue():
    client = GitHub()
    assert publish(report(), client)["pending"] == 0
    assert client.rows == {}


def test_reopen_failure_does_not_mark_a_reminder_as_delivered():
    client = GitHub()
    seed_review(client, "C 待查 2026-10-01")
    original = client.request

    def fail_reopen(method, path, **kwargs):
        if method == "PATCH":
            raise RuntimeError("reopen failed")
        return original(method, path, **kwargs)

    client.request = fail_reopen
    with pytest.raises(RuntimeError, match="reopen failed"):
        publish(report(), client)
    assert len(client.comments[1]) == 1
    client.request = original
    assert publish(report(), client)["reminded"] == 1
    assert client.rows[1]["state"] == "open"


@pytest.mark.parametrize("failure", ["patch", "post_response"])
def test_sent_notification_survives_partial_failure_without_duplicate(failure):
    client = GitHub()
    seed_review(client, comment="A 保留")
    data = report(1)
    data["identities"][0]["manual_review"]["scope_explanation"] = "新證據"
    original = client.request

    def fail_after_delivery(method, path, **kwargs):
        if failure == "patch" and method == "PATCH" and path == "/issues/1":
            raise RuntimeError("patch failed")
        result = original(method, path, **kwargs)
        if (
            failure == "post_response"
            and method == "POST"
            and path.endswith("/comments")
        ):
            raise RuntimeError("comment response lost after delivery")
        return result

    client.request = fail_after_delivery
    with pytest.raises(RuntimeError):
        publish(data, client)
    assert len(_load_queue(client.issues())[1]) == 1
    assert sum(c["user"]["login"] == BOT for c in client.comments[1]) == 1

    client.request = original
    result = publish(data, client)
    assert result["updated"] == 1 and result["pending"] == 0
    assert client.rows[1]["state"] == "open"
    assert sum(c["user"]["login"] == BOT for c in client.comments[1]) == 1
    assert publish(data, client)["unchanged"] == 1


def test_human_comment_cannot_claim_evidence_has_already_been_notified():
    client = GitHub()
    seed_review(client, comment="A 保留")
    data = report(1)
    data["identities"][0]["manual_review"]["scope_explanation"] = "新證據"
    body = issue_body(review_cases(data)[0], RUN)
    client.comments[1].append({"id": 60, "user": {"login": "human"}, "body": body})
    assert publish(data, client)["updated"] == 1
    assert sum(c["user"]["login"] == BOT for c in client.comments[1]) == 1


def test_failed_comment_lookup_keeps_evidence_pending_without_sending():
    client = GitHub()
    seed_review(client, comment="A 保留")
    data = report(1)
    data["identities"][0]["manual_review"]["scope_explanation"] = "新證據"
    old_body = client.rows[1]["body"]
    original = client.request

    def fail_lookup(method, path, **kwargs):
        if method == "GET" and path.endswith("/comments"):
            raise RuntimeError("comment lookup unavailable")
        return original(method, path, **kwargs)

    client.request = fail_lookup
    with pytest.raises(RuntimeError, match="lookup unavailable"):
        publish(data, client)
    assert len(_load_queue(client.issues())[1]) == 1
    assert client.rows[1]["body"] == old_body
    assert not any(c["user"]["login"] == BOT for c in client.comments[1])


def test_evidence_returning_to_old_fingerprint_sends_new_notification():
    client = GitHub()
    seed_review(client, comment="A 保留")
    original = report(1)
    changed = deepcopy(original)
    changed["identities"][0]["manual_review"]["scope_explanation"] = "另一組證據"
    for data in (changed, original, changed):
        assert publish(data, client)["updated"] == 1
    assert sum(c["user"]["login"] == BOT for c in client.comments[1]) == 3
    assert publish(changed, client)["unchanged"] == 1


@pytest.mark.parametrize(
    "link",
    [
        None,
        {"state": "verified", "checked_at": NOW.isoformat(), "anilist_id": None},
        {"state": "source_card_absent", "checked_at": None, "anilist_id": None},
        {"state": "source_identity_changed", "checked_at": None, "anilist_id": None},
        {"state": "verified", "checked_at": None, "anilist_id": 20},
        {"state": "verified", "checked_at": NOW.isoformat(), "anilist_id": 21},
    ],
)
def test_missing_or_unbound_source_link_cannot_resolve_deferred_conflict(link):
    client = GitHub()
    data = report(1)
    row = data["identities"][0]
    row.update(
        manual_review=None,
        secondary_unavailable_reason="conflicting_explicit_ids",
        explicit_anilist_ids=[20, 21],
    )
    assert publish(data, client, max_changes=0)["pending"] == 1
    before = deepcopy(_load_queue(client.issues())[1])
    row.pop("secondary_unavailable_reason")
    row["explicit_anilist_ids"] = [20]
    row["primary_observations"] = [
        {"quarter": "2026_秋", "total_episodes": 12, "source_link_observation": link}
    ]
    data["quarters"] = [
        {"quarter": "2026_秋", "checked_at": NOW.isoformat(), "result": "matched"}
    ]
    assert publish(data, client, max_changes=0)["pending"] == 1
    assert _load_queue(client.issues())[1] == before
    assert not any(MARKER in issue["body"] for issue in client.rows.values())


@pytest.mark.parametrize("verified_quarters", [1, 2])
def test_conflict_resolution_requires_explicit_links_for_every_original_quarter(
    verified_quarters,
):
    client = GitHub()
    data = report(1)
    row = data["identities"][0]
    row.update(
        manual_review=None,
        quarters=["2026_夏", "2026_秋"],
        secondary_unavailable_reason="conflicting_explicit_ids",
        explicit_anilist_ids=[20, 21],
    )
    assert publish(data, client, max_changes=0)["pending"] == 1
    row.pop("secondary_unavailable_reason")
    row["explicit_anilist_ids"] = [20]
    row["primary_observations"] = [
        {
            "quarter": quarter,
            "total_episodes": 12,
            "source_link_observation": {
                "state": "verified",
                "checked_at": NOW.isoformat(),
                "anilist_id": 20,
            },
        }
        for quarter in row["quarters"][:verified_quarters]
    ]
    data["quarters"] = [
        {"quarter": q, "checked_at": NOW.isoformat(), "result": "matched"}
        for q in row["quarters"]
    ]
    result = publish(data, client, max_changes=0)
    assert result["pending"] == (1 if verified_quarters == 1 else 0)
    assert not any(MARKER in issue["body"] for issue in client.rows.values())
