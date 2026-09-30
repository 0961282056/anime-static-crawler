"""One bounded, read-only PR merge check: 0 merged, 1 failed, 2 waiting."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.diagnostics import publish_failure, redact  # noqa: E402

WAITING = 2


def gh_json(arguments: list[str]) -> object | None:
    try:
        result = subprocess.run(
            ["gh", "pr", *arguments],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        # gh pr checks returns 1 for failures and 8 for pending checks.
        if result.returncode not in (0, 1, 8):
            print(
                f"PR API 暫時無法查詢（gh exit={result.returncode}）；稍後再查，仍受等待期限限制。"
            )
            if result.stderr.strip():
                reason = (
                    redact(result.stderr).replace("\r", " ").replace("\n", " ")[:1000]
                )
                print(f"PR API 原因：{reason}")
            return None
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(f"PR API 查詢失敗（{type(exc).__name__}）；稍後再查，仍受等待期限限制。")
        return None


def inspect_pr(pr_url: str, expected_head: str) -> int:
    info = gh_json(["view", pr_url, "--json", "state,headRefOid"])
    if not isinstance(info, dict) or not info.get("headRefOid"):
        print("PR status temporarily unavailable; keeping the bounded wait.")
        return WAITING
    if info["headRefOid"] != expected_head:
        return publish_failure(
            "PR_HEAD_CHANGED",
            f"PR head changed after preparation; expected={expected_head}, actual={info['headRefOid']}, PR={pr_url}",
        )
    if info.get("state") == "MERGED":
        return 0
    if info.get("state") == "CLOSED":
        return publish_failure(
            "PR_CLOSED", f"The data pull request closed without merging: {pr_url}"
        )
    if info.get("state") != "OPEN":
        return WAITING

    checks = gh_json(["checks", pr_url, "--required", "--json", "name,bucket,link"])
    if not isinstance(checks, list) or not checks:
        print("Required checks are not available yet; keeping the bounded wait.")
        return WAITING
    failures = [
        check
        for check in checks
        if isinstance(check, dict) and check.get("bucket") in {"fail", "cancel"}
    ]
    if failures:
        # Verify the failure still belongs to the exact head we prepared.
        current = gh_json(["view", pr_url, "--json", "state,headRefOid"])
        if not isinstance(current, dict) or not current.get("headRefOid"):
            return WAITING
        if current["headRefOid"] != expected_head:
            return publish_failure(
                "PR_HEAD_CHANGED",
                f"PR head changed while checking required checks: {pr_url}",
            )
        if current.get("state") == "MERGED":
            return 0
        details = [f"PR={pr_url}"]
        for check in failures:
            details.append(
                redact(
                    f"Required check failed: {check.get('name', 'unknown')} ({check.get('bucket')}) {check.get('link', '')}"
                )
            )
        return publish_failure("PR_CHECK_FAILED", "\n".join(details))
    # Passing checks alone are not proof of a merge; GitHub still enforces rules.
    return WAITING


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pr_url")
    parser.add_argument("expected_head")
    parser.add_argument(
        "--timeout",
        action="store_true",
        help="Report that the bounded merge wait expired",
    )
    args = parser.parse_args()
    if args.timeout:
        return publish_failure(
            "PR_TIMEOUT",
            f"The data pull request did not merge before the safety timeout: {args.pr_url}; expected head={args.expected_head}",
        )
    return inspect_pr(args.pr_url, args.expected_head)


if __name__ == "__main__":
    raise SystemExit(main())
