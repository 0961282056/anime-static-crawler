from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _job_section(workflow: str, job_name: str) -> str:
    match = re.search(rf"(?m)^  {re.escape(job_name)}:\s*$", workflow)
    assert match is not None, f"Missing {job_name!r} job"
    section_start = match.start()
    next_job = re.search(
        r"(?m)^  [a-zA-Z0-9_-]+:\s*$",
        workflow[match.end() :],
    )
    section_end = (
        match.end() + next_job.start() if next_job is not None else len(workflow)
    )
    return workflow[section_start:section_end]


def _assert_app_token_action_is_sha_pinned(workflow: str) -> None:
    references = [
        line.split("@", 1)[1].split("#", 1)[0].strip()
        for line in workflow.splitlines()
        if "uses: actions/create-github-app-token@" in line
    ]
    assert references, "A short-lived GitHub App token is required"
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in references), (
        "actions/create-github-app-token must be pinned to a full commit SHA"
    )


def _assert_only_allowlisted_paths_are_staged(
    workflow: str,
    expected_paths: set[str],
) -> None:
    staged_paths: set[str] = set()
    for line in workflow.splitlines():
        command = line.strip()
        if not command.startswith("git add "):
            continue
        tokens = shlex.split(command)
        assert "--" in tokens, "git add must use -- before its path allowlist"
        separator = tokens.index("--")
        paths = set(tokens[separator + 1 :])
        assert paths, "git add must name its allowed paths explicitly"
        assert paths <= expected_paths, (
            f"Unexpected staged paths: {paths - expected_paths}"
        )
        staged_paths.update(paths)

    assert staged_paths == expected_paths


def test_quality_gate_pins_and_verifies_workflow_linters() -> None:
    quality = _workflow("ci.yml")

    assert 'ACTIONLINT_VERSION: "1.7.12"' in quality
    assert (
        'ACTIONLINT_SHA256: "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"'
        in quality
    )
    assert 'SHELLCHECK_VERSION: "0.11.0"' in quality
    assert (
        'SHELLCHECK_SHA256: "8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198"'
        in quality
    )
    assert quality.count("sha256sum --check --strict") == 2
    assert '"$actionlint" -shellcheck "$shellcheck"' in quality
    assert '"$shellcheck" --severity=warning build.sh' in quality


@pytest.mark.parametrize(
    ("workflow_name", "allowed_paths"),
    [
        ("crawler.yml", {"dist/data", "cloudinary_cache.json"}),
        ("retention-execute.yml", {"cloudinary_cache.json"}),
    ],
)
def test_repository_mutation_uses_a_guarded_pull_request(
    workflow_name: str,
    allowed_paths: set[str],
) -> None:
    workflow = _workflow(workflow_name)

    assert not re.search(
        r"(?m)^\s*git\s+push\b[^\n]*(?:HEAD:)?(?:refs/heads/)?main(?:\s|$)",
        workflow,
    ), "Workflows must never push directly to main"
    _assert_app_token_action_is_sha_pinned(workflow)
    _assert_only_allowlisted_paths_are_staged(workflow, allowed_paths)
    assert "gh pr create" in workflow
    assert "--base main" in workflow
    assert "gh pr merge" in workflow
    assert "--auto" in workflow


def test_crawler_has_an_always_running_final_notification_job() -> None:
    crawler = _workflow("crawler.yml")
    notify_job = _job_section(crawler, "notify")

    assert "always()" in notify_job
    assert "crawl-and-prepare" in notify_job
    assert "publish-data-pr" in notify_job
    assert "DISCORD_WEBHOOK_URL" in notify_job
    assert "SENTRY_" not in crawler


def test_selector_canary_is_read_only_and_alerts_only_on_failure() -> None:
    workflow = _workflow("selector-canary.yml")
    canary_job = _job_section(workflow, "selector-canary")
    notify_job = _job_section(workflow, "notify-failure")

    assert 'cron: "15 9 * * *"' in workflow
    assert 'timezone: "Asia/Taipei"' in workflow
    assert "workflow_dispatch:" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "python manage.py selector-canary" in canary_job
    assert "requirements-canary.txt" in canary_job
    assert "requirements-build.txt" not in canary_job
    assert "environment: crawler-production" not in canary_job
    assert "DISCORD_WEBHOOK_URL" not in canary_job
    assert "always()" in notify_job
    assert "needs.selector-canary.result != 'success'" in notify_job
    assert "environment: crawler-production" in notify_job
    assert "DISCORD_WEBHOOK_URL" in notify_job
    assert "notify-selector-canary-failure" in notify_job
    assert "CLOUDINARY_" not in workflow
    assert "git add" not in workflow
    assert "git push" not in workflow


def test_crawler_path_allowlist_handles_unicode_json_names_safely() -> None:
    crawler = _workflow("crawler.yml")

    assert "git diff --cached --name-only -z" in crawler
    assert "read -r -d '' path" in crawler
    assert "dist/data/*.json" in crawler
    assert "anime-data-${{ github.run_id }}" in crawler


def test_retention_workflows_are_manual_and_execution_stops_during_crawling() -> None:
    plan = _workflow("retention-plan.yml")
    execute = _workflow("retention-execute.yml")

    for workflow in (plan, execute):
        assert "workflow_dispatch:" in workflow
        assert not re.search(r"(?m)^\s*schedule:\s*$", workflow)

    prepare_job = _job_section(execute, "prepare-cache-invalidation")
    delete_job = _job_section(execute, "delete-cloudinary")
    for job in (prepare_job, delete_job):
        assert "vars.CRAWLER_SCHEDULE_ENABLED" in job
        assert '!= "false"' in job

    assert "actions: read" in plan
    assert "GITHUB_RUN_ID" in plan
    assert "Dispatch a fresh workflow run" in plan
    assert "manifest_delay > 1800" in plan


def test_retention_merges_cache_safety_before_cloudinary_deletion() -> None:
    execute = _workflow("retention-execute.yml")
    prepare_job = _job_section(execute, "prepare-cache-invalidation")
    publisher_job = _job_section(execute, "publish-cache-pr")
    delete_job = _job_section(execute, "delete-cloudinary")

    assert "--prepare-execution" in prepare_job
    assert "--execute-prepared" not in prepare_job
    assert "CLOUDINARY_API_SECRET" in prepare_job
    assert "AUTOMATION_APP_PRIVATE_KEY" not in prepare_job

    assert "needs: prepare-cache-invalidation" in publisher_job
    assert "gh pr merge" in publisher_job
    assert "safe_main_sha" in publisher_job
    assert "CLOUDINARY_API_SECRET" not in publisher_job
    assert "AUTOMATION_APP_PRIVATE_KEY" in publisher_job

    assert "needs: [prepare-cache-invalidation, publish-cache-pr]" in delete_job
    assert "--execute-prepared" in delete_job
    assert "--prepare-execution" not in delete_job
    assert "CLOUDINARY_API_SECRET" in delete_job
    assert "AUTOMATION_APP_PRIVATE_KEY" not in delete_job
    assert "needs.publish-cache-pr.outputs.safe_main_sha" in delete_job

    assert execute.index("--prepare-execution") < execute.index("gh pr merge")
    assert execute.index("gh pr merge") < execute.index("--execute-prepared")
    assert "git push origin HEAD:main" not in execute
    assert "--execute \\" not in execute


@pytest.mark.parametrize(
    "workflow_name",
    ["crawler.yml", "retention-plan.yml", "retention-execute.yml"],
)
def test_workflow_artifacts_are_retained_for_at_least_seven_days(
    workflow_name: str,
) -> None:
    workflow = _workflow(workflow_name)
    retention_days = [
        int(value)
        for value in re.findall(r"(?m)^\s*retention-days:\s*(\d+)\s*$", workflow)
    ]

    assert retention_days, "Artifact retention must be explicit"
    assert min(retention_days) >= 7


def test_rerunnable_workflow_artifacts_use_stable_names_and_overwrite() -> None:
    assert "cloudinary-retention-plan-${{ github.run_id }}" in _workflow(
        "retention-plan.yml"
    )
    assert "retention-prepared-${{ github.run_id }}" in _workflow(
        "retention-execute.yml"
    )
    assert "anime-data-${{ github.run_id }}" in _workflow("crawler.yml")
    assert "overwrite: true" in _workflow("crawler.yml")
    assert "overwrite: true" in _workflow("retention-plan.yml")
    assert "overwrite: true" in _workflow("retention-execute.yml")


def test_pull_request_publishers_have_cleanup_time_after_merge_polling() -> None:
    crawler_publisher = _job_section(_workflow("crawler.yml"), "publish-data-pr")
    retention_publisher = _job_section(
        _workflow("retention-execute.yml"), "publish-cache-pr"
    )

    for publisher in (crawler_publisher, retention_publisher):
        assert "timeout-minutes: 45" in publisher
        assert "for _ in {1..120}" in publisher
        assert "sleep 15" in publisher
