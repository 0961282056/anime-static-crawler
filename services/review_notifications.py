"""Evidence-based GitHub review issues; responses never mutate public data."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from typing import Any

import requests

from services.response_limits import bounded_response

MARKER = "<!-- anime-source-review:v1:"
QUEUE_MARKER = "<!-- anime-source-review-queue:v1 -->"
BOT = "github-actions[bot]"
KINDS = {
    "snapshot_due": "官方快照複查到期",
    "identity_conflict": "作品連結衝突",
    "confirmed_total_disagreement": "已確認總集數不一致",
    "source_unavailable": "來源連線異常（維運）",
}


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def review_cases(report: dict) -> list[dict]:
    """Skip routine missing links, formatting differences and provider failures."""
    observed = datetime.fromisoformat(report["observed_at"])
    if observed.tzinfo is None:
        raise ValueError("Report observation time must include a timezone")
    cases = []
    for row in report["identities"]:
        base = {
            "card_id": row["card_id"],
            "anime_name": row["anime_name"],
            "original_name": row["original_name"],
            "bangumi_subject_id": row["bangumi_subject_id"],
            "quarters": sorted(row["quarters"]),
        }
        manual = row.get("manual_review")
        if manual and manual["due"]:
            cases.append(
                {
                    **base,
                    "kind": "snapshot_due",
                    "evidence": manual,
                    "recommendation": "請重新核對下列官方來源；原資料先保留。到期本身不代表集數錯誤。",
                }
            )
        if row.get("secondary_unavailable_reason") == "conflicting_explicit_ids":
            cases.append(
                {
                    **base,
                    "kind": "identity_conflict",
                    "evidence": {"explicit_anilist_ids": row["explicit_anilist_ids"]},
                    "recommendation": "同一作品的明確 AniList 連結互相矛盾，先確認是否不同季度或版本；不任選其中一個。",
                }
            )
        primary = {p["quarter"]: p for p in row["primary_observations"]}
        totals = {p["total_episodes"] for p in primary.values()}
        comparisons = []
        for comparison in row["secondary"]:
            p = primary[comparison["quarter"]]
            checked = p["provenance"].get("last_verified_at")
            if not checked:
                continue
            verified = datetime.fromisoformat(checked)
            if verified.tzinfo is None:
                continue
            if (
                comparison["result"] == "difference"
                and "total_disagreement" in comparison["reasons"]
                and comparison.get("checked_at")
                and p.get("fetch_state") == "ok"
                and p.get("episode_count_status") == "confirmed"
                and len(totals) == 1
                and timedelta(0) <= observed - verified <= timedelta(days=7)
            ):
                comparisons.append(
                    {
                        "quarter": comparison["quarter"],
                        "primary_total": p["total_episodes"],
                        "primary_sources": p["provenance"]["source_urls"],
                        "secondary_total": comparison["observation"]["total_episodes"],
                        "secondary_source": comparison["source_url"],
                    }
                )
        if comparisons:
            cases.append(
                {
                    **base,
                    "kind": "confirmed_total_disagreement",
                    "evidence": comparisons,
                    "recommendation": "先保留目前已確認集數；請核對電視版、網路版、分割季度或特別篇範圍，再決定是否修改。",
                }
            )
    for case in cases:
        case["key"] = _digest(
            [
                case["card_id"],
                case["original_name"],
                case["bangumi_subject_id"],
                case["kind"],
            ]
        )[:24]
        case["fingerprint"] = _digest(case)
    if report.get("errors"):
        failures = sorted(
            {(error["stage"], error["reason"]) for error in report["errors"]}
        )
        case = {
            "card_id": "source-health",
            "anime_name": "資料來源連線",
            "kind": "source_unavailable",
            "evidence": failures,
            "recommendation": "這是維運問題，不需要判斷集數。請維護者查看來源回應、執行環境與報告，修復後重跑；保留既有公開資料。",
            "key": "source-health",
        }
        case["fingerprint"] = _digest(case)
        cases.append(case)
    return sorted(
        cases, key=lambda case: (case["kind"] != "source_unavailable", case["key"])
    )


def _safe_text(value: str) -> str:
    return value.replace("@", "＠").replace("<", "&lt;").replace(">", "&gt;")


def issue_body(case: dict, run_url: str) -> str:
    marker = f"{MARKER}{case['key']}:{case['fingerprint']} -->"
    lines = [
        marker,
        "",
        f"**{_safe_text(case['anime_name'])}：{KINDS[case['kind']]}**",
        "",
        "尚未修改公開資料。",
        "",
    ]
    evidence = case["evidence"]
    links = set()
    if case["kind"] == "source_unavailable":
        lines.extend(f"- {stage}：`{reason}`" for stage, reason in evidence)
    else:
        lines.extend(
            [
                f"- 原文名稱：{_safe_text(case['original_name'] or '未提供')}",
                f"- 收錄季度：{', '.join(case['quarters'])}",
                "",
            ]
        )
        if case["kind"] == "snapshot_due":
            lines.extend(
                [
                    f"- 上次成功查證：{evidence['last_verified_at'] or '未知'}",
                    f"- 預定複查：{evidence['next_review_at'] or '尚未查證'}",
                    "",
                    _safe_text(evidence["scope_explanation"]),
                ]
            )
            links.update(evidence["source_urls"])
        elif case["kind"] == "identity_conflict":
            lines.append("同一作品出現不同的 AniList 連結，需先確認季度與版本：")
            links.update(
                f"https://anilist.co/anime/{subject}"
                for subject in evidence["explicit_anilist_ids"]
            )
        else:
            lines.extend(
                ["| 季度 | 目前已確認總數 | AniList 總數 |", "|---|---:|---:|"]
            )
            for item in evidence:
                lines.append(
                    f"| {item['quarter']} | {item['primary_total']} | {item['secondary_total']} |"
                )
                links.update(item["primary_sources"])
                links.add(item["secondary_source"])
    if links:
        lines.extend(["", "### 來源連結", ""])
        lines.extend(
            f"- <{url}>"
            for url in sorted(links)
            if re.fullmatch(r"https://[^\s<>]+", url)
        )
    lines.extend(
        [
            "",
            "### 建議",
            "",
            case["recommendation"],
            "",
            "### 如何回覆",
            "",
            "請留言選擇一項，並附來源網址與理由：",
            "",
            "- A：保留目前資料，說明適用的作品／集數範圍。",
            "- B：建議修正，列出正確值、版本範圍及官方證據。",
            "- C：證據不足，維持待查；可說明預計何時再檢查。",
            "",
            "回覆不會自動改寫資料。維護者須將決定寫入範圍規則或資料修正，經測試與 PR 後套用，再關閉此 Issue。",
            "關閉後相同證據不會重新提醒；證據改變才重新開啟。",
            "",
            f"[開啟這次核對報告與 artifact]({run_url})",
            "",
        ]
    )
    if case["kind"] == "source_unavailable":
        lines = lines[: lines.index("### 如何回覆")] + [
            "修復後請重跑來源核對並在此回報結果，再關閉 Issue。無需選擇集數。",
            "",
            f"[開啟核對報告與 artifact]({run_url})",
            "",
        ]
    return "\n".join(lines)


class GitHubReviewClient:
    def __init__(self, repository: str, token: str, session: Any = None) -> None:
        if (
            not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
            or not token
        ):
            raise ValueError("A repository and injected GitHub token are required")
        self.base = f"https://api.github.com/repos/{repository}"
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def request(self, method: str, path: str, **kwargs) -> Any:
        try:
            response = self.session.request(
                method,
                self.base + path,
                timeout=(5, 20),
                allow_redirects=False,
                stream=True,
                hooks={"response": bounded_response(16 * 1024 * 1024)},
                **kwargs,
            )
            try:
                if response.status_code not in {200, 201}:
                    raise RuntimeError(f"Review issue API HTTP {response.status_code}")
                return response.json()
            finally:
                response.close()
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError("Review issue API transport or JSON failure") from exc

    def issues(self) -> list[dict]:
        result = []
        for page in range(1, 11):
            batch = self.request(
                "GET", "/issues", params={"state": "all", "per_page": 100, "page": page}
            )
            if not isinstance(batch, list):
                raise RuntimeError("Invalid issue listing")
            result.extend(
                issue
                for issue in batch
                if "pull_request" not in issue
                and issue.get("user", {}).get("login") == BOT
                and any(
                    marker in (issue.get("body") or "")
                    for marker in (MARKER, QUEUE_MARKER)
                )
            )
            if len(batch) < 100:
                return result
        raise RuntimeError(
            "Issue listing budget exhausted; refusing to create possible duplicates"
        )


def review_comments(client: Any, number: int) -> list[dict]:
    result = []
    for page in range(1, 6):
        batch = client.request(
            "GET", f"/issues/{number}/comments", params={"per_page": 100, "page": page}
        )
        if not isinstance(batch, list):
            raise RuntimeError("Invalid review comment response")
        result.extend(batch)
        if len(batch) < 100:
            return result
    raise RuntimeError("Review comment pagination budget exhausted")


def publish_cases(
    cases: list[dict],
    client: Any,
    *,
    assignee: str,
    run_url: str,
    max_changes: int = 10,
    existing: list[dict] | None = None,
) -> tuple[dict, set[str]]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", assignee):
        raise ValueError("Invalid review assignee")
    if not re.fullmatch(
        r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/[0-9]+",
        run_url,
    ):
        raise ValueError("A canonical workflow run URL is required")
    result = {
        "candidates": len(cases),
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "deferred": 0,
    }
    handled: set[str] = set()
    if not cases:
        return result, handled
    existing = client.issues() if existing is None else existing
    for case in cases:
        prefix = f"{MARKER}{case['key']}:"
        matches = [issue for issue in existing if prefix in (issue.get("body") or "")]
        if len(matches) > 1:
            raise RuntimeError(
                "Duplicate review markers; manual investigation required"
            )
        issue = matches[0] if matches else None
        marker = f"{prefix}{case['fingerprint']} -->"
        if issue and marker in issue["body"]:
            result["unchanged"] += 1
            handled.add(case["key"])
            continue
        if result["created"] + result["updated"] >= max_changes:
            result["deferred"] += 1
            continue
        body = issue_body(case, run_url)
        title = _safe_text(
            f"[資料複查] {case['anime_name']}：{KINDS[case['kind']]}"
        ).replace("\n", " ")[:180]
        if issue:
            number = issue["number"]
            # A previous POST may have succeeded before its response or the
            # following PATCH failed. Reconcile trusted comments first.
            latest_notification = max(
                (
                    comment
                    for comment in review_comments(client, number)
                    if comment.get("user", {}).get("login") == BOT
                    and prefix in comment.get("body", "")
                ),
                key=lambda comment: comment["id"],
                default=None,
            )
            notified = (
                latest_notification is not None
                and marker in latest_notification.get("body", "")
            )
            if not notified:
                client.request(
                    "POST",
                    f"/issues/{number}/comments",
                    json={
                        "body": f"@{assignee} 核對證據已有變更，請重新審查。\n\n" + body
                    },
                )
            client.request(
                "PATCH",
                f"/issues/{number}",
                json={
                    "title": title,
                    "body": body,
                    "state": "open",
                    "assignees": [assignee],
                },
            )
            result["updated"] += 1
        else:
            client.request(
                "POST",
                "/issues",
                json={"title": title, "body": body, "assignees": [assignee]},
            )
            result["created"] += 1
        handled.add(case["key"])
    return result, handled


def publish_reviews(
    report: dict, client: Any, *, assignee: str, run_url: str, max_changes: int = 10
) -> dict:
    if report.get("schema_version") != 1 or report.get("mode") != "read_only":
        raise ValueError("Unsupported source audit report")
    result, _ = publish_cases(
        review_cases(report),
        client,
        assignee=assignee,
        run_url=run_url,
        max_changes=max_changes,
    )
    return result


def publish_from_environment(report_path: str) -> dict:
    from services.review_queue import publish_review_queue

    with open(report_path, encoding="utf-8") as handle:
        report = json.load(handle)
    repository = os.environ["GITHUB_REPOSITORY"]
    return publish_review_queue(
        report,
        GitHubReviewClient(repository, os.environ["GH_TOKEN"]),
        assignee=os.getenv("SOURCE_REVIEW_ASSIGNEE") or repository.split("/")[0],
        run_url=f"https://github.com/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
    )
