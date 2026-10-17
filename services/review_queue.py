"""Durable deferred evidence and explicitly dated review reminders in Issues."""

from __future__ import annotations

import base64
import json
import os
import re
import zlib
from datetime import date, datetime
from typing import Any

from models import TAIPEI_TZ
from services.review_notifications import (
    BOT,
    KINDS,
    MARKER,
    QUEUE_MARKER,
    GitHubReviewClient,
    publish_cases,
    review_cases,
    review_comments,
)

PAYLOAD = "<!-- queue-payload:"
REMINDER = "<!-- anime-review-reminder:v1:"


def _load_queue(issues: list[dict]) -> tuple[dict | None, dict]:
    matches = [issue for issue in issues if QUEUE_MARKER in (issue.get("body") or "")]
    if len(matches) > 1:
        raise RuntimeError("Multiple review queue issues; refusing to discard evidence")
    if not matches:
        return None, {}
    issue = matches[0]
    match = re.search(r"<!-- queue-payload:([A-Za-z0-9+/=]+) -->", issue["body"])
    if not match:
        raise RuntimeError("Review queue payload missing; refusing to overwrite")
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(base64.b64decode(match[1], validate=True), 2_000_001)
        if len(raw) > 2_000_000 or not decoder.eof or decoder.unused_data:
            raise ValueError("Invalid queue size")
        state = json.loads(raw)
        if state["schema_version"] != 1 or not isinstance(state["pending"], dict):
            raise ValueError("Invalid queue schema")
        for key, entry in state["pending"].items():
            if entry["case"]["key"] != key or entry["case"]["kind"] not in KINDS:
                raise ValueError("Invalid queue identity")
            if datetime.fromisoformat(entry["first_seen_at"]).tzinfo is None:
                raise ValueError("Queue timestamps must include timezone")
        return issue, state["pending"]
    except (ValueError, TypeError, KeyError, zlib.error) as exc:
        raise RuntimeError("Review queue corrupt; refusing to overwrite") from exc


def _save_queue(client: Any, issue: dict | None, pending: dict) -> dict | None:
    if issue is None and not pending:
        return None
    raw = json.dumps(
        {"schema_version": 1, "pending": pending}, ensure_ascii=False, sort_keys=True
    ).encode()
    encoded = base64.b64encode(zlib.compress(raw, 9)).decode()
    if len(raw) > 2_000_000 or len(encoded) > 50_000:
        raise RuntimeError(
            "Review queue exceeds safe Issue size; existing evidence retained"
        )
    body = "\n".join(
        [
            QUEUE_MARKER,
            "",
            f"目前有 **{len(pending)}** 筆尚未發出通知的待辦。",
            "",
            "這是系統維護的持久待辦，不是集數審核；無需回覆或修改本文。",
            "待辦保留首次發現時間與最近可用證據，直到通知完成或取得明確的解決證據。",
            "",
            f"{PAYLOAD}{encoded} -->",
            "",
        ]
    )
    if issue and issue["body"] == body:
        return issue
    data = {
        "title": "[系統] 資料複查持久待辦",
        "body": body,
        "state": "open" if pending else "closed",
    }
    if issue:
        result = client.request("PATCH", f"/issues/{issue['number']}", json=data)
        return {**issue, **data, **result}
    result = client.request(
        "POST", "/issues", json={"title": data["title"], "body": body}
    )
    return {**data, **result}


def _explicitly_resolved(case: dict, report: dict) -> bool:
    if case["kind"] == "source_unavailable":
        return not report["errors"]
    row = next(
        (
            row
            for row in report["identities"]
            if (row["card_id"], row["original_name"], row["bangumi_subject_id"])
            == (case["card_id"], case["original_name"], case["bangumi_subject_id"])
        ),
        None,
    )
    if not row:
        return False
    if case["kind"] == "snapshot_due":
        review = row.get("manual_review")
        return bool(
            review
            and not review["due"]
            and review.get("last_verified_at")
            and review["last_verified_at"] != case["evidence"].get("last_verified_at")
        )
    if case["kind"] == "confirmed_total_disagreement":
        corroborated = {
            s["quarter"]
            for s in row["secondary"]
            if s["result"] == "corroborated" and s.get("checked_at")
        }
        return set(case["quarters"]) <= corroborated
    if case["kind"] == "identity_conflict":
        checked = {
            q["quarter"]
            for q in report["quarters"]
            if q.get("checked_at") and q["result"] != "error"
        }
        ids = row.get("explicit_anilist_ids", [])
        if len(ids) != 1:
            return False
        # A missing source link leaves the stored ID as the only candidate;
        # that is not evidence that the earlier conflict was resolved.
        verified = {
            observation["quarter"]
            for observation in row.get("primary_observations", [])
            if (link := observation.get("source_link_observation"))
            and link.get("state") == "verified"
            and link.get("checked_at")
            and link.get("anilist_id") == ids[0]
        }
        return set(case["quarters"]) <= checked & verified
    return False


def remind_waiting_reviews(
    client: Any, issues: list[dict], *, assignee: str, today: date, max_changes: int
) -> tuple[int, int]:
    sent = deferred = 0
    for issue in sorted(issues, key=lambda item: item["number"]):
        if MARKER not in (issue.get("body") or ""):
            continue
        comments = review_comments(client, issue["number"])
        decisions = [
            c
            for c in comments
            if c.get("user", {}).get("login", "").casefold() == assignee.casefold()
            and re.match(r"^\s*[ABC](?:\s|[：:])", c.get("body", ""))
        ]
        if not decisions:
            continue
        latest = max(decisions, key=lambda c: c["id"])
        match = re.match(
            r"^\s*C[：:\s]+(?:待查[：:\s]*)?(\d{4}-\d{2}-\d{2})(?:\s|$)",
            latest.get("body", ""),
        )
        if not match:
            continue
        try:
            due = date.fromisoformat(match[1])
        except ValueError:
            continue
        marker = f"{REMINDER}{latest['id']}:{due.isoformat()} -->"
        if due > today or any(
            c.get("user", {}).get("login") == BOT and marker in c.get("body", "")
            for c in comments
        ):
            continue
        if sent >= max_changes:
            deferred += 1
            continue
        if issue.get("state") == "closed":
            client.request(
                "PATCH", f"/issues/{issue['number']}", json={"state": "open"}
            )
        client.request(
            "POST",
            f"/issues/{issue['number']}/comments",
            json={
                "body": f"{marker}\n\n@{assignee} 已到你指定的複查日期 {due.isoformat()}。請核對原 Issue 的來源與證據；若仍需等待，可回覆 `C 待查 YYYY-MM-DD` 指定新日期。尚未修改公開資料。"
            },
        )
        sent += 1
    return sent, deferred


def publish_review_queue(
    report: dict, client: Any, *, assignee: str, run_url: str, max_changes: int = 10
) -> dict:
    if report.get("schema_version") != 1 or report.get("mode") != "read_only":
        raise ValueError("Unsupported source audit report")
    # Validate destinations before any persistent or notification writes.
    publish_cases([], client, assignee=assignee, run_url=run_url)
    current = {case["key"]: case for case in review_cases(report)}
    existing = client.issues()
    queue_issue, pending = _load_queue(existing)
    for key, entry in list(pending.items()):
        if key not in current and _explicitly_resolved(entry["case"], report):
            del pending[key]
    for key, case in current.items():
        first = pending.get(key, {}).get("first_seen_at", report["observed_at"])
        pending[key] = {
            "case": case,
            "first_seen_at": first,
            "evidence_run_url": run_url,
        }
    # Persist before sending: a crash or a notification limit cannot erase work.
    queue_issue = _save_queue(client, queue_issue, pending)
    reminders, reminders_deferred = remind_waiting_reviews(
        client,
        existing,
        assignee=assignee,
        today=datetime.fromisoformat(report["observed_at"])
        .astimezone(TAIPEI_TZ)
        .date(),
        max_changes=max_changes,
    )
    ordered = sorted(
        pending.values(),
        key=lambda entry: (
            entry["case"]["kind"] != "source_unavailable",
            entry["first_seen_at"],
            entry["case"]["key"],
        ),
    )
    totals = {"created": 0, "updated": 0, "unchanged": 0, "deferred": 0}
    remaining = max_changes - reminders
    for entry in ordered:
        result, handled = publish_cases(
            [entry["case"]],
            client,
            assignee=assignee,
            run_url=entry["evidence_run_url"],
            max_changes=remaining,
            existing=existing,
        )
        for key in totals:
            totals[key] += result[key]
        remaining -= result["created"] + result["updated"]
        for key in handled:
            pending.pop(key, None)
    _save_queue(client, queue_issue, pending)
    return {
        "candidates": len(current),
        **totals,
        "pending": len(pending),
        "reminded": reminders,
        "reminders_deferred": reminders_deferred,
    }


def remind_from_environment() -> dict:
    repository = os.environ["GITHUB_REPOSITORY"]
    assignee = os.getenv("SOURCE_REVIEW_ASSIGNEE") or repository.split("/")[0]
    run_url = (
        f"https://github.com/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    )
    client = GitHubReviewClient(repository, os.environ["GH_TOKEN"])
    publish_cases([], client, assignee=assignee, run_url=run_url)
    reminded, deferred = remind_waiting_reviews(
        client,
        client.issues(),
        assignee=assignee,
        today=datetime.now(TAIPEI_TZ).date(),
        max_changes=10,
    )
    return {"reminded": reminded, "deferred": deferred}
