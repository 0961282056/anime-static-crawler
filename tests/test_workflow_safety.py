from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def test_production_notification_waits_for_same_run_verification_without_crawler_secrets():
    workflow = _workflow("verify-production.yml")
    verify = _job_section(workflow, "verify")
    notify = _job_section(workflow, "notify")
    assert "environment:" not in verify
    assert "python manage.py production-report" in verify
    assert verify.index("python manage.py production-report") < verify.index(
        "Save verification evidence"
    )
    assert "needs: verify" in notify and "always()" in notify
    assert "environment: crawler-production" in notify
    assert "production-verification-${{ github.run_id }}" in notify
    assert "digest-mismatch: error" in notify
    assert "EXPECTED_COMMIT: ${{ github.sha }}" in notify
    assert "python manage.py notify-production" in notify
    assert "CLOUDINARY" not in workflow


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


def _quality_step_script(step_name: str) -> str:
    gate = _job_section(_workflow("ci.yml"), "quality")
    step = gate.split(f"- name: {step_name}\n", 1)[1].split("\n      - name:", 1)[0]
    return textwrap.dedent(step.split("run: |\n", 1)[1])


def test_required_quality_context_waits_for_both_platforms_without_skipping() -> None:
    workflow = _workflow("ci.yml")
    gate = _job_section(workflow, "quality")
    windows = _job_section(workflow, "windows-static")
    triggers = workflow.split("permissions:", 1)[0]
    assert "pull_request:" in triggers and "push:" in triggers
    assert "paths:" not in triggers and "paths-ignore:" not in triggers
    assert "needs: [linux-quality, windows-static]" in gate
    assert "if: always()" in gate
    assert "continue-on-error:" not in workflow
    assert "name: Windows static build" in windows
    assert "if:" not in windows
    assert "python -m pytest tests/test_static_build.py --no-cov" in windows
    assert "LINUX_RESULT: ${{ needs.linux-quality.result }}" in gate
    assert "WINDOWS_RESULT: ${{ needs.windows-static.result }}" in gate


@pytest.mark.parametrize(
    ("linux", "windows", "success"),
    [
        ("success", "success", True),
        ("failure", "success", False),
        ("success", "failure", False),
        ("cancelled", "success", False),
        ("success", "cancelled", False),
        ("skipped", "success", False),
        ("success", "skipped", False),
        ("", "success", False),
        ("success", "", False),
    ],
)
def test_required_quality_gate_rejects_unsuccessful_platforms(linux, windows, success):
    script = _quality_step_script("Require Linux and Windows success")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("The quality gate executes Bash on Ubuntu")
    result = subprocess.run(
        [bash, "-e", "-c", script],
        env={**os.environ, "LINUX_RESULT": linux, "WINDOWS_RESULT": windows},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is success, result.stdout + result.stderr


@pytest.mark.parametrize("receipt_commit", ["a" * 40, "b" * 40, None])
def test_required_quality_gate_verifies_extracted_artifact_revision(
    tmp_path, receipt_commit
):
    import json

    if receipt_commit is not None:
        receipt = tmp_path / "quality-receipt/nested/release.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({"commit": receipt_commit}), encoding="utf-8")
    script = _quality_step_script(
        "Verify extracted release receipt belongs to this revision"
    )
    code = script.split("python3 - <<'PYTHON'\n", 1)[1].rsplit("\nPYTHON", 1)[0]
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "RUNNER_TEMP": str(tmp_path), "GITHUB_SHA": "a" * 40},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is (receipt_commit == "a" * 40)


def test_artifact_actions_keep_archives_and_same_run_digest_protection() -> None:
    uploads = downloads = 0
    for path in WORKFLOWS.glob("*.yml"):
        workflow = path.read_text(encoding="utf-8")
        references = list(
            re.finditer(
                r"uses: actions/(upload-artifact|download-artifact)@([^\s]+) # v(\d+)\.\d+\.\d+",
                workflow,
            )
        )
        assert len(references) == len(
            re.findall(
                r"uses: actions/(?:upload-artifact|download-artifact)@", workflow
            )
        )
        for match in references:
            action, revision, major = match.groups()
            assert re.fullmatch(r"[a-f0-9]{40}", revision), path.name
            step = re.split(
                r"\n(?:      - |  [a-zA-Z])", workflow[match.end() :], maxsplit=1
            )[0]
            assert "name:" in step
            if action == "upload-artifact":
                uploads += 1
                assert int(major) >= 7
                assert "archive: true" in step
            else:
                downloads += 1
                assert int(major) >= 8
                assert "digest-mismatch: error" in step
                for cross_run_input in (
                    "github-token:",
                    "repository:",
                    "run-id:",
                    "pattern:",
                ):
                    assert cross_run_input not in step, path.name
    assert uploads and downloads


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


def test_source_review_notifications_keep_issue_writes_out_of_fetch_job() -> None:
    workflow = _workflow("source-audit.yml")
    fetch = _job_section(workflow, "source-audit")
    notify = _job_section(workflow, "notify-reviews")
    assert "issues: write" not in fetch
    assert "GH_TOKEN" not in fetch
    assert "issues: write" in notify
    assert "always()" in notify
    assert "github.ref == 'refs/heads/main'" in notify
    assert "source-audit-${{ github.run_id }}" in notify
    assert "notify-source-reviews" in notify
    assert "environment: crawler-production" not in workflow
    assert "CLOUDINARY_" not in workflow
    assert "contents: write" not in workflow


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


def test_daily_review_reminders_have_no_source_or_production_access():
    workflow = _workflow("review-reminders.yml")
    assert 'cron: "0 11 * * *"' in workflow
    assert 'timezone: "Asia/Taipei"' in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "issues: write" in workflow
    assert "remind-source-reviews" in workflow
    for forbidden in (
        "source-audit",
        "CLOUDINARY_",
        "crawler-production",
        "contents: write",
        "git push",
        "secrets.",
    ):
        assert forbidden not in workflow
    for source in (
        workflow,
        _job_section(_workflow("source-audit.yml"), "notify-reviews"),
    ):
        assert "group: anime-source-review-writes" in source
        assert "cancel-in-progress: false" in source


def test_crawler_preflight_build_and_validation_agree_across_processes(tmp_path):
    import os
    import shutil
    import subprocess
    import sys

    workflow = _workflow("crawler.yml")
    step = workflow.split("- name: Run pre-crawl quality gate", 1)[1].split(
        "\n      - name:", 1
    )[0]
    commands = [line.strip() for line in step.split("run: |", 1)[1].splitlines()]
    build = next(line for line in commands if "python generate_static.py" in line)
    validate = next(
        line for line in commands if "python manage.py validate-all" in line
    )
    output = tmp_path / "preflight"
    shutil.copytree(ROOT / "dist" / "data", output / "data")
    environment = os.environ.copy()
    environment.pop("BUILD_VERSION", None)
    environment.update(OUTPUT_DIR=str(output), BUILD_ONLY="false")
    for command in (build, validate):
        child_environment = environment.copy()
        tokens = shlex.split(command)
        while tokens and "=" in tokens[0]:
            key, value = tokens.pop(0).split("=", 1)
            child_environment[key] = value
        assert tokens.pop(0) == "python"
        if tokens[0] == "generate_static.py":
            # A regression must fail before it can start a real crawl.
            assert child_environment["BUILD_ONLY"] == "true"
        result = subprocess.run(
            [sys.executable, *tokens],
            cwd=ROOT,
            env=child_environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr


def test_only_the_crawler_step_explicitly_enables_crawling():
    workflow = _workflow("crawler.yml")
    preflight = workflow.split("- name: Run pre-crawl quality gate", 1)[1].split(
        "\n      - name:", 1
    )[0]
    crawl = workflow.split("- name: Crawl, validate, and build", 1)[1].split(
        "\n      - name:", 1
    )[0]
    assert "BUILD_ONLY=true python generate_static.py" in preflight
    assert "--crawl" not in preflight
    assert "run: python generate_static.py --crawl" in crawl
    assert workflow.count("--crawl") == 1
